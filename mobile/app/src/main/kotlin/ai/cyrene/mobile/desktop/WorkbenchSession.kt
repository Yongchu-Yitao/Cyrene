package ai.cyrene.mobile.desktop

import ai.cyrene.mobile.runtime.protocol.StartupProgress
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/** Main-thread owner of one connection and one in-flight operation, independent of any screen. */
internal class WorkbenchSession(
    private val scope: CoroutineScope,
    private val connect: suspend ((StartupProgress) -> Unit) -> WorkbenchProxy,
    private val disconnect: suspend () -> Unit,
    private val healthy: suspend () -> Boolean,
) {
    private val mutable = MutableStateFlow(WorkbenchState())
    val state = mutable.asStateFlow()
    private var job: Job? = null

    fun start() {
        if (job?.isActive == true || mutable.value.phase == "ready") return
        job = scope.launch { open() }
    }

    private suspend fun open() {
        mutable.value = WorkbenchState("starting")
        try {
            val proxy = connect { progress ->
                mutable.update { if (it.phase == "starting") it.copy(startup = progress) else it }
            }
            mutable.value = WorkbenchState("ready", proxy = proxy)
        } catch (cancelled: CancellationException) { throw cancelled }
        catch (failure: Exception) { mutable.value = WorkbenchState("error", failure.message) }
    }

    fun resume() {
        if (mutable.value.phase != "ready" || job?.isActive == true) return
        job = scope.launch {
            suspend fun checkHealth(): Boolean = try { healthy() }
            catch (cancelled: CancellationException) { throw cancelled }
            catch (_: Exception) { false }
            // A busy guest can miss one health deadline. Keep the current page
            // and proxy until a second authenticated check confirms the loss.
            val available = checkHealth() || checkHealth()
            if (!available) {
                mutable.value.proxy?.close()
                open()
            }
        }
    }

    fun stop() {
        val previous = job
        mutable.value.proxy?.close()
        mutable.value = WorkbenchState("stopping")
        job = scope.launch {
            previous?.cancel(); previous?.join()
            try { disconnect(); mutable.value = WorkbenchState("stopped") }
            catch (cancelled: CancellationException) { throw cancelled }
            catch (failure: Exception) { mutable.value = WorkbenchState("error", failure.message) }
        }
    }
}
