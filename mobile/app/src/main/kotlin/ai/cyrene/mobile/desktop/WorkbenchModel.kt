package ai.cyrene.mobile.desktop

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import ai.cyrene.mobile.desktop.DesktopRuntimeClient
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

data class WorkbenchState(val phase: String = "idle", val error: String? = null, val proxy: WorkbenchProxy? = null)

class WorkbenchModel(application: Application) : AndroidViewModel(application) {
    private val client = DesktopRuntimeClient(application)
    private val mutable = MutableStateFlow(WorkbenchState())
    val state = mutable.asStateFlow()
    private var job: Job? = null

    fun start() {
        if (job?.isActive == true || mutable.value.phase == "ready") return
        mutable.value = WorkbenchState("starting")
        job = viewModelScope.launch {
            try {
                // Experimental TCG images need minutes on cold start. Keep progress visible.
                val connection = client.start(900_000)
                val proxy = withContext(Dispatchers.IO) { WorkbenchProxy(connection.url, connection.token) }
                mutable.value = WorkbenchState("ready", proxy = proxy)
            } catch (cancelled: CancellationException) { throw cancelled }
            catch (failure: Exception) { mutable.value = WorkbenchState("error", failure.message) }
        }
    }

    fun stop() {
        val previous = job
        mutable.value.proxy?.close()
        mutable.value = WorkbenchState("stopping")
        job = viewModelScope.launch {
            previous?.cancel()
            previous?.join()
            try { client.stop(); mutable.value = WorkbenchState("stopped") }
            catch (failure: Exception) { mutable.value = WorkbenchState("error", failure.message) }
        }
    }

    override fun onCleared() {
        mutable.value.proxy?.close()
        // The foreground Runtime retains running work until its Stop action is used.
        Thread { client.close() }.start()
        super.onCleared()
    }
}
