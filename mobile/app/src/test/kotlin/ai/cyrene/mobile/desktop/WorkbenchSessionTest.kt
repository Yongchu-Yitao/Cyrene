package ai.cyrene.mobile.desktop

import kotlinx.coroutines.*
import org.junit.Assert.*
import org.junit.Test

class WorkbenchSessionTest {
    private fun proxy() = WorkbenchProxy("http://127.0.0.1:4242", "a".repeat(43))

    @Test fun transientHealthFailurePreservesPageAndProxy() = runBlocking {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        val secondCheck = CompletableDeferred<Boolean>()
        var checks = 0
        var starts = 0
        val gateway = proxy()
        val session = WorkbenchSession(scope, { starts++; gateway }, {}, {
            if (++checks == 1) false else secondCheck.await()
        })
        try {
            session.start(); session.resume(); session.resume()
            assertEquals(2, checks)
            assertEquals("ready", session.state.value.phase)
            assertSame(gateway, session.state.value.proxy)
            secondCheck.complete(true)
            assertEquals(1, starts)
            assertSame(gateway, session.state.value.proxy)
        } finally { scope.cancel(); gateway.close() }
    }

    @Test fun repeatedEntrySharesStartupAndHealthyConnection() = runBlocking {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        val gate = CompletableDeferred<Unit>()
        var starts = 0
        val gateway = proxy()
        val session = WorkbenchSession(scope, { starts++; gate.await(); gateway }, {}, { true })
        try {
            session.start(); session.start(); session.resume()
            assertEquals(1, starts)
            gate.complete(Unit)
            assertEquals("ready", session.state.value.phase)
            session.start(); session.resume(); session.resume()
            assertEquals(1, starts)
            assertSame(gateway, session.state.value.proxy)
        } finally { scope.cancel(); gateway.close() }
    }

    @Test fun deadBackendReconnectsOnceWhileConcurrentResumeWaits() = runBlocking {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        val status = CompletableDeferred<Boolean>()
        val gateways = mutableListOf<WorkbenchProxy>()
        val session = WorkbenchSession(scope, { proxy().also { gateways.add(it) } }, {}, { status.await() })
        try {
            session.start()
            val firstOrigin = session.state.value.proxy!!.origin
            session.resume(); session.resume(); session.start()
            assertEquals(1, gateways.size)
            status.complete(false)
            assertEquals(2, gateways.size)
            assertNotEquals(firstOrigin, session.state.value.proxy!!.origin)
            assertEquals("ready", session.state.value.phase)
        } finally { scope.cancel(); gateways.forEach { it.close() } }
    }

    @Test fun explicitStopCancelsStartupWithoutResurrectingIt() = runBlocking {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        val gate = CompletableDeferred<Unit>()
        var stops = 0
        val session = WorkbenchSession(scope, { gate.await(); proxy() }, { stops++ }, { true })
        try {
            session.start(); session.stop()
            gate.complete(Unit)
            assertEquals("stopped", session.state.value.phase)
            assertNull(session.state.value.proxy)
            assertEquals(1, stops)
        } finally { scope.cancel() }
    }
}
