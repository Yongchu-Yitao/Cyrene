package ai.cyrene.mobile.localagent

import ai.cyrene.mobile.localagent.model.*
import ai.cyrene.mobile.localagent.runtime.*
import org.junit.Assert.*
import org.junit.Test

class AgentRuntimeTest {
    private val budget = RunBudget(10, 20, 60_000, 100_000, 10_000, 1_000_000, 4, 2)

    @Test fun twoPhaseStateMachineRejectsSkippedExecution() {
        var run = RunSnapshot("run_1", "ls_1", 7, RunState.READY, budget)
        run = RunStateMachine.transition(run, RunSignal.START)
        run = RunStateMachine.transition(run, RunSignal.PHASE_1_STARTED)
        run = RunStateMachine.transition(run, RunSignal.USE_TOOLS)
        run = RunStateMachine.transition(run, RunSignal.PHASE_2_STARTED)
        run = RunStateMachine.transition(run, RunSignal.PHASE_2_STARTED)
        assertEquals(RunState.WAITING_PHASE_2_MODEL, run.state)
        assertFailsWith<IllegalStateException> { RunStateMachine.transition(run, RunSignal.COMPLETE) }
    }

    @Test fun runGenerationNeverChangesAcrossTransitions() {
        val start = RunSnapshot("run_1", "ls_1", 42, RunState.READY, budget)
        val next = RunStateMachine.transition(start, RunSignal.START)
        assertEquals(42, next.generation)
    }

    @Test fun contextKeepsStablePrefixFirstAndDynamicValuesLast() {
        val built = ContextBuilder().build(
            ContextInputs("prompt", "soul", "tools", "memory", "entities", listOf("skill"), "plan", null, listOf("old"), "new", "runtime generation=42", listOf("result")),
            maxInputTokens = 10_000,
            reservedOutputTokens = 1_000,
        )
        assertEquals(listOf("mobile_agent", "agent_personality", "frozen_tools"), built.items.take(3).map(ContextItem::authority))
        assertEquals("tool_observation", built.items.last().authority)
        assertEquals(64, built.stablePrefixSha256.length)
    }

    private inline fun <reified T : Throwable> assertFailsWith(block: () -> Unit): T {
        try { block() } catch (error: Throwable) { if (error is T) return error else throw error }
        fail("Expected ${T::class.java.simpleName}")
        throw AssertionError()
    }
}
