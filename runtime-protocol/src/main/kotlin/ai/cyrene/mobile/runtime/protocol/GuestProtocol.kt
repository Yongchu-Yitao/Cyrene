package ai.cyrene.mobile.runtime.protocol

import org.json.JSONObject

const val GUEST_PROTOCOL_VERSION = 1
const val MAX_GUEST_MESSAGE_BYTES = 1_048_576

enum class GuestOperation(val wireName: String) {
    HELLO("hello"), SESSION_MOUNT("session_mount"), SESSION_UNMOUNT("session_unmount"),
    EXEC_START("exec_start"), EXEC_STDIN("exec_stdin"), EXEC_SIGNAL("exec_signal"), EXEC_WAIT("exec_wait"),
    FS_STAT("fs_stat"), FS_LIST("fs_list"), FS_READ("fs_read"), FS_WRITE("fs_write"),
    FS_WRITE_CHUNK("fs_write_chunk"), FS_PATCH("fs_patch"),
    FS_GLOB("fs_glob"), FS_GREP("fs_grep"), ARTIFACT_EXPORT("artifact_export"),
    DESKTOP_START("desktop_start"), DESKTOP_STATUS("desktop_status"), DESKTOP_STOP("desktop_stop"),
    RESOURCE_USAGE("resource_usage"), HEALTH_CHECK("health_check"), SHUTDOWN("shutdown");

    companion object {
        fun parse(value: String): GuestOperation = entries.firstOrNull { it.wireName == value }
            ?: throw GuestProtocolException("unsupported_operation", "Unsupported guest operation")
    }
}

data class GuestRequest(
    val requestId: String,
    val sessionId: String,
    val subagentId: String?,
    val operation: GuestOperation,
    val deadlineEpochMs: Long,
    val sequence: Long,
    val payload: JSONObject,
) {
    fun toJson(): String = JSONObject()
        .put("protocol", GUEST_PROTOCOL_VERSION).put("request_id", requestId)
        .put("session_id", sessionId).put("subagent_id", subagentId ?: "")
        .put("operation", operation.wireName).put("deadline_epoch_ms", deadlineEpochMs)
        .put("sequence", sequence).put("payload", payload).toString()

    companion object {
        fun parse(raw: String): GuestRequest {
            if (raw.toByteArray().size > MAX_GUEST_MESSAGE_BYTES) throw GuestProtocolException("message_too_large", "Guest request exceeds hard limit")
            val value = JSONObject(raw)
            val expected = setOf("protocol", "request_id", "session_id", "subagent_id", "operation", "deadline_epoch_ms", "sequence", "payload")
            val actual = value.keys().asSequence().toSet()
            if (actual != expected) throw GuestProtocolException("invalid_fields", "Guest request fields do not match schema")
            if (value.getInt("protocol") != GUEST_PROTOCOL_VERSION) throw GuestProtocolException("protocol_mismatch", "Guest protocol upgrade required")
            val sessionId = value.getString("session_id")
            if (!sessionId.startsWith("ls_")) throw GuestProtocolException("invalid_session", "Local session id required")
            val deadline = value.getLong("deadline_epoch_ms")
            if (deadline <= System.currentTimeMillis()) throw GuestProtocolException("deadline_expired", "Guest request deadline expired")
            return GuestRequest(
                value.getString("request_id"), sessionId,
                value.optString("subagent_id").ifBlank { null },
                GuestOperation.parse(value.getString("operation")), deadline,
                value.getLong("sequence"), value.getJSONObject("payload"),
            )
        }
    }
}

data class GuestResponse(
    val requestId: String,
    val status: String,
    val payload: JSONObject = JSONObject(),
    val errorType: String? = null,
    val message: String? = null,
) {
    fun toJson(): String = JSONObject()
        .put("protocol", GUEST_PROTOCOL_VERSION)
        .put("request_id", requestId)
        .put("status", status)
        .put("payload", payload)
        .put("error_type", errorType ?: JSONObject.NULL)
        .put("message", message ?: JSONObject.NULL)
        .toString()

    companion object {
        fun parse(raw: String, expectedRequestId: String? = null): GuestResponse {
            if (raw.toByteArray().size > MAX_GUEST_MESSAGE_BYTES) throw GuestProtocolException("message_too_large", "Guest response exceeds hard limit")
            val value = JSONObject(raw)
            val expected = setOf("protocol", "request_id", "status", "payload", "error_type", "message")
            if (value.keys().asSequence().toSet() != expected) throw GuestProtocolException("invalid_fields", "Guest response fields do not match schema")
            if (value.getInt("protocol") != GUEST_PROTOCOL_VERSION) throw GuestProtocolException("protocol_mismatch", "Guest protocol upgrade required")
            val requestId = value.getString("request_id")
            if (expectedRequestId != null && requestId != expectedRequestId) throw GuestProtocolException("request_mismatch", "Guest response request id mismatch")
            val status = value.getString("status")
            if (status !in setOf("success", "error", "cancelled", "timeout")) throw GuestProtocolException("invalid_status", "Guest response status is invalid")
            fun nullableString(key: String): String? = if (value.isNull(key)) null else value.getString(key)
            return GuestResponse(requestId, status, value.getJSONObject("payload"), nullableString("error_type"), nullableString("message"))
        }
    }
}

class GuestProtocolException(val code: String, override val message: String) : IllegalArgumentException(message)
