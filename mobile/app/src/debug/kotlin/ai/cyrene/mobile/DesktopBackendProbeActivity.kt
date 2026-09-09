package ai.cyrene.mobile

import android.app.Activity
import android.os.Bundle
import ai.cyrene.mobile.localagent.runtime.DesktopRuntimeClient
import kotlinx.coroutines.*
import org.json.JSONObject
import java.io.File
import java.net.Socket
import java.net.URI

/** Exercises main APK -> foreground service -> protected Binder -> Python health. */
class DesktopBackendProbeActivity : Activity() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        scope.launch {
            val client = DesktopRuntimeClient(this@DesktopBackendProbeActivity)
            var started = false
            val result = try {
                withContext(Dispatchers.IO) {
                    File(filesDir, "desktop-backend-probe.json").writeText(
                        JSONObject().put("stage", "starting_backend").put("completed", false).toString())
                }
                val timeoutMs = intent.getLongExtra("timeout_ms", 300_000).coerceIn(300_000, 900_000)
                val connection = client.start(timeoutMs)
                started = true
                check(client.ready()) { "Desktop backend is not healthy" }
                withContext(Dispatchers.IO) {
                    check(healthStatus(connection.url, null) == 401) { "Unauthenticated health request was not rejected" }
                    check(healthStatus(connection.url, connection.token) == 200) { "Authenticated health request failed" }
                }
                JSONObject().put("success", true).put("authenticated_backend", true)
                    .put("unauthenticated_rejected", true)
            } catch (failure: Throwable) {
                JSONObject().put("success", false).put("error", failure.message)
            } finally {
                withContext(NonCancellable) {
                    if (started) runCatching { client.stop() }
                    client.close()
                }
            }
            withContext(Dispatchers.IO) {
                File(filesDir, "desktop-backend-probe.json").writeText(result.put("completed", true).toString(2))
            }
            finish()
        }
    }

    private fun healthStatus(url: String, token: String?): Int {
        val endpoint = URI(url)
        return Socket(endpoint.host, endpoint.port).use { socket ->
            socket.soTimeout = 15000
            val request = "GET /api/health HTTP/1.0\r\nHost: 127.0.0.1\r\n" +
                (token?.let { "X-Cyrene-Token: $it\r\n" } ?: "") + "\r\n"
            socket.getOutputStream().write(request.toByteArray(Charsets.US_ASCII))
            socket.getOutputStream().flush()
            socket.getInputStream().bufferedReader().readLine().split(' ')[1].toInt()
        }
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }
}
