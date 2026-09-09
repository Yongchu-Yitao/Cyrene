package ai.cyrene.mobile

import org.json.JSONArray
import org.json.JSONObject

/** Folds the desktop Workbench run protocol into its visible timeline order. */
internal fun liveConversationTimeline(
    events: List<JSONObject>,
    running: Boolean,
): List<JSONObject> {
    val result = mutableListOf<JSONObject>()
    var activity: JSONObject? = null
    val replyDeltas = StringBuilder()
    var replyDone = ""
    var sequence = 0

    fun currentActivity(): JSONObject {
        activity?.let { return it }
        sequence += 1
        return JSONObject()
            .put("id", "live_activity_$sequence")
            .put("role", "assistant")
            .put("content", "")
            .put("activityCard", true)
            .put("reasoning", "")
            .put("trace", JSONArray())
            .also { activity = it }
    }

    fun flushActivity(force: Boolean = false, active: Boolean = false) {
        val item = activity ?: return
        val hasTrace = item.optJSONArray("trace")?.length()?.let { it > 0 } == true
        val hasReasoning = item.optString("reasoning").isNotBlank()
        if (hasTrace || hasReasoning || force) {
            item.put("runtimeActivityActive", active)
            result += item
        }
        activity = null
    }

    events.forEach { event ->
        when (event.optString("type")) {
            "reasoning_start" -> currentActivity().apply {
                put("reasoningActive", true)
                event.optString("phase").takeIf(String::isNotBlank)?.let { put("llmPhase", it) }
                event.optString("provider").takeIf(String::isNotBlank)?.let { put("provider", it) }
            }
            "reasoning_delta" -> {
                val item = currentActivity()
                item.put("reasoning", item.optString("reasoning") + event.optString("delta"))
                item.put("reasoningActive", true)
                event.optString("phase").takeIf(String::isNotBlank)?.let { item.put("llmPhase", it) }
                event.optString("provider").takeIf(String::isNotBlank)?.let { item.put("provider", it) }
            }
            "reasoning_done" -> {
                val item = currentActivity()
                val response = event.optString("response")
                if (response.isNotBlank()) item.put("reasoning", response)
                item.put("reasoningActive", false)
            }
            "tool_call_started", "tool_call_progress", "tool_call_finished",
            "auto_review",
            "permission_decision", "subagent_update" -> currentActivity().getJSONArray("trace")
                .put(JSONObject(event.toString()))
            "phase_transition" -> if (
                event.optString("detail").isNotBlank() ||
                event.optString("detail_key").isNotBlank()
            ) {
                currentActivity().getJSONArray("trace").put(JSONObject(event.toString()))
            } else Unit
            "intermediate_message" -> {
                flushActivity()
                event.optJSONObject("message")?.let { message ->
                    result += JSONObject(message.toString())
                        .put("role", "assistant")
                        .put("runtimeSegment", true)
                }
            }
            "reply_delta" -> replyDeltas.append(event.optString("delta"))
            "reply_done" -> replyDone = event.optString("response")
        }
    }
    if (running && activity == null && result.none { it.optBoolean("activityCard") }) {
        currentActivity()
    }
    flushActivity(
        force = running && result.none { it.optBoolean("activityCard") },
        active = running,
    )
    val reply = replyDeltas.toString().ifBlank { replyDone }
    if (reply.isNotBlank()) {
        result += JSONObject()
            .put("id", "live_reply")
            .put("role", "assistant")
            .put("content", reply)
            .put("liveReply", true)
    }
    return result
}

internal fun withoutDurableDuplicates(
    live: List<JSONObject>,
    durable: JSONArray?,
): List<JSONObject> {
    if (durable == null) return live
    val ids = mutableSetOf<String>()
    val intermediateContent = mutableSetOf<String>()
    for (index in 0 until durable.length()) {
        val item = durable.optJSONObject(index) ?: continue
        item.optString("id").takeIf(String::isNotBlank)?.let(ids::add)
        if (item.optBoolean("intermediate")) {
            item.optString("content").normalizeTimelineContent()
                .takeIf(String::isNotBlank)?.let(intermediateContent::add)
        }
    }
    return live.filterNot { item ->
        val sameId = item.optString("id").takeIf(String::isNotBlank)?.let(ids::contains) == true
        val sameIntermediate = item.optBoolean("runtimeSegment") &&
            item.optString("content").normalizeTimelineContent() in intermediateContent
        sameId || sameIntermediate
    }
}

internal fun isConversationActivityMessage(message: JSONObject): Boolean {
    if (message.optBoolean("activityCard")) return true
    if (message.optString("content").isNotBlank()) return false
    if (message.optString("reasoning").isNotBlank()) return true
    if ((message.optJSONArray("trace")?.length() ?: 0) > 0) return true
    val id = message.optString("id")
    return message.optBoolean("intermediate") &&
        (id.startsWith("reasoning_msg_") || id.startsWith("activity_msg_"))
}

private fun String.normalizeTimelineContent(): String = trim().replace(Regex("\\s+"), " ")
