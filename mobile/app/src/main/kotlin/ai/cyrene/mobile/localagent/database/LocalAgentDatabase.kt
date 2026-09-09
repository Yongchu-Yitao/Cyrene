package ai.cyrene.mobile.localagent.database

import android.content.ContentValues
import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import ai.cyrene.mobile.localagent.model.RunState
import ai.cyrene.mobile.localagent.model.StopReason
import ai.cyrene.mobile.localagent.runtime.RunBudget
import ai.cyrene.mobile.localagent.runtime.RunSnapshot
import org.json.JSONObject

data class LocalSessionEntity(
    val sessionId: String, val title: String, val executionTarget: String,
    val tombstone: Boolean = false,
    val createdAt: String = System.currentTimeMillis().toString(),
    val updatedAt: String = createdAt,
)
data class ToolCallEntity(
    val callId: String, val runId: String, val name: String, val schemaHash: String,
    val argsHash: String, val executor: String, val status: String, val resultJson: String?,
)
data class LocalMessageEntity(
    val messageId: String, val sessionId: String, val role: String,
    val content: String, val createdAt: String,
    val attachmentsJson: String = "[]",
)
data class LocalTraceEntity(
    val runId: String?,
    val type: String,
    val payloadJson: String,
    val createdAt: String,
)

class LocalAgentDatabase private constructor(context: Context) :
    SQLiteOpenHelper(context.applicationContext, DATABASE_NAME, null, SCHEMA_VERSION) {

    private val localDao = LocalAgentDao(this)
    override fun onConfigure(db: SQLiteDatabase) {
        db.enableWriteAheadLogging()
        db.setForeignKeyConstraintsEnabled(true)
    }

    override fun onCreate(db: SQLiteDatabase) = createSchema(db)

    override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) {
        var version = oldVersion
        while (version < newVersion) {
            when (version) {
                1 -> migrateToDeviceOnlySessions(db)
                2 -> migrateToTimestampedSessions(db)
                3 -> migrateToMessageAttachments(db)
                else -> error("Missing Local Agent database migration $version -> ${version + 1}")
            }
            version += 1
        }
    }

    fun dao(): LocalAgentDao = localDao

    companion object {
        private const val DATABASE_NAME = "cyrene-local-agent.db"
        private const val SCHEMA_VERSION = 4
        @Volatile private var instance: LocalAgentDatabase? = null
        fun open(context: Context): LocalAgentDatabase = instance ?: synchronized(this) {
            instance ?: LocalAgentDatabase(context).also { instance = it }
        }

        private fun createSchema(db: SQLiteDatabase) {
            SCHEMA.forEach(db::execSQL)
        }

        private fun migrateToDeviceOnlySessions(db: SQLiteDatabase) {
            db.execSQL("ALTER TABLE local_sessions RENAME TO local_sessions_v1")
            db.execSQL("CREATE TABLE local_sessions(session_id TEXT PRIMARY KEY,title TEXT NOT NULL,execution_target TEXT NOT NULL,tombstone INTEGER NOT NULL DEFAULT 0)")
            db.execSQL("INSERT INTO local_sessions(session_id,title,execution_target,tombstone) SELECT session_id,title,execution_target,tombstone FROM local_sessions_v1")
            db.execSQL("DROP TABLE local_sessions_v1")
            listOf(
                "bundle_components", "bundle_generations", "content_objects",
                "conversation_events", "entity_mutation_outbox", "entity_snapshot_rows",
                "sync_inbox", "sync_outbox", "usage_snapshots",
            ).forEach { db.execSQL("DROP TABLE IF EXISTS $it") }
        }

        private fun migrateToTimestampedSessions(db: SQLiteDatabase) {
            val now = System.currentTimeMillis().toString()
            db.execSQL("ALTER TABLE local_sessions ADD COLUMN created_at TEXT NOT NULL DEFAULT '$now'")
            db.execSQL("ALTER TABLE local_sessions ADD COLUMN updated_at TEXT NOT NULL DEFAULT '$now'")
            db.execSQL(
                "UPDATE local_sessions SET updated_at=COALESCE(" +
                    "(SELECT MAX(messages.created_at) FROM messages " +
                    "WHERE messages.session_id=local_sessions.session_id), created_at)",
            )
        }

        private fun migrateToMessageAttachments(db: SQLiteDatabase) {
            db.execSQL(
                "ALTER TABLE messages ADD COLUMN attachments_json TEXT NOT NULL DEFAULT '[]'",
            )
        }

        private val SCHEMA = listOf(
            """CREATE TABLE local_sessions(session_id TEXT PRIMARY KEY,title TEXT NOT NULL,execution_target TEXT NOT NULL,tombstone INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)""",
            """CREATE TABLE messages(message_id TEXT PRIMARY KEY,event_id TEXT NOT NULL,session_id TEXT NOT NULL,role TEXT NOT NULL,content TEXT NOT NULL,created_at TEXT NOT NULL,attachments_json TEXT NOT NULL DEFAULT '[]')""",
            """CREATE TABLE runs(run_id TEXT PRIMARY KEY,session_id TEXT NOT NULL,state TEXT NOT NULL,bundle_generation INTEGER NOT NULL,lease_id TEXT,budget_json TEXT NOT NULL,stop_reason TEXT,updated_at TEXT NOT NULL)""",
            """CREATE TABLE model_turns(model_turn_id TEXT PRIMARY KEY,run_id TEXT NOT NULL,phase TEXT NOT NULL,idempotency_key TEXT NOT NULL,provider_state_ref TEXT,event_cursor INTEGER NOT NULL,state TEXT NOT NULL)""",
            """CREATE TABLE tool_calls(call_id TEXT PRIMARY KEY,run_id TEXT NOT NULL,name TEXT NOT NULL,schema_hash TEXT NOT NULL,args_hash TEXT NOT NULL,executor TEXT NOT NULL,status TEXT NOT NULL,result_json TEXT)""",
            """CREATE TABLE mutation_journal(journal_id TEXT PRIMARY KEY,call_id TEXT NOT NULL,phase TEXT NOT NULL,idempotency_key TEXT NOT NULL,request_id TEXT,result_ref TEXT,compensation_json TEXT)""",
            """CREATE TABLE plans(plan_id TEXT PRIMARY KEY,run_id TEXT NOT NULL,revision INTEGER NOT NULL,objective TEXT NOT NULL,status TEXT NOT NULL,approval_hash TEXT)""",
            """CREATE TABLE plan_steps(plan_id TEXT NOT NULL,step_id TEXT NOT NULL,position INTEGER NOT NULL,description TEXT NOT NULL,status TEXT NOT NULL,PRIMARY KEY(plan_id,step_id))""",
            """CREATE TABLE approvals(approval_id TEXT PRIMARY KEY,run_id TEXT NOT NULL,tool_call_id TEXT NOT NULL,capability_id TEXT NOT NULL,args_hash TEXT NOT NULL,generation INTEGER NOT NULL,risk TEXT NOT NULL,decision TEXT NOT NULL,expires_at_epoch_ms INTEGER NOT NULL,decided_by TEXT)""",
            """CREATE TABLE subagents(agent_id TEXT PRIMARY KEY,run_id TEXT NOT NULL,parent_agent_id TEXT,state TEXT NOT NULL,generation INTEGER NOT NULL,depth INTEGER NOT NULL,workspace_path TEXT NOT NULL,mailbox_cursor INTEGER NOT NULL,budget_json TEXT NOT NULL)""",
            """CREATE TABLE mailbox_events(mailbox_event_id TEXT PRIMARY KEY,agent_id TEXT NOT NULL,sequence INTEGER NOT NULL,type TEXT NOT NULL,payload_json TEXT NOT NULL)""",
            """CREATE TABLE artifacts(artifact_id TEXT PRIMARY KEY,session_id TEXT NOT NULL,hash TEXT NOT NULL,media_type TEXT NOT NULL,path TEXT NOT NULL,size INTEGER NOT NULL,transfer_state TEXT NOT NULL)""",
            """CREATE TABLE artifact_chunks(artifact_id TEXT NOT NULL,chunk_index INTEGER NOT NULL,hash TEXT NOT NULL,state TEXT NOT NULL,PRIMARY KEY(artifact_id,chunk_index))""",
            """CREATE TABLE runtime_sessions(session_id TEXT PRIMARY KEY,image_version TEXT NOT NULL,overlay_path TEXT NOT NULL,state TEXT NOT NULL,resource_json TEXT NOT NULL)""",
            """CREATE TABLE traces(trace_id TEXT PRIMARY KEY,session_id TEXT NOT NULL,run_id TEXT,type TEXT NOT NULL,redacted_json TEXT NOT NULL,created_at TEXT NOT NULL)""",
        )
    }
}

class LocalAgentDao(private val helper: LocalAgentDatabase) {
    suspend fun putSession(value: LocalSessionEntity) {
        helper.writableDatabase.insertWithOnConflict("local_sessions", null, ContentValues().apply {
            put("session_id", value.sessionId); put("title", value.title)
            put("execution_target", value.executionTarget); put("tombstone", value.tombstone)
            put("created_at", value.createdAt); put("updated_at", value.updatedAt)
        }, SQLiteDatabase.CONFLICT_REPLACE)
    }

    suspend fun session(sessionId: String): LocalSessionEntity? = helper.readableDatabase.query(
        "local_sessions", null, "session_id=?", arrayOf(sessionId), null, null, null, "1",
    ).use { cursor -> if (cursor.moveToFirst()) cursor.session() else null }

    suspend fun sessions(): List<LocalSessionEntity> = helper.readableDatabase.query(
        "local_sessions", null, "tombstone=0", null, null, null,
        "CAST(updated_at AS INTEGER) DESC",
    ).use { cursor -> buildList { while (cursor.moveToNext()) add(cursor.session()) } }

    suspend fun renameSession(sessionId: String, title: String): Int =
        helper.writableDatabase.update(
            "local_sessions",
            ContentValues().apply {
                put("title", title)
                put("updated_at", System.currentTimeMillis().toString())
            },
            "session_id=? AND tombstone=0",
            arrayOf(sessionId),
        )

    suspend fun deleteSession(sessionId: String): Int =
        helper.writableDatabase.update(
            "local_sessions",
            ContentValues().apply {
                put("tombstone", 1)
                put("updated_at", System.currentTimeMillis().toString())
            },
            "session_id=?",
            arrayOf(sessionId),
        )

    suspend fun saveMessage(
        sessionId: String,
        role: String,
        content: String,
        attachmentsJson: String = "[]",
    ): LocalMessageEntity {
        require(role in setOf("user", "assistant"))
        require(runCatching { org.json.JSONArray(attachmentsJson) }.isSuccess) {
            "attachments_json must be a JSON array"
        }
        val value = LocalMessageEntity(
            "msg_${java.util.UUID.randomUUID().toString().replace("-", "")}",
            sessionId, role, content.take(500_000), System.currentTimeMillis().toString(),
            attachmentsJson,
        )
        helper.writableDatabase.insertOrThrow("messages", null, ContentValues().apply {
            put("message_id", value.messageId); put("event_id", value.messageId)
            put("session_id", value.sessionId); put("role", value.role)
            put("content", value.content); put("created_at", value.createdAt)
            put("attachments_json", value.attachmentsJson)
        })
        helper.writableDatabase.update(
            "local_sessions",
            ContentValues().apply { put("updated_at", value.createdAt) },
            "session_id=?",
            arrayOf(sessionId),
        )
        return value
    }

    suspend fun messages(sessionId: String, limit: Int = 100): List<LocalMessageEntity> =
        helper.readableDatabase.rawQuery(
            "SELECT message_id,session_id,role,content,created_at,attachments_json FROM " +
                "(SELECT * FROM messages WHERE session_id=? ORDER BY CAST(created_at AS INTEGER) DESC LIMIT ?) " +
                "ORDER BY CAST(created_at AS INTEGER) ASC",
            arrayOf(sessionId, limit.coerceIn(1, 500).toString()),
        ).use { cursor -> buildList {
            while (cursor.moveToNext()) add(LocalMessageEntity(
                cursor.text("message_id"), cursor.text("session_id"), cursor.text("role"),
                cursor.text("content"), cursor.text("created_at"), cursor.text("attachments_json"),
            ))
        } }

    suspend fun messageCount(sessionId: String): Int = helper.readableDatabase.rawQuery(
        "SELECT COUNT(*) FROM messages WHERE session_id=?",
        arrayOf(sessionId),
    ).use { cursor -> if (cursor.moveToFirst()) cursor.getInt(0) else 0 }

    suspend fun insertToolCall(value: ToolCallEntity) {
        val result = helper.writableDatabase.insertOrThrow("tool_calls", null, ContentValues().apply {
            put("call_id", value.callId); put("run_id", value.runId); put("name", value.name); put("schema_hash", value.schemaHash)
            put("args_hash", value.argsHash); put("executor", value.executor); put("status", value.status); put("result_json", value.resultJson)
        })
        check(result != -1L)
    }

    suspend fun finishToolCallOnce(callId: String, status: String, resultJson: String): Int = helper.writableDatabase.update(
        "tool_calls", ContentValues().apply { put("status", status); put("result_json", resultJson) }, "call_id=? AND result_json IS NULL", arrayOf(callId),
    )

    suspend fun saveRun(snapshot: RunSnapshot) {
        helper.writableDatabase.insertWithOnConflict("runs", null, ContentValues().apply {
            put("run_id", snapshot.runId); put("session_id", snapshot.sessionId); put("state", snapshot.state.name)
            put("bundle_generation", snapshot.generation); put("budget_json", snapshot.budget.toJson().toString())
            put("stop_reason", snapshot.stopReason?.wireName); put("updated_at", System.currentTimeMillis().toString())
        }, SQLiteDatabase.CONFLICT_REPLACE)
    }

    suspend fun run(runId: String): RunSnapshot? = helper.readableDatabase.query(
        "runs", null, "run_id=?", arrayOf(runId), null, null, null, "1",
    ).use { cursor ->
        if (!cursor.moveToFirst()) null else RunSnapshot(
            runId = cursor.text("run_id"), sessionId = cursor.text("session_id"), generation = cursor.long("bundle_generation"),
            state = RunState.valueOf(cursor.text("state")), budget = runBudgetFromJson(JSONObject(cursor.text("budget_json"))),
            stopReason = cursor.getColumnIndexOrThrow("stop_reason").let { if (cursor.isNull(it)) null else StopReason.entries.firstOrNull { reason -> reason.wireName == cursor.getString(it) } },
        )
    }

    suspend fun latestRun(sessionId: String): RunSnapshot? = helper.readableDatabase.query(
        "runs", arrayOf("run_id"), "session_id=?", arrayOf(sessionId), null, null, "updated_at DESC", "1",
    ).use { cursor -> if (!cursor.moveToFirst()) null else run(cursor.text("run_id")) }

    suspend fun traces(sessionId: String, limit: Int = 100): List<LocalTraceEntity> = helper.readableDatabase.query(
        "traces", arrayOf("run_id", "type", "redacted_json", "created_at"), "session_id=?", arrayOf(sessionId), null, null,
        "created_at DESC", limit.coerceIn(1, 500).toString(),
    ).use { cursor -> buildList {
        while (cursor.moveToNext()) add(
            LocalTraceEntity(
                cursor.getColumnIndexOrThrow("run_id").let { if (cursor.isNull(it)) null else cursor.getString(it) },
                cursor.text("type"), cursor.text("redacted_json"), cursor.text("created_at"),
            ),
        )
    } }

    suspend fun appendTrace(sessionId: String, runId: String?, type: String, redactedJson: String) {
        helper.writableDatabase.insertOrThrow("traces", null, ContentValues().apply {
            put("trace_id", "trace_${java.util.UUID.randomUUID().toString().replace("-", "")}")
            put("session_id", sessionId); put("run_id", runId); put("type", type)
            put("redacted_json", redactedJson.take(256 * 1024)); put("created_at", System.currentTimeMillis().toString())
        })
    }

}

private fun android.database.Cursor.text(name: String) = getString(getColumnIndexOrThrow(name))
private fun android.database.Cursor.long(name: String) = getLong(getColumnIndexOrThrow(name))
private fun android.database.Cursor.session() = LocalSessionEntity(
    text("session_id"), text("title"), text("execution_target"), long("tombstone") != 0L,
    text("created_at"), text("updated_at"),
)

private fun RunBudget.toJson() = JSONObject()
    .put("remaining_model_turns", remainingModelTurns).put("remaining_tool_calls", remainingToolCalls)
    .put("remaining_wall_time_ms", remainingWallTimeMs).put("remaining_input_tokens", remainingInputTokens)
    .put("remaining_output_tokens", remainingOutputTokens).put("remaining_cost_micros", remainingCostMicros)
    .put("max_subagents", maxSubagents).put("max_subagent_depth", maxSubagentDepth)

private fun runBudgetFromJson(value: JSONObject) = RunBudget(
    value.getInt("remaining_model_turns"), value.getInt("remaining_tool_calls"), value.getLong("remaining_wall_time_ms"),
    value.getLong("remaining_input_tokens"), value.getLong("remaining_output_tokens"), value.getLong("remaining_cost_micros"),
    value.getInt("max_subagents"), value.getInt("max_subagent_depth"),
)
