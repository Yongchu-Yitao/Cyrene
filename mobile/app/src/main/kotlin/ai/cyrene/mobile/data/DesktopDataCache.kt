package ai.cyrene.mobile.data

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

internal data class CachedProjectData(
    val chats: List<JSONObject> = emptyList(),
    val tasks: List<JSONObject> = emptyList(),
)

internal data class DesktopDataSnapshot(
    val projects: List<JSONObject> = emptyList(),
    val projectData: Map<String, CachedProjectData> = emptyMap(),
    val chatDetails: Map<String, JSONObject> = emptyMap(),
    val taskDetails: Map<String, JSONObject> = emptyMap(),
    val taskArtifacts: Map<String, List<JSONObject>> = emptyMap(),
) {
    fun toJson(): JSONObject = JSONObject()
        .put("version", CACHE_VERSION)
        .put("projects", JSONArray(projects))
        .put(
            "project_data",
            JSONObject().also { output ->
                projectData.forEach { (projectId, data) ->
                    output.put(
                        projectId,
                        JSONObject()
                            .put("chats", JSONArray(data.chats))
                            .put("tasks", JSONArray(data.tasks)),
                    )
                }
            },
        )
        .put("chat_details", jsonObjectMap(chatDetails))
        .put("task_details", jsonObjectMap(taskDetails))
        .put(
            "task_artifacts",
            JSONObject().also { output ->
                taskArtifacts.forEach { (taskId, artifacts) ->
                    output.put(taskId, JSONArray(artifacts))
                }
            },
        )

    companion object {
        private const val CACHE_VERSION = 1

        fun fromJson(raw: JSONObject): DesktopDataSnapshot {
            require(raw.optInt("version") == CACHE_VERSION)
            val projectDataObject = raw.optJSONObject("project_data") ?: JSONObject()
            val projectData = projectDataObject.keys().asSequence().associateWith { projectId ->
                val data = projectDataObject.optJSONObject(projectId) ?: JSONObject()
                CachedProjectData(
                    chats = data.optJSONArray("chats").objects(),
                    tasks = data.optJSONArray("tasks").objects(),
                )
            }
            val artifactsObject = raw.optJSONObject("task_artifacts") ?: JSONObject()
            return DesktopDataSnapshot(
                projects = raw.optJSONArray("projects").objects(),
                projectData = projectData,
                chatDetails = raw.optJSONObject("chat_details").objectMap(),
                taskDetails = raw.optJSONObject("task_details").objectMap(),
                taskArtifacts = artifactsObject.keys().asSequence().associateWith { taskId ->
                    artifactsObject.optJSONArray(taskId).objects()
                },
            )
        }

        private fun jsonObjectMap(values: Map<String, JSONObject>): JSONObject =
            JSONObject().also { output ->
                values.forEach { (key, value) -> output.put(key, value) }
            }
    }
}

internal class DesktopDataCache(context: Context) {
    private val directory = File(context.filesDir, "desktop-data")

    fun load(peerId: String): DesktopDataSnapshot? {
        val target = cacheFile(peerId)
        if (!target.isFile) return null
        return runCatching {
            DesktopDataSnapshot.fromJson(JSONObject(target.readText()))
        }.getOrNull()
    }

    fun save(peerId: String, snapshot: DesktopDataSnapshot) {
        directory.mkdirs()
        val target = cacheFile(peerId)
        val temporary = File(directory, "${target.name}.tmp")
        temporary.writeText(snapshot.toJson().toString())
        if (!temporary.renameTo(target)) {
            target.delete()
            check(temporary.renameTo(target)) { "Could not publish desktop data cache" }
        }
    }

    fun clear(peerId: String) {
        cacheFile(peerId).delete()
    }

    private fun cacheFile(peerId: String): File {
        val safeId = peerId.replace(Regex("[^A-Za-z0-9_-]"), "_").take(96)
        return File(directory, "${safeId.ifBlank { "desktop" }}.json")
    }
}

private fun JSONArray?.objects(): List<JSONObject> =
    if (this == null) emptyList() else (0 until length()).mapNotNull { optJSONObject(it) }

private fun JSONObject?.objectMap(): Map<String, JSONObject> {
    if (this == null) return emptyMap()
    return keys().asSequence().mapNotNull { key ->
        optJSONObject(key)?.let { key to it }
    }.toMap()
}
