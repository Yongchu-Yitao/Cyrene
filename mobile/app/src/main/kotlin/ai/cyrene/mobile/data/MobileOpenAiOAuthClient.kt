package ai.cyrene.mobile.data

import kotlinx.coroutines.delay
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URI
import java.net.URLEncoder
import java.util.Base64

data class OpenAiDeviceCode(
    val verificationUrl: String,
    val userCode: String,
    internal val deviceAuthId: String,
    internal val intervalSeconds: Long,
)

/** OpenAI device-code OAuth owned entirely by this Android installation. */
class MobileOpenAiOAuthClient(private val store: SecureStore) {
    fun status(includeModels: Boolean = true): JSONObject {
        val credentials = store.openAiOAuthCredentials()
        val connected = credentials?.optString("refresh_token").orEmpty().isNotBlank()
        return JSONObject()
            .put("available", true)
            .put("connected", connected)
            .put("account", credentials?.optJSONObject("account") ?: JSONObject())
            .put("models", if (includeModels && connected) discoverModels() else JSONArray())
    }

    fun requestDeviceCode(): OpenAiDeviceCode {
        val result = requestJson(
            "POST",
            "$AUTH_BASE/api/accounts/deviceauth/usercode",
            JSONObject().put("client_id", CLIENT_ID).toString(),
        )
        return OpenAiDeviceCode(
            verificationUrl = "$AUTH_BASE/codex/device",
            userCode = result.getString("user_code"),
            deviceAuthId = result.getString("device_auth_id"),
            intervalSeconds = result.optString("interval", "5").toLongOrNull()?.coerceAtLeast(1) ?: 5,
        )
    }

    suspend fun completeDeviceCode(code: OpenAiDeviceCode): JSONObject {
        val started = System.currentTimeMillis()
        var authorization: JSONObject? = null
        while (System.currentTimeMillis() - started < 15 * 60_000L) {
            val result = requestJsonAllowPending(
                "$AUTH_BASE/api/accounts/deviceauth/token",
                JSONObject().put("device_auth_id", code.deviceAuthId).put("user_code", code.userCode).toString(),
            )
            if (result != null) {
                authorization = result
                break
            }
            delay(code.intervalSeconds * 1_000L)
        }
        val issued = authorization ?: error("OpenAI 登录已超时，请重新登录")
        val form = linkedMapOf(
            "grant_type" to "authorization_code",
            "code" to issued.getString("authorization_code"),
            "redirect_uri" to "$AUTH_BASE/deviceauth/callback",
            "client_id" to CLIENT_ID,
            "code_verifier" to issued.getString("code_verifier"),
        ).entries.joinToString("&") { (key, value) ->
            "${urlEncode(key)}=${urlEncode(value)}"
        }
        val tokens = requestJson("POST", "$AUTH_BASE/oauth/token", form, "application/x-www-form-urlencoded")
        persistTokens(tokens)
        return status()
    }

    fun validAccessToken(): JSONObject {
        val saved = store.openAiOAuthCredentials() ?: error("请先在手机上登录 OpenAI")
        val expiry = jwtPayload(saved.optString("access_token")).optLong("exp", 0) * 1_000L
        if (expiry == 0L || expiry > System.currentTimeMillis() + 60_000L) return saved
        val refreshed = requestJson(
            "POST",
            "$AUTH_BASE/oauth/token",
            JSONObject()
                .put("client_id", CLIENT_ID)
                .put("grant_type", "refresh_token")
                .put("refresh_token", saved.getString("refresh_token"))
                .toString(),
        )
        val merged = JSONObject(saved.toString())
        listOf("id_token", "access_token", "refresh_token").forEach { key ->
            refreshed.optString(key).takeIf(String::isNotBlank)?.let { merged.put(key, it) }
        }
        persistTokens(merged)
        return store.openAiOAuthCredentials() ?: error("无法保存 OpenAI 登录状态")
    }

    fun logout() = store.clearOpenAiOAuthCredentials()

    fun discoverModels(): JSONArray = runCatching {
        val credentials = validAccessToken()
        val response = requestJson(
            "GET",
            "$CODEX_BASE/models?client_version=0.2.5",
            headers = authHeaders(credentials),
        )
        response.optJSONArray("models") ?: response.optJSONArray("data") ?: JSONArray()
    }.getOrDefault(JSONArray())

    fun authHeaders(credentials: JSONObject = validAccessToken()): Map<String, String> = buildMap {
        put("Authorization", "Bearer ${credentials.getString("access_token")}")
        put("originator", "codex_cli_rs")
        put("User-Agent", "cyrene-mobile/0.2.5")
        credentials.optString("account_id").takeIf(String::isNotBlank)?.let {
            put("chatgpt-account-id", it)
        }
    }

    private fun persistTokens(tokens: JSONObject) {
        val idPayload = jwtPayload(tokens.optString("id_token"))
        val accessPayload = jwtPayload(tokens.optString("access_token"))
        val authClaims = idPayload.optJSONObject("https://api.openai.com/auth") ?: JSONObject()
        val accountId = authClaims.optString("chatgpt_account_id")
            .ifBlank { idPayload.optString("chatgpt_account_id") }
            .ifBlank { accessPayload.optString("chatgpt_account_id") }
        val account = JSONObject()
            .put("email", idPayload.optString("email"))
            .put("planType", authClaims.optString("chatgpt_plan_type"))
        store.saveOpenAiOAuthCredentials(
            JSONObject(tokens.toString())
                .put("account_id", accountId)
                .put("account", account)
                .put("saved_at", System.currentTimeMillis()),
        )
    }

    private fun jwtPayload(token: String): JSONObject = runCatching {
        val part = token.split('.')[1]
        JSONObject(String(Base64.getUrlDecoder().decode(part), Charsets.UTF_8))
    }.getOrDefault(JSONObject())

    private fun requestJsonAllowPending(url: String, body: String): JSONObject? {
        return try {
            requestJson("POST", url, body)
        } catch (error: HttpStatusException) {
            if (error.status == 403 || error.status == 404) null else throw error
        }
    }

    private fun requestJson(
        method: String,
        url: String,
        body: String? = null,
        contentType: String = "application/json",
        headers: Map<String, String> = emptyMap(),
    ): JSONObject {
        val uri = URI(url)
        require(uri.scheme == "https" && !uri.host.isNullOrBlank())
        val connection = uri.toURL().openConnection() as HttpURLConnection
        try {
            connection.requestMethod = method
            connection.connectTimeout = 15_000
            connection.readTimeout = 60_000
            connection.instanceFollowRedirects = false
            connection.setRequestProperty("Accept", "application/json")
            headers.forEach(connection::setRequestProperty)
            if (body != null) {
                connection.doOutput = true
                connection.setRequestProperty("Content-Type", contentType)
                val bytes = body.toByteArray(Charsets.UTF_8)
                connection.setFixedLengthStreamingMode(bytes.size)
                connection.outputStream.use { it.write(bytes) }
                bytes.fill(0)
            }
            val status = connection.responseCode
            val stream = if (status in 200..299) connection.inputStream else connection.errorStream
            val text = stream?.bufferedReader(Charsets.UTF_8)?.use { it.readText() }.orEmpty()
            if (status !in 200..299) {
                val message = runCatching {
                    val json = JSONObject(text)
                    json.optJSONObject("error")?.optString("message")
                        ?: json.optString("error_description")
                        ?: json.optString("message")
                }.getOrNull().orEmpty().ifBlank { "HTTP $status" }
                throw HttpStatusException(status, message.take(500))
            }
            return if (text.isBlank()) JSONObject() else JSONObject(text)
        } finally {
            connection.disconnect()
        }
    }

    private fun urlEncode(value: String): String = URLEncoder.encode(value, Charsets.UTF_8.name())

    private class HttpStatusException(val status: Int, message: String) : IllegalStateException(message)

    companion object {
        const val CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
        const val AUTH_BASE = "https://auth.openai.com"
        const val CODEX_BASE = "https://chatgpt.com/backend-api/codex"
    }
}
