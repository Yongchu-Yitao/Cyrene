package ai.cyrene.mobile.data

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Test

class DesktopDataSnapshotTest {
    @Test
    fun roundTripPreservesPrefetchedDesktopData() {
        val project = JSONObject().put("id", "project-1").put("name", "Demo")
        val chat = JSONObject().put("id", "chat-1").put("title", "Chat")
        val chatDetail = JSONObject()
            .put("id", "chat-1")
            .put("messages", org.json.JSONArray().put(JSONObject().put("content", "Hello")))
        val task = JSONObject().put("id", "task-1").put("title", "Task")
        val taskDetail = JSONObject().put("id", "task-1").put("goal", "Ship it")
        val artifact = JSONObject().put("id", "artifact-1").put("name", "result.txt")
        val snapshot = DesktopDataSnapshot(
            projects = listOf(project),
            projectData = mapOf("project-1" to CachedProjectData(listOf(chat), listOf(task))),
            chatDetails = mapOf("chat-1" to chatDetail),
            taskDetails = mapOf("task-1" to taskDetail),
            taskArtifacts = mapOf("task-1" to listOf(artifact)),
        )

        val restored = DesktopDataSnapshot.fromJson(snapshot.toJson())

        assertEquals("Demo", restored.projects.single().getString("name"))
        assertEquals("Chat", restored.projectData.getValue("project-1").chats.single().getString("title"))
        assertEquals(
            "Hello",
            restored.chatDetails.getValue("chat-1")
                .getJSONArray("messages").getJSONObject(0).getString("content"),
        )
        assertEquals("Ship it", restored.taskDetails.getValue("task-1").getString("goal"))
        assertEquals(
            "result.txt",
            restored.taskArtifacts.getValue("task-1").single().getString("name"),
        )
    }
}
