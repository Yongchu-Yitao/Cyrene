package ai.cyrene.mobile

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ConversationTimelineTest {
    @Test
    fun splitsActivitiesAroundIntermediateMessagesLikeWorkbench() {
        val timeline = liveConversationTimeline(
            listOf(
                event("reasoning_delta").put("delta", "first thought"),
                event("tool_call_started")
                    .put("tool_call_id", "call_1").put("tool", "read_file"),
                event("intermediate_message").put(
                    "message",
                    JSONObject().put("id", "mid_1").put("content", "I found the entry point."),
                ),
                event("tool_call_started")
                    .put("tool_call_id", "call_2").put("tool", "exec_command"),
                event("tool_call_finished")
                    .put("tool_call_id", "call_2").put("tool", "exec_command")
                    .put("status", "completed"),
                event("reply_done").put("response", "Done."),
            ),
            running = false,
        )

        assertEquals(4, timeline.size)
        assertTrue(timeline[0].optBoolean("activityCard"))
        assertEquals("I found the entry point.", timeline[1].getString("content"))
        assertTrue(timeline[2].optBoolean("activityCard"))
        assertEquals(2, timeline[2].getJSONArray("trace").length())
        assertEquals("Done.", timeline[3].getString("content"))
    }

    @Test
    fun removesLiveIntermediateOnceDurableTranscriptOwnsIt() {
        val durable = JSONArray().put(
            JSONObject()
                .put("id", "mid_1")
                .put("role", "assistant")
                .put("content", "Already saved")
                .put("intermediate", true),
        )
        val live = listOf(
            JSONObject().put("id", "mid_1").put("content", "Already saved"),
            JSONObject().put("id", "live_reply").put("content", "Final"),
        )

        val filtered = withoutDurableDuplicates(live, durable)

        assertEquals(1, filtered.size)
        assertEquals("live_reply", filtered.single().getString("id"))
    }

    @Test
    fun runningConversationHasARealActivityCardEvenBeforeFirstEvent() {
        val timeline = liveConversationTimeline(emptyList(), running = true)

        assertEquals(1, timeline.size)
        assertTrue(timeline.single().optBoolean("activityCard"))
        assertTrue(timeline.single().optBoolean("runtimeActivityActive"))
        assertFalse(timeline.single().has("liveReply"))
    }

    @Test
    fun recognizesDurableActivityWhenTransportDropsMarker() {
        val reasoning = JSONObject()
            .put("id", "reasoning_msg_1")
            .put("role", "assistant")
            .put("content", "")
            .put("reasoning", "Inspecting the repository")
        val tool = JSONObject()
            .put("id", "activity_msg_1")
            .put("role", "assistant")
            .put("content", "")
            .put("trace", JSONArray().put(JSONObject().put("tool", "Read")))
        val prose = JSONObject()
            .put("id", "message_1")
            .put("role", "assistant")
            .put("content", "Visible reply")

        assertTrue(isConversationActivityMessage(reasoning))
        assertTrue(isConversationActivityMessage(tool))
        assertFalse(isConversationActivityMessage(prose))
    }

    private fun event(type: String) = JSONObject().put("type", type)
}
