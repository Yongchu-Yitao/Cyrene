package ai.cyrene.mobile.desktop

import android.content.*
import android.os.IBinder
import ai.cyrene.mobile.R
import ai.cyrene.mobile.runtime.protocol.*
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import org.json.JSONObject
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap

class RuntimeCompanionClient(context: Context) : AutoCloseable {
    private val context = context.applicationContext
    private val progressListeners = ConcurrentHashMap<String, (StartupProgress) -> Unit>()
    private val pending = ConcurrentHashMap<String, CompletableDeferred<GuestResponse>>()
    @Volatile private var service: IRuntimeService? = null
    private var bound = false

    private val callback = object : IRuntimeCallback.Stub() {
        override fun onProgress(requestId: String, progressJson: String) {
            runCatching { StartupProgress.parse(progressJson) }.onSuccess { progressListeners[requestId]?.invoke(it) }
        }
        override fun onResult(responseJson: String) {
            runCatching { GuestResponse.parse(responseJson) }
                .onSuccess { response -> pending.remove(response.requestId)?.complete(response) }
                .onFailure { error -> pending.values.forEach { it.completeExceptionally(error) }; pending.clear() }
        }
    }

    private val connection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName?, binder: IBinder?) {
            service = IRuntimeService.Stub.asInterface(binder)
        }

        override fun onServiceDisconnected(name: ComponentName?) {
            service = null
            pending.values.forEach { it.completeExceptionally(IllegalStateException("Runtime companion disconnected")) }
            pending.clear()
        }
    }

    suspend fun bind(timeoutMs: Long = 5_000) = withContext(Dispatchers.Main) {
        if (!bound) {
            val intent = Intent(ACTION_BIND)
                .setComponent(ComponentName(context.packageName, RUNTIME_SERVICE_CLASS))
                .addFlags(Intent.FLAG_INCLUDE_STOPPED_PACKAGES)
            bound = bindService(intent)
            check(bound) { context.getString(R.string.workbench_runtime_service_unavailable) }

        }
        withTimeout(timeoutMs) {
            while (service == null) kotlinx.coroutines.delay(20)
        }
    }

    private fun bindService(intent: Intent): Boolean = try {
        context.bindService(intent, connection, Context.BIND_AUTO_CREATE).also { bound = it }
    } catch (_: SecurityException) {
        throw IllegalStateException(context.getString(R.string.workbench_runtime_signature_mismatch))
    }

    suspend fun submit(
        sessionId: String,
        operation: GuestOperation,
        payload: JSONObject = JSONObject(),
        subagentId: String? = null,
        timeoutMs: Long = 30_000,
        onProgress: (StartupProgress) -> Unit = {},
    ): GuestResponse {
        bind()
        val requestId = "gr_${UUID.randomUUID().toString().replace("-", "")}"
        val request = JSONObject()
            .put("protocol", GUEST_PROTOCOL_VERSION)
            .put("request_id", requestId)
            .put("session_id", sessionId)
            .put("subagent_id", subagentId ?: "")
            .put("operation", operation.wireName)
            .put("deadline_epoch_ms", System.currentTimeMillis() + timeoutMs)
            .put("sequence", System.nanoTime())
            .put("payload", payload)
        val deferred = CompletableDeferred<GuestResponse>()
        pending[requestId] = deferred
        progressListeners[requestId] = onProgress
        try {
            service?.submit(request.toString(), callback) ?: error("Runtime companion unavailable")
            return withTimeout(timeoutMs) { deferred.await() }
        } catch (error: Throwable) {
            // A timed-out read-only status request must never tear down an active VM.
            if (operation != GuestOperation.DESKTOP_STATUS) service?.cancel(requestId)
            throw error
        } finally {
            pending.remove(requestId)
            progressListeners.remove(requestId)
        }
    }

    override fun close() {
        if (bound) context.unbindService(connection)
        bound = false
        service = null
    }

    companion object {
        const val ACTION_BIND = "ai.cyrene.mobile.runtime.BIND"
        const val RUNTIME_SERVICE_CLASS = "ai.cyrene.mobile.runtime.CyreneRuntimeService"
    }
}
