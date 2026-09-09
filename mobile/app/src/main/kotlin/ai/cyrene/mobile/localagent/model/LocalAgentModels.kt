package ai.cyrene.mobile.localagent.model

const val LOCAL_AGENT_PROTOCOL = 1
const val MODEL_EVENT_SCHEMA = "cyrene-model-event-v1"
const val MAX_INLINE_TOOL_RESULT_BYTES = 64 * 1024

enum class RunState {
    CREATED, READY, BUILDING_PHASE_1_CONTEXT, WAITING_PHASE_1_MODEL,
    ASKING_USER, ENTERING_EXECUTION, BUILDING_PHASE_2_CONTEXT, WAITING_PHASE_2_MODEL,
    VALIDATING_TOOL_CALLS, WAITING_APPROVAL, EXECUTING_TOOLS, SUBMITTING_TOOL_RESULTS,
    COMPACTING, WAITING_USER_GUIDANCE, FINALIZING, PAUSED_OS,
    CANCELLING, FAILED, COMPLETED,
}

enum class StopReason(val wireName: String) {
    COMPLETED("completed"), USER_INPUT_REQUIRED("user_input_required"),
    APPROVAL_REQUIRED("approval_required"),
    CANCELLED("cancelled"), BUDGET_EXHAUSTED("budget_exhausted"),
    STEP_LIMIT("step_limit"), REPEATED_FAILURE("repeated_failure"),
    POLICY_DENIED("policy_denied"), RUNTIME_UNAVAILABLE("runtime_unavailable"),
    FATAL_ERROR("fatal_error"),
}

enum class ModelEventType(val wireName: String) {
    TURN_STARTED("turn.started"), MESSAGE_DELTA("message.delta"),
    MESSAGE_COMPLETED("message.completed"), REASONING_STATUS("reasoning.status"),
    TOOL_CALL_STARTED("tool_call.started"), TOOL_ARGUMENTS_DELTA("tool_call.arguments_delta"),
    TOOL_CALL_COMPLETED("tool_call.completed"), USAGE_UPDATED("usage.updated"),
    BUDGET_WARNING("budget.warning"), BUDGET_EXHAUSTED("budget.exhausted"),
    TURN_COMPLETED("turn.completed"), TURN_CANCELLED("turn.cancelled"), TURN_ERROR("turn.error");

    companion object {
        fun fromWire(value: String): ModelEventType = entries.firstOrNull { it.wireName == value }
            ?: throw ProtocolViolation("unknown_model_event", "Unknown model event: $value")
    }
}

data class ModelEvent(
    val modelTurnId: String,
    val sequence: Long,
    val type: ModelEventType,
    val payload: String,
)

enum class ToolResultStatus { SUCCESS, ERROR, DENIED, CANCELLED, TIMEOUT, UNKNOWN }

data class ToolResult(
    val callId: String,
    val status: ToolResultStatus,
    val summary: String,
    val inlinePayload: String? = null,
    val artifactRef: String? = null,
    val errorType: String? = null,
) {
    init {
        require(callId.isNotBlank())
        require(inlinePayload == null || inlinePayload.toByteArray().size <= MAX_INLINE_TOOL_RESULT_BYTES) {
            "inline tool result exceeds hard limit"
        }
        require(inlinePayload == null || artifactRef == null) { "result cannot be both inline and artifact" }
    }
}

class ProtocolViolation(val code: String, override val message: String) : IllegalArgumentException(message)
