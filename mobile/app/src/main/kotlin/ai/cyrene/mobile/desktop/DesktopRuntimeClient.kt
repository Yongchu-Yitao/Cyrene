package ai.cyrene.mobile.desktop

import android.content.Context
import android.content.ComponentName
import android.content.Intent
import ai.cyrene.mobile.runtime.protocol.GuestOperation
import ai.cyrene.mobile.runtime.protocol.StartupProgress
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/** Android lifecycle bridge to the shared Python backend. */
class DesktopRuntimeClient(private val context: Context) : AutoCloseable {
    private val companion = RuntimeCompanionClient(context)

    class Connection(val url: String, val token: String) {
        // Avoid data-class toString() leaking the bearer credential into logs.
        override fun toString() = "DesktopRuntimeConnection($url)"
        val headers: Map<String, String> get() = mapOf("X-Cyrene-Token" to token)
    }

    suspend fun start(timeoutMs: Long = 180_000, onProgress: (StartupProgress) -> Unit = {}): Connection {
        // Keep long first-run extraction protected once the visible app starts it.
        context.startForegroundService(serviceIntent().setAction("ai.cyrene.mobile.runtime.KEEP_DESKTOP"))
        try {
            val response = submit(GuestOperation.DESKTOP_START, timeoutMs, onProgress)
            check(response.status == "success") { response.message ?: "Desktop backend start failed" }
            return Connection(response.payload.getString("url"), response.payload.getString("token"))
        } catch (failure: Throwable) {
            context.stopService(serviceIntent())
            throw failure
        }
    }

    suspend fun ready(): Boolean {
        val response = submit(GuestOperation.DESKTOP_STATUS)
        check(response.status == "success") { response.message ?: "Desktop backend status failed" }
        return response.payload.getBoolean("ready")
    }

    suspend fun stop() {
        val response = submit(GuestOperation.DESKTOP_STOP)
        check(response.status == "success") { response.message ?: "Desktop backend stop failed" }
        context.stopService(serviceIntent())
    }

    private fun serviceIntent() = Intent().setComponent(ComponentName(
        context.packageName, RuntimeCompanionClient.RUNTIME_SERVICE_CLASS))

    // Companion cancellation can synchronously stop the VM through Binder.
    // Keep that bounded but potentially slow operation off Android's UI thread.
    private suspend fun submit(operation: GuestOperation, timeoutMs: Long = 30_000, onProgress: (StartupProgress) -> Unit = {}) = withContext(Dispatchers.IO) {
        companion.submit("ls_desktop_backend", operation, timeoutMs = timeoutMs, onProgress = onProgress)
    }

    override fun close() = companion.close()
}
