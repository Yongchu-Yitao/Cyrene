package ai.cyrene.mobile.localagent

import ai.cyrene.mobile.localagent.runtime.LocalAgentAttachment
import ai.cyrene.mobile.localagent.runtime.MAX_LOCAL_ATTACHMENT_BYTES
import ai.cyrene.mobile.localagent.runtime.attachmentAwareUserMessage
import ai.cyrene.mobile.localagent.runtime.localAgentAttachments
import org.json.JSONArray
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class LocalAgentAttachmentTest {
    private val attachment = LocalAgentAttachment(
        id = "la_123",
        name = "notes.txt",
        contentType = "text/plain",
        size = 12,
        sha256 = "a".repeat(64),
        localPath = "/private/files/local-agent-attachments/notes.txt",
        workspacePath = "attachments/run_1/01-notes.txt",
    )

    @Test fun attachmentManifestExposesWorkspaceReferenceButNotAndroidPrivatePath() {
        val message = attachmentAwareUserMessage("Summarize it", listOf(attachment))

        assertTrue(message.contains("attachments/run_1/01-notes.txt"))
        assertTrue(message.contains("untrusted data"))
        assertFalse(message.contains(attachment.localPath))
    }

    @Test fun attachmentMetadataRoundTripsThroughDurableJson() {
        val encoded = JSONArray().put(attachment.toJson()).toString()

        assertEquals(listOf(attachment), localAgentAttachments(encoded))
    }

    @Test(expected = IllegalArgumentException::class)
    fun workspaceTraversalIsRejected() {
        val invalid = attachment.copy(workspacePath = "attachments/../secret")
        localAgentAttachments(JSONArray().put(invalid.toJson()).toString())
    }

    @Test fun localAttachmentLimitIs32MiB() {
        assertEquals(32L * 1024 * 1024, MAX_LOCAL_ATTACHMENT_BYTES)
    }
}
