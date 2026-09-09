package ai.cyrene.mobile

import org.json.JSONObject
import java.time.Instant
import java.time.OffsetDateTime

internal data class RecentSession(
    val kind: String,
    val data: JSONObject,
    val project: JSONObject,
)

internal fun recentSessionsForProjects(
    projects: List<JSONObject>,
    projectChats: Map<String, List<JSONObject>>,
    projectTasks: Map<String, List<JSONObject>>,
): List<RecentSession> = projects.flatMap { project ->
    val projectId = project.optString("id")
    projectChats[projectId].orEmpty().map { RecentSession("chat", it, project) } +
        projectTasks[projectId].orEmpty().map { RecentSession("task", it, project) }
}.sortedByDescending { recentSessionTimestamp(it.data) }

internal fun recentSessionTimestamp(item: JSONObject): Long {
    val raw = item.optString("updated_at")
        .ifBlank { item.optString("updatedAt") }
        .ifBlank { item.optString("created_at") }
        .ifBlank { item.optString("createdAt") }
    return runCatching { Instant.parse(raw).toEpochMilli() }.getOrNull()
        ?: runCatching { OffsetDateTime.parse(raw).toInstant().toEpochMilli() }
            .getOrDefault(0L)
}
