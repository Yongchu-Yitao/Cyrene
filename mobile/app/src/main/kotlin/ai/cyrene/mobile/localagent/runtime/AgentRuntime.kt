package ai.cyrene.mobile.localagent.runtime

import ai.cyrene.mobile.localagent.model.*
import ai.cyrene.mobile.protocol.CanonicalJson
import ai.cyrene.mobile.protocol.hex
import org.json.JSONArray
import org.json.JSONObject
import java.security.MessageDigest

data class RunBudget(
    val remainingModelTurns: Int,
    val remainingToolCalls: Int,
    val remainingWallTimeMs: Long,
    val remainingInputTokens: Long,
    val remainingOutputTokens: Long,
    val remainingCostMicros: Long,
    val maxSubagents: Int,
    val maxSubagentDepth: Int,
) {
    fun requireModelTurn(): RunBudget {
        if (remainingModelTurns <= 0 || remainingWallTimeMs <= 0 || remainingCostMicros <= 0) {
            throw BudgetStopped("model budget exhausted")
        }
        return copy(remainingModelTurns = remainingModelTurns - 1)
    }

    fun requireToolCalls(count: Int): RunBudget {
        if (count < 0 || remainingToolCalls < count) throw BudgetStopped("tool call budget exhausted")
        return copy(remainingToolCalls = remainingToolCalls - count)
    }
}

data class RunSnapshot(
    val runId: String,
    val sessionId: String,
    val generation: Long,
    val state: RunState,
    val budget: RunBudget,
    val activePlanRevision: Long? = null,
    val pendingApprovalId: String? = null,
    val pendingModelTurnId: String? = null,
    val pendingToolCallIds: Set<String> = emptySet(),
    val compactionRef: String? = null,
    val stopReason: StopReason? = null,
)

enum class RunSignal {
    START, PHASE_1_STARTED, ASK_USER, USE_TOOLS, QUIT,
    PHASE_2_STARTED, TOOL_CALLS_RECEIVED, APPROVAL_NEEDED, APPROVAL_RESOLVED,
    TOOLS_FINISHED, RESULTS_SUBMITTED, COMPACTION_NEEDED, COMPACTION_FINISHED,
    FINALIZE, COMPLETE, OS_PAUSE, RESUME, CANCEL, FAIL,
}

object RunStateMachine {
    private val transitions: Map<Pair<RunState, RunSignal>, RunState> = buildMap {
        put(RunState.CREATED to RunSignal.START, RunState.BUILDING_PHASE_1_CONTEXT)
        put(RunState.READY to RunSignal.START, RunState.BUILDING_PHASE_1_CONTEXT)
        put(RunState.BUILDING_PHASE_1_CONTEXT to RunSignal.PHASE_1_STARTED, RunState.WAITING_PHASE_1_MODEL)
        put(RunState.WAITING_PHASE_1_MODEL to RunSignal.ASK_USER, RunState.ASKING_USER)
        put(RunState.ASKING_USER to RunSignal.RESUME, RunState.BUILDING_PHASE_1_CONTEXT)
        put(RunState.WAITING_PHASE_1_MODEL to RunSignal.USE_TOOLS, RunState.ENTERING_EXECUTION)
        put(RunState.WAITING_PHASE_1_MODEL to RunSignal.QUIT, RunState.FINALIZING)
        put(RunState.ENTERING_EXECUTION to RunSignal.PHASE_2_STARTED, RunState.BUILDING_PHASE_2_CONTEXT)
        put(RunState.BUILDING_PHASE_2_CONTEXT to RunSignal.PHASE_2_STARTED, RunState.WAITING_PHASE_2_MODEL)
        put(RunState.WAITING_PHASE_2_MODEL to RunSignal.TOOL_CALLS_RECEIVED, RunState.VALIDATING_TOOL_CALLS)
        put(RunState.VALIDATING_TOOL_CALLS to RunSignal.APPROVAL_NEEDED, RunState.WAITING_APPROVAL)
        put(RunState.WAITING_APPROVAL to RunSignal.APPROVAL_RESOLVED, RunState.EXECUTING_TOOLS)
        put(RunState.VALIDATING_TOOL_CALLS to RunSignal.TOOLS_FINISHED, RunState.EXECUTING_TOOLS)
        put(RunState.EXECUTING_TOOLS to RunSignal.TOOLS_FINISHED, RunState.SUBMITTING_TOOL_RESULTS)
        put(RunState.SUBMITTING_TOOL_RESULTS to RunSignal.RESULTS_SUBMITTED, RunState.WAITING_PHASE_2_MODEL)
        put(RunState.WAITING_PHASE_2_MODEL to RunSignal.COMPACTION_NEEDED, RunState.COMPACTING)
        put(RunState.COMPACTING to RunSignal.COMPACTION_FINISHED, RunState.WAITING_PHASE_2_MODEL)
        put(RunState.WAITING_PHASE_2_MODEL to RunSignal.FINALIZE, RunState.FINALIZING)
        put(RunState.FINALIZING to RunSignal.COMPLETE, RunState.COMPLETED)
        RunState.entries.filterNot { it in setOf(RunState.COMPLETED, RunState.FAILED) }.forEach { state ->
            put(state to RunSignal.OS_PAUSE, RunState.PAUSED_OS)
            put(state to RunSignal.CANCEL, RunState.CANCELLING)
            put(state to RunSignal.FAIL, RunState.FAILED)
        }
        put(RunState.PAUSED_OS to RunSignal.RESUME, RunState.BUILDING_PHASE_1_CONTEXT)
        put(RunState.CANCELLING to RunSignal.COMPLETE, RunState.COMPLETED)
    }

    fun transition(snapshot: RunSnapshot, signal: RunSignal): RunSnapshot {
        val next = transitions[snapshot.state to signal]
            ?: throw IllegalStateException("Invalid run transition ${snapshot.state} + $signal")
        val reason = when {
            next == RunState.COMPLETED && snapshot.state == RunState.CANCELLING -> StopReason.CANCELLED
            next == RunState.COMPLETED -> StopReason.COMPLETED
            next == RunState.FAILED -> StopReason.FATAL_ERROR
            else -> snapshot.stopReason
        }
        return snapshot.copy(state = next, stopReason = reason)
    }
}

data class ContextInputs(
    val promptBundle: String,
    val soul: String,
    val toolDefinitions: String,
    val memory: String,
    val entities: String,
    val activeSkills: List<String>,
    val runControl: String,
    val compaction: String?,
    val recentEvents: List<String>,
    val currentInput: String,
    val runtimeReminder: String,
    val toolResults: List<String>,
)

data class BuiltContext(
    val items: List<ContextItem>,
    val stablePrefixSha256: String,
    val estimatedTokens: Int,
)

data class ContextItem(val authority: String, val trust: String, val content: String)

class ContextBuilder(private val charsPerToken: Double = 3.5) {
    fun build(inputs: ContextInputs, maxInputTokens: Int, reservedOutputTokens: Int): BuiltContext {
        val stable = listOf(
            ContextItem("mobile_agent", "trusted", inputs.promptBundle),
            ContextItem("agent_personality", "trusted", inputs.soul),
            ContextItem("frozen_tools", "trusted", inputs.toolDefinitions),
        )
        val dynamic = buildList {
            add(ContextItem("local_context", "semi_trusted", inputs.memory))
            add(ContextItem("local_entities", "semi_trusted", inputs.entities))
            inputs.activeSkills.forEach { add(ContextItem("activated_skill", "semi_trusted", it)) }
            add(ContextItem("run_control", "trusted", inputs.runControl))
            inputs.compaction?.let { add(ContextItem("compaction", "trusted", it)) }
            inputs.recentEvents.forEach { add(ContextItem("conversation", "untrusted", it)) }
            add(ContextItem("user", "untrusted", inputs.currentInput))
            add(ContextItem("runtime", "trusted_factual", inputs.runtimeReminder))
            inputs.toolResults.forEach { add(ContextItem("tool_observation", "untrusted", it)) }
        }
        val items = stable + dynamic
        val tokens = estimate(items)
        if (tokens + reservedOutputTokens > maxInputTokens) {
            throw ContextOverflow(tokens, maxInputTokens - reservedOutputTokens)
        }
        val stableJson = JSONArray(stable.map { item ->
            JSONObject().put("authority", item.authority).put("trust", item.trust).put("content", item.content)
        })
        val hash = MessageDigest.getInstance("SHA-256")
            .digest(CanonicalJson.encode(stableJson).toByteArray()).hex()
        return BuiltContext(items, hash, tokens)
    }

    private fun estimate(items: List<ContextItem>): Int =
        kotlin.math.ceil(items.sumOf { it.content.length } / charsPerToken).toInt()
}

data class CompactionState(
    val objective: String,
    val constraints: List<String>,
    val bundleGeneration: Long,
    val activePlan: String?,
    val approvals: List<String>,
    val pendingToolCalls: List<String>,
    val subagents: List<String>,
    val exactFacts: List<String>,
    val artifacts: List<String>,
    val componentVersions: Map<String, String>,
    val unresolved: List<String>,
    val nextSafeAction: String,
) {
    fun toJson(): JSONObject = JSONObject()
        .put("schema", "cyrene-context-compaction-v1")
        .put("objective", objective)
        .put("constraints", JSONArray(constraints))
        .put("bundle_generation", bundleGeneration)
        .put("active_plan", activePlan ?: JSONObject.NULL)
        .put("approvals", JSONArray(approvals))
        .put("pending_tool_calls", JSONArray(pendingToolCalls))
        .put("subagents", JSONArray(subagents))
        .put("exact_facts", JSONArray(exactFacts))
        .put("artifacts", JSONArray(artifacts))
        .put("component_versions", JSONObject(componentVersions))
        .put("unresolved", JSONArray(unresolved))
        .put("next_safe_action", nextSafeAction)
}

interface DurableRunStore {
    suspend fun load(runId: String): RunSnapshot?
    suspend fun save(snapshot: RunSnapshot)
    suspend fun appendEvent(runId: String, type: String, payload: JSONObject)
}

class RunCheckpointController(private val store: DurableRunStore) {
    suspend fun signal(snapshot: RunSnapshot, signal: RunSignal, payload: JSONObject = JSONObject()): RunSnapshot {
        val next = RunStateMachine.transition(snapshot, signal)
        store.save(next)
        store.appendEvent(next.runId, "run_state_changed", payload.put("state", next.state.name).put("signal", signal.name))
        return next
    }
}

class BudgetStopped(message: String) : IllegalStateException(message)
class ContextOverflow(val estimatedTokens: Int, val availableTokens: Int) : IllegalStateException("Context requires $estimatedTokens tokens; $availableTokens available")
