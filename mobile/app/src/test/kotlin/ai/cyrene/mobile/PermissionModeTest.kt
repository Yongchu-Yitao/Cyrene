package ai.cyrene.mobile

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class PermissionModeTest {
    @Test
    fun unknownOrMissingModeDefaultsToAuto() {
        assertEquals(PermissionMode.AUTO, PermissionMode.fromWireValue(null))
        assertEquals(PermissionMode.AUTO, PermissionMode.fromWireValue("unknown"))
        assertEquals(PermissionMode.DEFAULT, PermissionMode.fromWireValue(" DEFAULT "))
    }

    @Test
    fun planFallsBackToDefaultWhenAnsweringOrRunningTasks() {
        assertEquals("default", PermissionMode.PLAN.approvalWireValue())
        assertEquals("default", PermissionMode.PLAN.taskWireValue())
        assertEquals("auto", PermissionMode.AUTO.approvalWireValue())
        assertEquals("auto", PermissionMode.AUTO.taskWireValue())
    }

    @Test
    fun parsesHiddenPermissionQuestionFromChatDetail() {
        val chat = JSONObject().put(
            "pending_question",
            JSONObject()
                .put("id", "question_1")
                .put("text", "Allow access outside the workspace?")
                .put("kind", "write_permission_request")
                .put("allowCustom", false)
                .put(
                    "options",
                    JSONArray()
                        .put(JSONObject().put("id", "allow_once").put("label", "Allow once"))
                        .put("Deny"),
                ),
        )

        val question = requireNotNull(pendingApprovalQuestion(chat))
        assertEquals("question_1", question.id)
        assertEquals("Allow access outside the workspace?", question.prompt)
        assertEquals(listOf("在本次会话同意", "同意一次", "拒绝"), question.options.map { it.label })
        assertFalse(question.allowCustom)
    }

    @Test
    fun normalizesLegacyPermissionQuestionToScopedSelector() {
        val question = requireNotNull(
            parseApprovalQuestion(
                JSONObject()
                    .put("id", "question_legacy")
                    .put("allow_custom", true)
                    .put(
                        "options",
                        JSONArray().put("Allow once").put("Always allow").put("Deny"),
                    )
                    .put("meta", JSONObject().put("kind", "read_elevation")),
            ),
        )

        assertEquals("read_elevation", question.kind)
        assertEquals(listOf("在本次会话同意", "同意一次", "拒绝"), question.options.map { it.label })
        assertFalse(question.allowCustom)
    }

    @Test
    fun keepsClarificationChoicesAndCustomAnswer() {
        val question = requireNotNull(
            parseApprovalQuestion(
                JSONObject()
                    .put("id", "question_clarification")
                    .put("kind", "clarification")
                    .put("options", JSONArray().put("北京").put("上海")),
            ),
        )

        assertEquals(listOf("北京", "上海"), question.options.map { it.label })
        assertTrue(question.allowCustom)
    }

    @Test
    fun fallsBackToAwaitingUserRunEvent() {
        val event = JSONObject()
            .put("type", "awaiting_user")
            .put(
                "pending_question",
                JSONObject()
                    .put("questionId", "question_2")
                    .put("prompt", "Continue?")
                    .put("choices", JSONArray().put("Continue").put("Stop")),
            )

        val question = requireNotNull(pendingApprovalQuestion(JSONObject(), listOf(event)))
        assertEquals("question_2", question.id)
        assertEquals("Continue?", question.prompt)
        assertTrue(question.allowCustom)
    }
}
