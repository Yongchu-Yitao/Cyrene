package ai.cyrene.mobile.localagent.runtime

import ai.cyrene.mobile.localagent.model.*
import ai.cyrene.mobile.localagent.protocol.EventAcceptance
import ai.cyrene.mobile.localagent.protocol.LocalAgentProtocol
import ai.cyrene.mobile.localagent.protocol.OrderedModelEventReducer
import org.json.JSONArray
import org.json.JSONObject
import java.util.UUID

data class FrozenRunInputs(
    val sessionId: String,
    val runId: String,
    val generation: Long,
    val promptBundleSha256: String,
    val mainToolBundleSha256: String,
    val userMessage: String,
    val contextItems: List<JSONObject>,
    val budget: RunBudget,
)

fun interface HarnessToolInvoker {
    suspend fun invoke(callId: String, name: String, arguments: JSONObject): ToolResult
}

interface RuntimeEventSink {
    suspend fun checkpoint(snapshot: RunSnapshot)
    suspend fun event(type: String, payload: JSONObject)
}

data class AgentRunOutcome(
    val stopReason: StopReason,
    val finalAnswer: String,
    val toolResults: List<ToolResult>,
    val remainingBudget: RunBudget,
)

class LocalAgentOrchestrator(
    private val model: LocalModelTransport,
    private val tools: HarnessToolInvoker,
    private val sink: RuntimeEventSink,
) {
    suspend fun run(inputs: FrozenRunInputs): AgentRunOutcome {
        var budget = inputs.budget.requireModelTurn()
        var snapshot = RunSnapshot(inputs.runId, inputs.sessionId, inputs.generation, RunState.BUILDING_PHASE_1_CONTEXT, budget)
        sink.checkpoint(snapshot)
        val phase1Turn = newId("mt")
        snapshot = snapshot.copy(state = RunState.WAITING_PHASE_1_MODEL, pendingModelTurnId = phase1Turn)
        sink.checkpoint(snapshot)
        val decision = completeTurn(
            model.start(
                modelPayload(
                    inputs, phase1Turn, "decision",
                    inputs.contextItems + JSONObject().put("role", "user").put("content", inputs.userMessage),
                    budget,
                ),
                "model:start:$phase1Turn",
            )
        )
        val decisionCalls = decision.toolCalls
        if (decisionCalls.isEmpty() && decision.message.isNotBlank()) {
            return complete(snapshot, decision.message, emptyList(), budget)
        }
        if (decisionCalls.size != 1 || decisionCalls.single().name !in setOf("use_tools", "ask_user", "quit")) {
            return stop(snapshot, StopReason.FATAL_ERROR, "", emptyList(), budget, "invalid_phase_1_decision")
        }
        when (decisionCalls.single().name) {
            "ask_user" -> {
                sink.event("user_input_required", JSONObject().put("arguments", decisionCalls.single().arguments))
                val question = decision.message.ifBlank { decisionCalls.single().arguments.optString("question") }
                return stop(snapshot, StopReason.USER_INPUT_REQUIRED, question, emptyList(), budget, "ask_user")
            }
            "quit" -> return complete(snapshot, decision.message.ifBlank { decisionCalls.single().arguments.optString("answer") }, emptyList(), budget)
        }

        snapshot = snapshot.copy(state = RunState.ENTERING_EXECUTION, pendingModelTurnId = null)
        sink.checkpoint(snapshot)
        val allResults = mutableListOf<ToolResult>()
        var inputItems = inputs.contextItems + JSONObject().put("role", "user").put("content", inputs.userMessage)
        var previousTurnId: String? = null
        var repeatedNoProgress = 0
        while (true) {
            budget = try { budget.requireModelTurn() } catch (_: BudgetStopped) {
                return stop(snapshot, StopReason.BUDGET_EXHAUSTED, "", allResults, budget, "model_turn_budget")
            }
            val turnId = newId("mt")
            snapshot = snapshot.copy(state = RunState.WAITING_PHASE_2_MODEL, budget = budget, pendingModelTurnId = turnId)
            sink.checkpoint(snapshot)
            val response = if (previousTurnId == null) {
                model.start(modelPayload(inputs, turnId, "execution", inputItems, budget), "model:start:$turnId")
            } else {
                val latest = allResults.takeLastWhile { it.callId in snapshot.pendingToolCallIds }
                model.continueTurn(
                    modelPayload(inputs, turnId, "execution", inputItems, budget)
                        .put("previous_model_turn_id", previousTurnId)
                        .put("tool_results", JSONArray(latest.map(::toolResultJson))),
                    "model:continue:$turnId",
                )
            }
            val turn = completeTurn(response)
            if (turn.toolCalls.isEmpty()) {
                if (turn.message.isNotBlank()) return complete(snapshot, turn.message, allResults, budget)
                repeatedNoProgress += 1
                if (repeatedNoProgress >= 2) return stop(snapshot, StopReason.REPEATED_FAILURE, "", allResults, budget, "model_made_no_progress")
                previousTurnId = null
                continue
            }
            repeatedNoProgress = 0
            budget = try { budget.requireToolCalls(turn.toolCalls.size) } catch (_: BudgetStopped) {
                return stop(snapshot, StopReason.BUDGET_EXHAUSTED, "", allResults, budget, "tool_call_budget")
            }
            val callIds = turn.toolCalls.map(TypedToolCall::callId).toSet()
            snapshot = snapshot.copy(state = RunState.EXECUTING_TOOLS, budget = budget, pendingToolCallIds = callIds)
            sink.checkpoint(snapshot)
            val roundResults = turn.toolCalls.map { call ->
                val result = try {
                    tools.invoke(call.callId, call.name, call.arguments)
                } catch (error: Throwable) {
                    ToolResult(call.callId, ToolResultStatus.ERROR, error.message ?: "Tool failed", errorType = "executor_error")
                }
                sink.event("tool_result", toolResultJson(result))
                result
            }
            if (roundResults.map(ToolResult::callId).toSet() != callIds) {
                return stop(snapshot, StopReason.FATAL_ERROR, "", allResults, budget, "missing_tool_result")
            }
            allResults += roundResults
            previousTurnId = turn.modelTurnId
            snapshot = snapshot.copy(state = RunState.SUBMITTING_TOOL_RESULTS, pendingToolCallIds = callIds)
            sink.checkpoint(snapshot)
        }
    }

    private suspend fun completeTurn(initial: JSONObject): CompletedTurn {
        val turnId = initial.getString("model_turn_id")
        val reducer = OrderedModelEventReducer()
        val accepted = mutableListOf<ModelEvent>()
        var state = initial.optString("state", "RUNNING")
        var response = initial
        while (true) {
            val events = response.optJSONArray("events") ?: JSONArray()
            for (index in 0 until events.length()) {
                val event = LocalAgentProtocol.parseModelEvent(events.getJSONObject(index))
                when (reducer.accept(event)) {
                    EventAcceptance.ACCEPTED -> { accepted += event; sink.event("model_event", events.getJSONObject(index)) }
                    EventAcceptance.DUPLICATE -> Unit
                    EventAcceptance.GAP -> throw ProtocolViolation("model_event_gap", "Model event sequence has a gap")
                }
            }
            state = response.optString("state", state)
            if (state != "RUNNING") break
            response = model.wait(turnId, reducer.cursor(turnId))
        }
        if (state in setOf("ERROR", "CANCELLED")) throw IllegalStateException("Model turn ended as $state")
        val message = accepted.filter { it.type == ModelEventType.MESSAGE_COMPLETED }
            .joinToString("") { JSONObject(it.payload).optString("content") }
        val calls = accepted.filter { it.type == ModelEventType.TOOL_CALL_COMPLETED }.map { event ->
            val payload = JSONObject(event.payload)
            TypedToolCall(
                payload.getString("call_id"), payload.getString("name"),
                runCatching { JSONObject(payload.getString("arguments_json")) }.getOrElse {
                    throw ProtocolViolation("invalid_tool_arguments", "Completed tool arguments are not JSON")
                },
            )
        }
        return CompletedTurn(turnId, message, calls)
    }

    private fun modelPayload(inputs: FrozenRunInputs, turnId: String, phase: String, items: List<JSONObject>, budget: RunBudget): JSONObject =
        JSONObject().put("session_id", inputs.sessionId).put("run_id", inputs.runId).put("model_turn_id", turnId)
            .put("bundle_generation", inputs.generation).put("phase", phase)
            .put("prompt_bundle_sha256", inputs.promptBundleSha256).put("tool_bundle_sha256", inputs.mainToolBundleSha256)
            .put("input_items", JSONArray(items)).put("previous_provider_state_ref", "")
            .put("limits", JSONObject().put("max_output_tokens", budget.remainingOutputTokens.coerceAtMost(32768))
                .put("remaining_model_turns", budget.remainingModelTurns).put("remaining_tool_calls", budget.remainingToolCalls))

    private suspend fun complete(snapshot: RunSnapshot, answer: String, results: List<ToolResult>, budget: RunBudget): AgentRunOutcome {
        sink.checkpoint(snapshot.copy(state = RunState.COMPLETED, stopReason = StopReason.COMPLETED, pendingModelTurnId = null, pendingToolCallIds = emptySet()))
        sink.event("final_answer", JSONObject().put("content", answer).put("tool_result_count", results.size))
        return AgentRunOutcome(StopReason.COMPLETED, answer, results, budget)
    }

    private suspend fun stop(snapshot: RunSnapshot, reason: StopReason, answer: String, results: List<ToolResult>, budget: RunBudget, detail: String): AgentRunOutcome {
        val state = if (reason in setOf(StopReason.USER_INPUT_REQUIRED, StopReason.APPROVAL_REQUIRED)) RunState.WAITING_USER_GUIDANCE else RunState.FAILED
        sink.checkpoint(snapshot.copy(state = state, stopReason = reason, pendingModelTurnId = null))
        sink.event("run_stopped", JSONObject().put("reason", reason.wireName).put("detail", detail))
        return AgentRunOutcome(reason, answer, results, budget)
    }

    private fun toolResultJson(value: ToolResult) = JSONObject()
        .put("call_id", value.callId).put("status", value.status.name.lowercase()).put("summary", value.summary)
        .put("inline_payload", value.inlinePayload ?: JSONObject.NULL).put("artifact_ref", value.artifactRef ?: JSONObject.NULL)
        .put("error_type", value.errorType ?: JSONObject.NULL)

    private fun newId(prefix: String) = "${prefix}_${UUID.randomUUID().toString().replace("-", "")}"
    private data class TypedToolCall(val callId: String, val name: String, val arguments: JSONObject)
    private data class CompletedTurn(val modelTurnId: String, val message: String, val toolCalls: List<TypedToolCall>)
}

interface LocalModelTransport {
    suspend fun start(payload: JSONObject, idempotencyKey: String): JSONObject
    suspend fun continueTurn(payload: JSONObject, idempotencyKey: String): JSONObject
    suspend fun wait(modelTurnId: String, cursor: Long): JSONObject
}
