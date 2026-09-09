package ai.cyrene.mobile.localagent.database

import ai.cyrene.mobile.localagent.runtime.DurableRunStore
import ai.cyrene.mobile.localagent.runtime.RunSnapshot
import ai.cyrene.mobile.localagent.runtime.RuntimeEventSink
import org.json.JSONObject

/** Durable bridge used by the foreground-service-owned orchestrator. */
class DatabaseRuntimeStore(
    private val dao: LocalAgentDao,
    private val sessionId: String,
) : DurableRunStore, RuntimeEventSink {
    private var currentRunId: String? = null

    override suspend fun load(runId: String): RunSnapshot? = dao.run(runId)

    override suspend fun save(snapshot: RunSnapshot) {
        currentRunId = snapshot.runId
        dao.saveRun(snapshot)
    }

    override suspend fun appendEvent(runId: String, type: String, payload: JSONObject) {
        dao.appendTrace(sessionId, runId, type, redact(payload).toString())
    }

    override suspend fun checkpoint(snapshot: RunSnapshot) = save(snapshot)

    override suspend fun event(type: String, payload: JSONObject) {
        dao.appendTrace(sessionId, currentRunId, type, redact(payload).toString())
    }

    private fun redact(value: JSONObject): JSONObject {
        val blocked = setOf("api_key", "token", "cookie", "authorization", "hidden_reasoning")
        return JSONObject().also { output ->
            value.keys().forEach { key ->
                output.put(key, if (key.lowercase() in blocked) "[REDACTED]" else value.get(key))
            }
        }
    }
}
