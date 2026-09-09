package ai.cyrene.mobile.localagent.tooling

import ai.cyrene.mobile.localagent.model.*
import ai.cyrene.mobile.protocol.CanonicalJson
import ai.cyrene.mobile.protocol.hex
import org.json.JSONArray
import org.json.JSONObject
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.withContext
import java.security.MessageDigest
import java.util.concurrent.ConcurrentHashMap

enum class ToolRisk(val wireName: String) {
    READ_ONLY("read_only"), SEARCH_ONLY("search_only"), COMPUTE_ONLY("compute_only"),
    WRITE_LOCAL("write_local"), PROCESS_EXECUTION("process_execution"),
    NETWORK_OPEN_WORLD("network_open_world"), EXTERNAL_SHARE("external_share"),
    DESTRUCTIVE("destructive"), IDENTITY_ACCESS("identity_access"),
    SECURITY_SENSITIVE("security_sensitive"),
}

enum class ToolExecutorKind { ANDROID_HOST, LINUX_GUEST, HARNESS }
enum class PermissionDecision { ALLOW, DENY, ASK_USER, APPROVAL_REQUIRED, REQUIRE_STRONGER_AUTH, RUN_IN_SANDBOX, DRAFT_ONLY }

data class ToolContract(
    val capabilityId: String,
    val packageId: String,
    val name: String,
    val purpose: String,
    val inputSchema: JSONObject,
    val outputSchema: JSONObject,
    val schemaHash: String,
    val risk: ToolRisk,
    val sideEffect: Boolean,
    val concurrencySafe: Boolean,
    val executor: ToolExecutorKind,
    val timeoutMs: Long,
    val maxResultBytes: Int,
    val idempotent: Boolean,
    val minimumRuntime: Int,
)

class ProgressiveToolRegistry(contracts: Collection<ToolContract>) {
    private val contracts = contracts.associateBy(ToolContract::capabilityId)
    private val described = ConcurrentHashMap.newKeySet<String>()

    init {
        require(this.contracts.size == contracts.size) { "duplicate capability id" }
        contracts.forEach { contract ->
            require(contract.schemaHash == schemaHash(contract.inputSchema)) { "tool schema hash mismatch: ${contract.capabilityId}" }
            require(contract.minimumRuntime <= LOCAL_AGENT_PROTOCOL) { "tool requires newer runtime" }
            require(contract.maxResultBytes in 1..MAX_INLINE_TOOL_RESULT_BYTES)
        }
    }

    fun discover(packageId: String? = null): List<Map<String, String>> = contracts.values
        .filter { packageId == null || it.packageId == packageId }
        .sortedBy(ToolContract::capabilityId)
        .map { mapOf("capability_id" to it.capabilityId, "name" to it.name, "purpose" to it.purpose, "risk" to it.risk.wireName) }

    fun describe(capabilityId: String): ToolContract = contracts[capabilityId]
        ?.also { described += capabilityId }
        ?: throw ToolCallFailure("unknown_tool", "Capability is not present in the local tool contract")

    fun validateInvoke(capabilityId: String, schemaHash: String, arguments: JSONObject): ToolContract {
        val contract = contracts[capabilityId] ?: throw ToolCallFailure("unknown_tool", "Capability is unavailable")
        if (capabilityId !in described) throw ToolCallFailure("describe_required", "Capability must be described before invoke")
        if (contract.schemaHash != schemaHash) throw ToolCallFailure("schema_changed", "Capability schema differs from the local tool contract")
        StrictJsonSchema.validate(contract.inputSchema, arguments)
        return contract
    }
}

object StrictJsonSchema {
    fun validate(schema: JSONObject, value: Any?, path: String = "$") {
        when (schema.getString("type")) {
            "object" -> validateObject(schema, value, path)
            "array" -> {
                if (value !is JSONArray) invalid(path, "must be an array")
                val itemSchema = schema.optJSONObject("items")
                if (itemSchema != null) (0 until value.length()).forEach { validate(itemSchema, value.get(it), "$path[$it]") }
            }
            "string" -> {
                if (value !is String) invalid(path, "must be a string")
                if (value.length > schema.optInt("maxLength", Int.MAX_VALUE)) invalid(path, "is too long")
                schema.optJSONArray("enum")?.let { allowed ->
                    if ((0 until allowed.length()).none { allowed.getString(it) == value }) invalid(path, "is not an allowed value")
                }
            }
            "integer" -> if (value !is Int && value !is Long) invalid(path, "must be an integer")
            "number" -> if (value !is Number || value.toDouble().isNaN() || value.toDouble().isInfinite()) invalid(path, "must be finite")
            "boolean" -> if (value !is Boolean) invalid(path, "must be a boolean")
            "null" -> if (value != null && value != JSONObject.NULL) invalid(path, "must be null")
            else -> invalid(path, "uses an unsupported schema type")
        }
    }

    private fun validateObject(schema: JSONObject, value: Any?, path: String) {
        if (value !is JSONObject) invalid(path, "must be an object")
        val properties = schema.optJSONObject("properties") ?: JSONObject()
        val known = properties.keys().asSequence().toSet()
        val actual = value.keys().asSequence().toSet()
        if (schema.optBoolean("additionalProperties", true).not() && (actual - known).isNotEmpty()) {
            invalid(path, "contains unknown fields: ${actual - known}")
        }
        val required = schema.optJSONArray("required")?.let { array ->
            (0 until array.length()).map(array::getString).toSet()
        }.orEmpty()
        if ((required - actual).isNotEmpty()) invalid(path, "is missing fields: ${required - actual}")
        (actual intersect known).forEach { key -> validate(properties.getJSONObject(key), value.get(key), "$path.$key") }
    }

    private fun invalid(path: String, reason: String): Nothing = throw ToolCallFailure("invalid_arguments", "$path $reason")
}

data class PermissionContext(
    val sessionId: String,
    val runId: String,
    val generation: Long,
    val permissionMode: String,
    val policyMaximum: PermissionDecision,
)

class PermissionEngine {
    fun decide(contract: ToolContract, context: PermissionContext): PermissionDecision {
        val local = when (contract.risk) {
            ToolRisk.READ_ONLY, ToolRisk.SEARCH_ONLY, ToolRisk.COMPUTE_ONLY -> PermissionDecision.ALLOW
            ToolRisk.WRITE_LOCAL -> if (context.permissionMode == "plan") PermissionDecision.DENY else PermissionDecision.RUN_IN_SANDBOX
            ToolRisk.PROCESS_EXECUTION -> if (context.permissionMode == "plan") PermissionDecision.DENY else PermissionDecision.RUN_IN_SANDBOX
            ToolRisk.NETWORK_OPEN_WORLD, ToolRisk.EXTERNAL_SHARE, ToolRisk.DESTRUCTIVE,
            ToolRisk.IDENTITY_ACCESS, ToolRisk.SECURITY_SENSITIVE -> PermissionDecision.APPROVAL_REQUIRED
        }
        return moreRestrictive(local, context.policyMaximum)
    }

    private fun moreRestrictive(left: PermissionDecision, right: PermissionDecision): PermissionDecision {
        val rank = listOf(
            PermissionDecision.ALLOW, PermissionDecision.RUN_IN_SANDBOX, PermissionDecision.DRAFT_ONLY,
            PermissionDecision.ASK_USER, PermissionDecision.APPROVAL_REQUIRED,
            PermissionDecision.REQUIRE_STRONGER_AUTH, PermissionDecision.DENY,
        )
        return if (rank.indexOf(left) >= rank.indexOf(right)) left else right
    }
}

data class ApprovalBinding(
    val approvalId: String,
    val toolCallId: String,
    val capabilityId: String,
    val argsHash: String,
    val generation: Long,
    val risk: ToolRisk,
    val expiresAtEpochMs: Long,
    val approved: Boolean,
) {
    fun authorizes(callId: String, capability: String, arguments: JSONObject, bundleGeneration: Long, nowEpochMs: Long): Boolean =
        approved && nowEpochMs < expiresAtEpochMs && toolCallId == callId && capabilityId == capability &&
            argsHash == schemaHash(arguments) && generation == bundleGeneration
}

interface ToolExecutionAdapter {
    suspend fun execute(callId: String, contract: ToolContract, arguments: JSONObject): ToolResult
    suspend fun cancel(callId: String): Boolean
}

interface ToolResultRecorder {
    suspend fun recordExactlyOnce(result: ToolResult): Boolean
}

class ToolDispatcher(
    private val registry: ProgressiveToolRegistry,
    private val permissions: PermissionEngine,
    private val adapters: Map<ToolExecutorKind, ToolExecutionAdapter>,
    private val recorder: ToolResultRecorder,
) {
    suspend fun invoke(
        callId: String,
        capabilityId: String,
        schemaHash: String,
        arguments: JSONObject,
        context: PermissionContext,
        approval: ApprovalBinding? = null,
        nowEpochMs: Long = System.currentTimeMillis(),
    ): ToolResult {
        val result = try {
            val contract = registry.validateInvoke(capabilityId, schemaHash, arguments)
            when (permissions.decide(contract, context)) {
                PermissionDecision.DENY -> denied(callId, "policy_denied", "Tool is denied by policy")
                PermissionDecision.APPROVAL_REQUIRED, PermissionDecision.REQUIRE_STRONGER_AUTH -> {
                    if (approval?.authorizes(callId, capabilityId, arguments, context.generation, nowEpochMs) != true) {
                        denied(callId, "approval_required", "A matching structured approval is required")
                    } else execute(callId, contract, arguments)
                }
                PermissionDecision.ASK_USER -> denied(callId, "user_input_required", "User input is required")
                PermissionDecision.DRAFT_ONLY -> denied(callId, "draft_only", "Only a draft action is authorized")
                PermissionDecision.ALLOW, PermissionDecision.RUN_IN_SANDBOX -> execute(callId, contract, arguments)
            }
        } catch (failure: ToolCallFailure) {
            ToolResult(callId, ToolResultStatus.ERROR, failure.message, errorType = failure.code)
        } catch (cancelled: kotlinx.coroutines.CancellationException) {
            ToolResult(callId, ToolResultStatus.CANCELLED, "Tool execution was cancelled", errorType = "cancelled")
        } catch (error: Throwable) {
            ToolResult(callId, ToolResultStatus.ERROR, error.message ?: "Tool execution failed", errorType = "internal_error")
        }
        val recorded = withContext(NonCancellable) { recorder.recordExactlyOnce(result) }
        if (!recorded) throw ToolCallFailure("duplicate_tool_result", "A final Tool Result already exists")
        return result
    }

    private suspend fun execute(callId: String, contract: ToolContract, arguments: JSONObject): ToolResult =
        adapters[contract.executor]?.execute(callId, contract, arguments)
            ?: ToolResult(callId, ToolResultStatus.ERROR, "Executor is unavailable", errorType = "runtime_unavailable")

    private fun denied(callId: String, type: String, message: String) = ToolResult(callId, ToolResultStatus.DENIED, message, errorType = type)
}

class ToolCallFailure(val code: String, override val message: String) : IllegalArgumentException(message)

fun schemaHash(value: JSONObject): String = MessageDigest.getInstance("SHA-256")
    .digest(CanonicalJson.encode(value).toByteArray())
    .hex()
