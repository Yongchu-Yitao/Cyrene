package ai.cyrene.mobile.localagent

import ai.cyrene.mobile.localagent.model.*
import ai.cyrene.mobile.localagent.tooling.*
import kotlinx.coroutines.runBlocking
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class ToolingTest {
    private val schema = JSONObject()
        .put("type", "object")
        .put("properties", JSONObject().put("path", JSONObject().put("type", "string").put("maxLength", 100)))
        .put("required", JSONArray().put("path"))
        .put("additionalProperties", false)

    @Test fun capabilityMustBeDescribedAndSchemaMustStayFrozen() {
        val registry = ProgressiveToolRegistry(listOf(contract()))
        val args = JSONObject().put("path", "a.txt")
        assertEquals("describe_required", assertFailsWith<ToolCallFailure> { registry.validateInvoke("code.write", schemaHash(schema), args) }.code)
        registry.describe("code.write")
        assertEquals("schema_changed", assertFailsWith<ToolCallFailure> { registry.validateInvoke("code.write", "bad", args) }.code)
        assertEquals("invalid_arguments", assertFailsWith<ToolCallFailure> { registry.validateInvoke("code.write", schemaHash(schema), JSONObject().put("path", "x").put("extra", 1)) }.code)
    }

    @Test fun approvalIsBoundToArgsGenerationAndSingleToolResult() = runBlocking {
        val registry = ProgressiveToolRegistry(listOf(contract())).also { it.describe("code.write") }
        val recorded = mutableListOf<ToolResult>()
        val dispatcher = ToolDispatcher(
            registry, PermissionEngine(),
            mapOf(ToolExecutorKind.LINUX_GUEST to object : ToolExecutionAdapter {
                override suspend fun execute(callId: String, contract: ToolContract, arguments: JSONObject) = ToolResult(callId, ToolResultStatus.SUCCESS, "written")
                override suspend fun cancel(callId: String) = true
            }),
            object : ToolResultRecorder { override suspend fun recordExactlyOnce(result: ToolResult) = recorded.none { it.callId == result.callId }.also { if (it) recorded += result } },
        )
        val args = JSONObject().put("path", "a.txt")
        val context = PermissionContext("ls_1", "run_1", 9, "default", PermissionDecision.ALLOW)
        val denied = dispatcher.invoke("call_1", "code.write", schemaHash(schema), args, context)
        assertEquals(ToolResultStatus.DENIED, denied.status)
        val approval = ApprovalBinding("ap_1", "call_2", "code.write", schemaHash(args), 9, ToolRisk.DESTRUCTIVE, Long.MAX_VALUE, true)
        val success = dispatcher.invoke("call_2", "code.write", schemaHash(schema), args, context, approval)
        assertEquals(ToolResultStatus.SUCCESS, success.status)
        assertEquals(2, recorded.size)
    }

    private fun contract() = ToolContract("code.write", "code_tools", "Write", "write a workspace file", schema, JSONObject().put("type", "object"), schemaHash(schema), ToolRisk.DESTRUCTIVE, true, false, ToolExecutorKind.LINUX_GUEST, 10_000, 1024, false, 1)

    private inline fun <reified T : Throwable> assertFailsWith(block: () -> Unit): T {
        try { block() } catch (error: Throwable) { if (error is T) return error else throw error }
        fail("Expected ${T::class.java.simpleName}")
        throw AssertionError()
    }
}
