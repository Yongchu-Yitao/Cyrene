package ai.cyrene.mobile.desktop

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import ai.cyrene.mobile.runtime.protocol.StartupProgress

data class WorkbenchState(val phase: String = "idle", val error: String? = null, val proxy: WorkbenchProxy? = null, val startup: StartupProgress = StartupProgress("connect"))

/** Activity models observe one process-owned session; closing a screen cannot cancel startup. */
class WorkbenchModel(application: Application) : AndroidViewModel(application) {
    private val session = getSession(application)
    val state = session.state
    fun start() = session.start()
    fun resume() = session.resume()
    fun stop() = session.stop()

    companion object {
        private var shared: WorkbenchSession? = null
        @Synchronized private fun getSession(application: Application): WorkbenchSession = shared ?: run {
            val client = DesktopRuntimeClient(application)
            WorkbenchSession(
                kotlinx.coroutines.CoroutineScope(kotlinx.coroutines.SupervisorJob() + Dispatchers.Main.immediate),
                connect = { report ->
                    val connection = client.start(900_000, report)
                    withContext(Dispatchers.IO) {
                        WorkbenchProxy(connection.url, connection.token, application.assets::open)
                    }
                },
                disconnect = { client.stop() },
                healthy = { client.ready() },
            ).also { shared = it }
        }
    }
}
