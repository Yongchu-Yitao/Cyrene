package ai.cyrene.mobile.localagent

import ai.cyrene.mobile.localagent.model.*
import ai.cyrene.mobile.localagent.runtime.*
import kotlinx.coroutines.runBlocking
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class LocalAgentOrchestratorTest {
    @Test fun twoPhaseLoopReturnsExactlyOneResultPerToolCall() = runBlocking {
        val snapshots = mutableListOf<RunSnapshot>()
        val events = mutableListOf<String>()
        val calls = mutableListOf<String>()
        val transport = object : LocalModelTransport {
            override suspend fun start(payload: JSONObject, idempotencyKey: String): JSONObject =
                if (payload.getString("phase") == "decision") decision(payload.getString("model_turn_id"), "do work")
                else toolTurn(payload.getString("model_turn_id"))
            override suspend fun continueTurn(payload: JSONObject, idempotencyKey: String): JSONObject {
                assertEquals(1, payload.getJSONArray("tool_results").length())
                return finalTurn(payload.getString("model_turn_id"))
            }
            override suspend fun wait(modelTurnId: String, cursor: Long) = error("all fixtures complete inline")
        }
        val orchestrator = LocalAgentOrchestrator(
            transport,
            HarnessToolInvoker { callId, name, _ -> calls += name; ToolResult(callId, ToolResultStatus.SUCCESS, "read") },
            object : RuntimeEventSink {
                override suspend fun checkpoint(snapshot: RunSnapshot) { snapshots += snapshot }
                override suspend fun event(type: String, payload: JSONObject) { events += type }
            },
        )
        val outcome = orchestrator.run(
            FrozenRunInputs(
                "ls_1", "run_1", 4, "p".repeat(64), "t".repeat(64), "do work",
                listOf(JSONObject().put("role", "user").put("content", "do work")),
                RunBudget(5, 5, 60_000, 10_000, 10_000, 1000, 2, 1),
            )
        )
        assertEquals(StopReason.COMPLETED, outcome.stopReason)
        assertEquals("done", outcome.finalAnswer)
        assertEquals(listOf("Read"), calls)
        assertEquals(1, outcome.toolResults.size)
        assertEquals(RunState.COMPLETED, snapshots.last().state)
        assertTrue("tool_result" in events)
    }

    private fun decision(turn: String, task: String) = result(
        turn,
        listOf(event(turn, 1, "turn.started", JSONObject()), event(turn, 2, "tool_call.completed", JSONObject().put("call_id", "decision_1").put("name", "use_tools").put("arguments_json", JSONObject().put("task", task).toString())), event(turn, 3, "turn.completed", JSONObject())),
    )
    private fun toolTurn(turn: String) = result(
        turn,
        listOf(event(turn, 1, "turn.started", JSONObject()), event(turn, 2, "tool_call.completed", JSONObject().put("call_id", "call_1").put("name", "Read").put("arguments_json", JSONObject().put("path", "a").toString())), event(turn, 3, "turn.completed", JSONObject())),
    )
    private fun finalTurn(turn: String) = result(
        turn,
        listOf(event(turn, 1, "turn.started", JSONObject()), event(turn, 2, "message.completed", JSONObject().put("content", "done")), event(turn, 3, "turn.completed", JSONObject())),
    )
    private fun result(turn: String, events: List<JSONObject>) = JSONObject().put("model_turn_id", turn).put("state", "COMPLETED").put("events", JSONArray(events))
    private fun event(turn: String, sequence: Int, type: String, payload: JSONObject) = JSONObject()
        .put("schema", MODEL_EVENT_SCHEMA).put("model_turn_id", turn).put("sequence", sequence).put("type", type).put("payload", payload)
}
