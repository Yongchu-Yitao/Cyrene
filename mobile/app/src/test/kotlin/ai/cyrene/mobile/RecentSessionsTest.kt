package ai.cyrene.mobile

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Test

class RecentSessionsTest {
    @Test
    fun combinesEveryProjectAndSortsByUpdatedTime() {
        val projectA = JSONObject().put("id", "project_a").put("name", "Alpha")
        val projectB = JSONObject().put("id", "project_b").put("name", "Beta")
        val chatA = JSONObject()
            .put("id", "chat_a")
            .put("updated_at", "2026-08-01T08:00:00Z")
        val taskA = JSONObject()
            .put("id", "task_a")
            .put("updatedAt", "2026-08-01T10:00:00Z")
        val chatB = JSONObject()
            .put("id", "chat_b")
            .put("updated_at", "2026-08-01T09:00:00Z")

        val sessions = recentSessionsForProjects(
            projects = listOf(projectA, projectB),
            projectChats = mapOf(
                "project_a" to listOf(chatA),
                "project_b" to listOf(chatB),
            ),
            projectTasks = mapOf("project_a" to listOf(taskA)),
        )

        assertEquals(listOf("task_a", "chat_b", "chat_a"), sessions.map {
            it.data.getString("id")
        })
        assertEquals(listOf("Alpha", "Beta", "Alpha"), sessions.map {
            it.project.getString("name")
        })
    }

    @Test
    fun mixesLocalChatsWithRemoteSessionsByTheSameTimestampRule() {
        val local = JSONObject().put("id", LOCAL_PROJECT_ID).put("name", "Local")
        val remote = JSONObject().put("id", "project_remote").put("name", "Remote")
        val localChat = JSONObject()
            .put("id", "ls_1")
            .put("local", true)
            .put("updatedAt", "2026-08-01T11:00:00Z")
        val remoteChat = JSONObject()
            .put("id", "chat_1")
            .put("updated_at", "2026-08-01T10:00:00Z")

        val sessions = recentSessionsForProjects(
            projects = listOf(local, remote),
            projectChats = mapOf(
                LOCAL_PROJECT_ID to listOf(localChat),
                "project_remote" to listOf(remoteChat),
            ),
            projectTasks = emptyMap(),
        )

        assertEquals(listOf("ls_1", "chat_1"), sessions.map { it.data.getString("id") })
        assertEquals(listOf("Local", "Remote"), sessions.map { it.project.getString("name") })
    }
}
