package ai.cyrene.mobile.localagent.runtime

import ai.cyrene.mobile.data.SecureStore
import ai.cyrene.mobile.data.MobileOpenAiOAuthClient
import ai.cyrene.mobile.data.resolvedModelCandidates
import ai.cyrene.mobile.localagent.model.MODEL_EVENT_SCHEMA
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URI
import java.net.URL
import java.util.concurrent.ConcurrentHashMap

internal class ProviderTranscript(initial: JSONArray) {
    private val items = mutableListOf<JSONObject>()
    private val observedToolResults = mutableSetOf<String>()

    init { appendAll(initial) }

    @Synchronized
    fun append(value: JSONObject) { items += JSONObject(value.toString()) }

    @Synchronized
    fun appendAll(values: JSONArray) {
        for (index in 0 until values.length()) {
            values.optJSONObject(index)?.let(::append)
        }
    }

    @Synchronized
    fun appendChatToolResults(results: JSONArray) {
        for (index in 0 until results.length()) {
            val result = results.optJSONObject(index) ?: continue
            val callId = result.optString("call_id")
            if (callId.isBlank() || !observedToolResults.add(callId)) continue
            append(JSONObject()
                .put("role", "tool")
                .put("tool_call_id", callId)
                .put("content", structuredToolResult(result)))
        }
    }

    @Synchronized
    fun appendResponsesToolResults(results: JSONArray) {
        for (index in 0 until results.length()) {
            val result = results.optJSONObject(index) ?: continue
            val callId = result.optString("call_id")
            if (callId.isBlank() || !observedToolResults.add(callId)) continue
            append(JSONObject()
                .put("type", "function_call_output")
                .put("call_id", callId)
                .put("output", structuredToolResult(result)))
        }
    }

    @Synchronized
    fun snapshot(): JSONArray = JSONArray(items.map { JSONObject(it.toString()) })

    companion object {
        internal fun structuredToolResult(result: JSONObject): String {
            fun nullableString(key: String): String? =
                if (!result.has(key) || result.isNull(key)) null
                else result.optString(key).takeIf(String::isNotBlank)
            return JSONObject()
                .put("status", result.optString("status", "unknown"))
                .put("summary", result.optString("summary"))
                .apply {
                    nullableString("error_type")?.let { put("error_type", it) }
                    nullableString("inline_payload")?.let { payload ->
                        put("payload", runCatching { JSONObject(payload) }.getOrElse { payload })
                    }
                    nullableString("artifact_ref")?.let { put("artifact_ref", it) }
                }
                .toString()
                .take(128_000)
        }
    }
}

/** Direct, on-device OpenAI-compatible Provider transport for local sessions. */
class MobileProviderClient(private val store: SecureStore) : LocalModelTransport {
    private val openAiOAuth = MobileOpenAiOAuthClient(store)
    private val chatTranscripts = ConcurrentHashMap<String, ProviderTranscript>()
    private val responsesTranscripts = ConcurrentHashMap<String, ProviderTranscript>()

    override suspend fun start(payload: JSONObject, idempotencyKey: String): JSONObject = complete(payload)

    override suspend fun continueTurn(payload: JSONObject, idempotencyKey: String): JSONObject = complete(payload)

    override suspend fun wait(modelTurnId: String, cursor: Long): JSONObject =
        throw IllegalStateException("Mobile Provider turns complete inline")

    private suspend fun complete(payload: JSONObject): JSONObject = withContext(Dispatchers.IO) {
        val turnId = payload.getString("model_turn_id")
        val phase = payload.getString("phase")
        val configuration = store.localModelConfiguration()
            ?: throw IllegalStateException("请先在设置中配置模型")
        val candidate = primaryCandidate(configuration)
        if (candidate.optString("adapter") == "codex_oauth") {
            return@withContext completeCodex(payload, candidate)
        }
        val transcriptKey = transcriptKey(payload, phase)
        val transcript = chatTranscripts.computeIfAbsent(transcriptKey) {
            ProviderTranscript(chatInput(payload, phase))
        }
        transcript.appendChatToolResults(payload.optJSONArray("tool_results") ?: JSONArray())
        val request = JSONObject()
            .put("model", candidate.getString("model"))
            .put("messages", transcript.snapshot())
            .put("tools", if (phase == "decision") decisionTools() else executionTools())
            .put("max_tokens", payload.getJSONObject("limits").optInt("max_output_tokens", 8192).coerceIn(1, 32768))
            .put("stream", false)
        val response = post(candidate, request)
        val choice = response.optJSONArray("choices")?.optJSONObject(0)
            ?: throw IllegalStateException(response.optJSONObject("error")?.optString("message")
                ?: "模型服务没有返回 choices")
        val assistant = choice.optJSONObject("message") ?: JSONObject()
        transcript.append(JSONObject(assistant.toString()))
        val events = JSONArray()
        var sequence = 1
        fun event(type: String, body: JSONObject = JSONObject()) {
            events.put(JSONObject().put("schema", MODEL_EVENT_SCHEMA).put("model_turn_id", turnId)
                .put("sequence", sequence++).put("type", type).put("payload", body))
        }
        event("turn.started", JSONObject().put("phase", phase))
        assistant.optString("reasoning_content").ifBlank {
            assistant.optString("reasoning")
        }.takeIf(String::isNotBlank)?.let { reasoning ->
            event("reasoning.status", JSONObject().put("content", reasoning))
        }
        val content = assistant.optString("content")
        if (content.isNotBlank()) {
            event("message.delta", JSONObject().put("delta", content))
            event("message.completed", JSONObject().put("content", content))
        }
        val calls = assistant.optJSONArray("tool_calls") ?: JSONArray()
        for (index in 0 until calls.length()) {
            val call = calls.optJSONObject(index) ?: continue
            val function = call.optJSONObject("function") ?: continue
            val callId = call.optString("id").ifBlank { "call_${turnId}_$index" }
            val name = function.optString("name")
            val arguments = function.opt("arguments")?.toString().orEmpty().ifBlank { "{}" }
            event("tool_call.started", JSONObject().put("call_id", callId).put("name", name))
            event("tool_call.completed", JSONObject().put("call_id", callId).put("name", name)
                .put("arguments_json", arguments))
        }
        response.optJSONObject("usage")?.let { usage ->
            event("usage.updated", JSONObject().apply {
                listOf("prompt_tokens", "completion_tokens", "total_tokens").forEach { key ->
                    if (usage.has(key)) put(key, usage.get(key))
                }
            })
        }
        event("turn.completed", JSONObject().put("finish_reason", choice.optString("finish_reason", "stop")))
        JSONObject().put("model_turn_id", turnId).put("state", "COMPLETED").put("events", events)
    }

    private fun primaryCandidate(models: JSONObject): JSONObject {
        val candidates = resolvedModelCandidates(models, "primary")
        val candidate = candidates.firstOrNull { item ->
            when (item.optString("adapter")) {
                "codex_oauth" -> store.openAiOAuthCredentials()
                    ?.optString("refresh_token").orEmpty().isNotBlank()
                "openai", "openai_compatible" -> item.optString("api_key").isNotBlank()
                else -> false
            }
        } ?: candidates.firstOrNull()
            ?: throw IllegalStateException("主要模型路由中没有可用的模型配置")
        require(candidate.optString("adapter") in setOf("openai", "openai_compatible", "codex_oauth")) {
            "移动端暂不支持 ${candidate.optString("adapter")} 模型适配器"
        }
        require(candidate.optString("model").isNotBlank()) { "模型标识为空" }
        if (candidate.optString("adapter") == "codex_oauth") {
            require(
                store.openAiOAuthCredentials()?.optString("refresh_token").orEmpty().isNotBlank(),
            ) { "请先在移动端登录 OpenAI" }
            return candidate
        }
        require(candidate.optString("api_key").isNotBlank()) { "请在移动端模型设置中重新输入 API Key" }
        val uri = URI(candidate.optString("base_url"))
        require(uri.scheme in setOf("http", "https") && !uri.host.isNullOrBlank() && uri.userInfo == null) {
            "模型 Base URL 无效"
        }
        return candidate
    }

    private fun completeCodex(payload: JSONObject, candidate: JSONObject): JSONObject {
        val turnId = payload.getString("model_turn_id")
        val phase = payload.getString("phase")
        val model = candidate.optString("model")
        val transcriptKey = transcriptKey(payload, phase)
        val transcript = responsesTranscripts.computeIfAbsent(transcriptKey) {
            ProviderTranscript(responsesInput(payload))
        }
        transcript.appendResponsesToolResults(payload.optJSONArray("tool_results") ?: JSONArray())
        val request = JSONObject()
            .put("model", model)
            .put("instructions", systemPrompt(phase))
            .put("input", transcript.snapshot())
            .put("tools", responsesTools(if (phase == "decision") decisionTools() else executionTools()))
            .put("tool_choice", "auto")
            .put("parallel_tool_calls", false)
            .put("store", false)
            .put("stream", false)
        candidate.optString("reasoning_effort").takeIf(String::isNotBlank)?.let {
            request.put("reasoning", JSONObject().put("effort", it).put("summary", "auto"))
        }
        val response = postCodex(request)
        val events = JSONArray()
        var sequence = 1
        fun event(type: String, body: JSONObject = JSONObject()) {
            events.put(JSONObject().put("schema", MODEL_EVENT_SCHEMA).put("model_turn_id", turnId)
                .put("sequence", sequence++).put("type", type).put("payload", body))
        }
        event("turn.started", JSONObject().put("phase", phase))
        val output = response.optJSONArray("output") ?: JSONArray()
        transcript.appendAll(output)
        val text = StringBuilder()
        for (index in 0 until output.length()) {
            val item = output.optJSONObject(index) ?: continue
            when (item.optString("type")) {
                "message" -> {
                    val content = item.optJSONArray("content") ?: JSONArray()
                    for (partIndex in 0 until content.length()) {
                        val part = content.optJSONObject(partIndex) ?: continue
                        if (part.optString("type") == "output_text") text.append(part.optString("text"))
                    }
                }
                "function_call" -> {
                    val callId = item.optString("call_id").ifBlank { item.optString("id") }
                    event("tool_call.started", JSONObject().put("call_id", callId).put("name", item.optString("name")))
                    event("tool_call.completed", JSONObject().put("call_id", callId)
                        .put("name", item.optString("name")).put("arguments_json", item.optString("arguments", "{}")))
                }
                "reasoning" -> {
                    val summary = item.optJSONArray("summary") ?: JSONArray()
                    val reasoning = (0 until summary.length()).joinToString("\n") {
                        summary.optJSONObject(it)?.optString("text").orEmpty()
                    }.trim()
                    if (reasoning.isNotBlank()) event("reasoning.status", JSONObject().put("content", reasoning))
                }
            }
        }
        if (text.isNotEmpty()) {
            event("message.delta", JSONObject().put("delta", text.toString()))
            event("message.completed", JSONObject().put("content", text.toString()))
        }
        response.optJSONObject("usage")?.let { event("usage.updated", it) }
        event("turn.completed", JSONObject().put("finish_reason", response.optString("status", "completed")))
        return JSONObject().put("model_turn_id", turnId).put("state", "COMPLETED").put("events", events)
    }

    private fun responsesTools(chatTools: JSONArray): JSONArray = JSONArray().also { result ->
        for (index in 0 until chatTools.length()) {
            val function = chatTools.optJSONObject(index)?.optJSONObject("function") ?: continue
            result.put(JSONObject()
                .put("type", "function")
                .put("name", function.optString("name"))
                .put("description", function.optString("description"))
                .put("parameters", function.optJSONObject("parameters") ?: JSONObject()))
        }
    }

    private fun postCodex(body: JSONObject): JSONObject {
        val connection = URL("${MobileOpenAiOAuthClient.CODEX_BASE}/responses").openConnection() as HttpURLConnection
        try {
            connection.requestMethod = "POST"
            connection.connectTimeout = 15_000
            connection.readTimeout = 180_000
            connection.doOutput = true
            connection.instanceFollowRedirects = false
            connection.setRequestProperty("Content-Type", "application/json")
            connection.setRequestProperty("Accept", "application/json")
            openAiOAuth.authHeaders().forEach(connection::setRequestProperty)
            val bytes = body.toString().toByteArray(Charsets.UTF_8)
            connection.setFixedLengthStreamingMode(bytes.size)
            connection.outputStream.use { it.write(bytes) }
            val status = connection.responseCode
            val stream = if (status in 200..299) connection.inputStream else connection.errorStream
            val text = stream?.bufferedReader(Charsets.UTF_8)?.use { it.readText() }.orEmpty()
            if (status !in 200..299) {
                val detail = runCatching { JSONObject(text).optJSONObject("error")?.optString("message") }
                    .getOrNull().orEmpty().ifBlank { "HTTP $status" }
                throw IllegalStateException("OpenAI 请求失败：${detail.take(500)}")
            }
            return JSONObject(text)
        } finally {
            connection.disconnect()
        }
    }

    private fun chatInput(payload: JSONObject, phase: String): JSONArray {
        val result = JSONArray().put(JSONObject().put("role", "system").put("content", systemPrompt(phase)))
        val input = payload.optJSONArray("input_items") ?: JSONArray()
        for (index in 0 until input.length()) {
            val item = input.optJSONObject(index) ?: continue
            val role = item.optString("role", "user").takeIf { it in setOf("user", "assistant", "tool") } ?: "user"
            result.put(JSONObject().put("role", role).put("content", item.optString("content").take(500_000)))
        }
        return result
    }

    private fun responsesInput(payload: JSONObject): JSONArray = JSONArray().also { input ->
        val sourceItems = payload.optJSONArray("input_items") ?: JSONArray()
        for (index in 0 until sourceItems.length()) {
            val source = sourceItems.optJSONObject(index) ?: continue
            input.put(JSONObject()
                .put("role", source.optString("role", "user"))
                .put("content", source.optString("content").take(500_000)))
        }
    }

    private fun transcriptKey(payload: JSONObject, phase: String) =
        "${payload.optString("run_id")}:$phase"

    private fun post(candidate: JSONObject, body: JSONObject): JSONObject {
        val base = candidate.getString("base_url").trimEnd('/')
        val endpoint = if (base.endsWith("/chat/completions")) base else "$base/chat/completions"
        val connection = URL(endpoint).openConnection() as HttpURLConnection
        try {
            connection.requestMethod = "POST"
            connection.connectTimeout = 15_000
            connection.readTimeout = 120_000
            connection.instanceFollowRedirects = false
            connection.doOutput = true
            connection.setRequestProperty("Content-Type", "application/json")
            connection.setRequestProperty("Accept", "application/json")
            connection.setRequestProperty("Authorization", "Bearer ${candidate.getString("api_key")}")
            val bytes = body.toString().toByteArray(Charsets.UTF_8)
            connection.setFixedLengthStreamingMode(bytes.size)
            connection.outputStream.use { it.write(bytes) }
            val status = connection.responseCode
            val stream = if (status in 200..299) connection.inputStream else connection.errorStream
            val text = stream?.bufferedReader(Charsets.UTF_8)?.use { it.readText() }.orEmpty()
            if (status !in 200..299) {
                val detail = runCatching { JSONObject(text).optJSONObject("error")?.optString("message") }
                    .getOrNull().orEmpty().ifBlank { "HTTP $status" }
                throw IllegalStateException("模型请求失败：${detail.take(500)}")
            }
            return JSONObject(text)
        } finally {
            connection.disconnect()
        }
    }

    private fun systemPrompt(phase: String): String = if (phase == "decision") {
        """
        你是 Cyrene 本地 Agent。模型推理在 Android 设备上进行；文件和命令工具在同一设备内真实的 QEMU Alpine Linux 虚拟机中执行。

        能力事实：
        - Linux 虚拟机拥有持久化、跨本地会话共享的 /workspace。
        - 虚拟机通过 QEMU NAT 使用 Android 当前网络，正常情况下可解析 DNS、访问 HTTP/HTTPS，并可通过 apk 安装 Alpine 软件包。
        - 基础镜像可能尚未安装 Python、pip、curl 等命令；“command not found”只表示软件缺失，不表示没有网络，可进入工具阶段用 apk 安装。

        直接回答确实不需要工具的问题。凡是涉及设备文件、系统状态、命令是否存在、网络是否可用、安装软件或需要生成/修改文件，都必须调用 use_tools 实测和执行；信息不足时调用 ask_user，经过实测仍明确无法继续时调用 quit。不要把环境假设当成事实，也不要声称已经执行尚未执行的操作。
        """.trimIndent()
    } else {
        """
        你是 Cyrene 本地 Agent，正在一台真实的 QEMU Alpine Linux 虚拟机中使用工具完成用户任务。

        运行环境：
        - Bash 默认工作目录是 /workspace。所有本地会话共享并持久化这一个工作区和同一份 Linux 根文件系统；安装的软件也会保留并可被其他本地会话使用。
        - Read、Write、Edit、Glob、Grep、PublishFile 的路径必须相对于 /workspace，禁止使用绝对路径或 .. 越界。
        - 虚拟机已配置 QEMU NAT 网络，可使用 DNS、HTTP/HTTPS 和 Alpine apk 仓库；实际连通性仍可能随 Android 当前网络临时变化。
        - 这是精简 Alpine 镜像。缺少某个命令或运行时不代表能力不存在，也不代表断网；优先执行 apk update，并用 apk add 安装所需软件。不要使用 apt、apt-get 或 dpkg。

        网络诊断规则：
        - 不得仅根据 ping 失败、单个网址失败、单次 wget/curl 失败或某个命令不存在，就断言“没有网络”。ICMP、特定协议或站点可能被单独限制。
        - 宣布网络不可用前，至少分别检查默认路由、DNS 解析和 HTTPS/软件仓库，例如 ip route、nslookup dl-cdn.alpinelinux.org、apk update，并准确报告每一步的原始错误。
        - 若 DNS 或网络在切换后暂时失败，可以短暂重试；仍失败时说明是路由、DNS、TLS、仓库还是具体站点失败，不要笼统归因。

        持续使用工具直到任务完成或出现有证据支持的阻塞。产生用户可能需要下载到虚拟机之外的最终文件时，必须对每个最终文件调用 PublishFile；不要发布临时文件、中间产物或目录。根据真实工具结果回答，不伪造执行、文件、软件安装或网络状态。最终回答简洁说明完成内容；失败时给出具体失败命令、错误和可行的下一步。
        """.trimIndent()
    }

    private fun function(name: String, description: String, properties: JSONObject, required: List<String> = emptyList()): JSONObject =
        JSONObject().put("type", "function").put("function", JSONObject().put("name", name)
            .put("description", description).put("parameters", JSONObject().put("type", "object")
                .put("properties", properties).put("required", JSONArray(required)).put("additionalProperties", false)))

    private fun stringProperty(description: String) = JSONObject().put("type", "string").put("description", description)

    private fun decisionTools() = JSONArray()
        .put(function("use_tools", "进入本地工具执行阶段", JSONObject().put("task", stringProperty("保持用户原始任务语义")), listOf("task")))
        .put(function("ask_user", "请求用户补充必要信息", JSONObject().put("question", stringProperty("问题")), listOf("question")))
        .put(function("quit", "无法继续并说明原因", JSONObject().put("answer", stringProperty("说明")), listOf("answer")))

    private fun executionTools() = JSONArray()
        .put(function("Read", "读取工作区文件", JSONObject().put("path", stringProperty("相对路径")), listOf("path")))
        .put(function("Write", "写入工作区文件", JSONObject().put("path", stringProperty("相对路径")).put("content", stringProperty("完整内容")), listOf("path", "content")))
        .put(function("Edit", "替换工作区文件中的一段文本", JSONObject().put("path", stringProperty("相对路径")).put("old_string", stringProperty("原文本")).put("new_string", stringProperty("新文本")), listOf("path", "old_string", "new_string")))
        .put(function("Glob", "按 glob 查找工作区文件", JSONObject().put("pattern", stringProperty("例如 **/*.kt")), listOf("pattern")))
        .put(function("Grep", "搜索工作区文本", JSONObject().put("pattern", stringProperty("正则表达式")).put("path", stringProperty("相对目录，默认 .")), listOf("pattern")))
        .put(function("Bash", "在隔离工作区执行 shell 命令", JSONObject().put("command", stringProperty("命令")), listOf("command")))
        .put(function("PublishFile", "将已完成的工作区文件发布到本地对话的可下载文件列表", JSONObject().put("path", stringProperty("最终文件的工作区相对路径")), listOf("path")))
}
