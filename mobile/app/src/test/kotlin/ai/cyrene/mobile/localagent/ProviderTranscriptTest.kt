package ai.cyrene.mobile.localagent

import ai.cyrene.mobile.localagent.runtime.ProviderTranscript
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ProviderTranscriptTest {
    @Test fun chatTranscriptRetainsEveryToolRoundAndExactFailure() {
        val transcript = ProviderTranscript(JSONArray().put(
            JSONObject().put("role", "user").put("content", "work"),
        ))
        transcript.append(assistantCall("call_1", "Read"))
        transcript.appendChatToolResults(JSONArray().put(result("call_1", "success", "read", "{\"content\":\"a\"}")))
        transcript.append(assistantCall("call_2", "Bash"))
        transcript.appendChatToolResults(JSONArray().put(
            result("call_2", "error", "Command exited with code 127", null, "command_failed"),
        ))

        val snapshot = transcript.snapshot()
        assertEquals(5, snapshot.length())
        assertEquals("call_1", snapshot.getJSONObject(2).getString("tool_call_id"))
        val finalResult = JSONObject(snapshot.getJSONObject(4).getString("content"))
        assertEquals("error", finalResult.getString("status"))
        assertEquals("command_failed", finalResult.getString("error_type"))
        assertFalse(finalResult.has("payload"))
        assertTrue(finalResult.getString("summary").contains("127"))
    }

    @Test fun responsesTranscriptRetainsOutputsAndDeduplicatesResults() {
        val transcript = ProviderTranscript(JSONArray().put(
            JSONObject().put("role", "user").put("content", "work"),
        ))
        transcript.append(JSONObject().put("type", "function_call").put("call_id", "call_1")
            .put("name", "Read").put("arguments", "{}"))
        val results = JSONArray().put(result("call_1", "success", "read", "{\"content\":\"a\"}"))
        transcript.appendResponsesToolResults(results)
        transcript.appendResponsesToolResults(results)

        val snapshot = transcript.snapshot()
        assertEquals(3, snapshot.length())
        assertEquals("function_call", snapshot.getJSONObject(1).getString("type"))
        assertEquals("function_call_output", snapshot.getJSONObject(2).getString("type"))
        assertEquals("call_1", snapshot.getJSONObject(2).getString("call_id"))
    }

    private fun assistantCall(callId: String, name: String) = JSONObject()
        .put("role", "assistant")
        .put("tool_calls", JSONArray().put(JSONObject().put("id", callId).put("type", "function")
            .put("function", JSONObject().put("name", name).put("arguments", "{}"))))

    private fun result(
        callId: String,
        status: String,
        summary: String,
        payload: String?,
        errorType: String? = null,
    ) = JSONObject()
        .put("call_id", callId)
        .put("status", status)
        .put("summary", summary)
        .put("inline_payload", payload ?: JSONObject.NULL)
        .put("artifact_ref", JSONObject.NULL)
        .put("error_type", errorType ?: JSONObject.NULL)
}
