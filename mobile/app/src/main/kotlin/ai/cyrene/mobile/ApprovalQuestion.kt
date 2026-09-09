package ai.cyrene.mobile

import org.json.JSONArray
import org.json.JSONObject

data class ApprovalOption(
    val id: String,
    val label: String,
)

data class ApprovalQuestion(
    val id: String,
    val title: String,
    val prompt: String,
    val kind: String,
    val options: List<ApprovalOption>,
    val allowCustom: Boolean,
)

private fun permissionKind(value: JSONObject): String = value.optString("kind").ifBlank {
    value.optString("questionKind").ifBlank {
        value.optJSONObject("meta")?.optString("kind").orEmpty()
    }
}

fun isPermissionQuestion(kind: String): Boolean =
    kind.contains("permission", ignoreCase = true) || kind in setOf(
        "scope_elevation",
        "read_elevation",
        "destructive_confirmation",
        "external_delivery_request",
        "process_execution",
        "external_tool_execution",
        "task_permission_request",
        "remote_harness_invoke",
    )

private fun normalizedOptions(value: JSONObject, kind: String): List<ApprovalOption> =
    if (isPermissionQuestion(kind)) {
        listOf(
            ApprovalOption(id = "approve_session", label = "在本次会话同意"),
            ApprovalOption(id = "approve_once", label = "同意一次"),
            ApprovalOption(id = "deny", label = "拒绝"),
        )
    } else {
        parseApprovalOptions(value.optJSONArray("options") ?: value.optJSONArray("choices"))
    }

fun pendingApprovalQuestion(
    chat: JSONObject?,
    runEvents: List<JSONObject> = emptyList(),
): ApprovalQuestion? {
    val direct = chat?.optJSONObject("pending_question")
        ?: chat?.optJSONObject("pendingQuestion")
    parseApprovalQuestion(direct)?.let { return it }

    runEvents.asReversed().forEach { event ->
        if (event.optString("type") == "awaiting_user") {
            parseApprovalQuestion(
                event.optJSONObject("pending_question")
                    ?: event.optJSONObject("pendingQuestion")
            )?.let { return it }
        }
    }

    val messages = chat?.optJSONArray("messages") ?: return null
    for (index in messages.length() - 1 downTo 0) {
        val message = messages.optJSONObject(index) ?: continue
        val questionId = message.optString("question_id").ifBlank {
            message.optString("questionId")
        }
        if (questionId.isBlank()) continue
        val kind = message.optString("question_kind").ifBlank {
            message.optString("questionKind")
        }
        return ApprovalQuestion(
            id = questionId,
            title = "",
            prompt = message.optString("content").ifBlank { message.optString("text") },
            kind = kind,
            options = normalizedOptions(message, kind),
            allowCustom = !isPermissionQuestion(kind),
        )
    }
    return null
}

fun parseApprovalQuestion(value: JSONObject?): ApprovalQuestion? {
    value ?: return null
    val id = value.optString("id").ifBlank { value.optString("questionId") }
    if (id.isBlank()) return null
    val kind = permissionKind(value)
    return ApprovalQuestion(
        id = id,
        title = value.optString("title"),
        prompt = value.optString("text").ifBlank {
            value.optString("prompt").ifBlank { value.optString("question") }
        },
        kind = kind,
        options = normalizedOptions(value, kind),
        allowCustom = if (isPermissionQuestion(kind)) false else when {
            value.has("allowCustom") -> value.optBoolean("allowCustom")
            value.has("allow_custom") -> value.optBoolean("allow_custom")
            else -> true
        },
    )
}

private fun parseApprovalOptions(values: JSONArray?): List<ApprovalOption> {
    if (values == null) return emptyList()
    return (0 until values.length()).mapNotNull { index ->
        when (val raw = values.opt(index)) {
            is JSONObject -> {
                val label = raw.optString("label").ifBlank {
                    raw.optString("text").ifBlank { raw.optString("value") }
                }
                label.takeIf(String::isNotBlank)?.let {
                    ApprovalOption(
                        id = raw.optString("id").ifBlank { "option_${index + 1}" },
                        label = it,
                    )
                }
            }
            is String -> raw.trim().takeIf(String::isNotBlank)?.let {
                ApprovalOption(id = "option_${index + 1}", label = it)
            }
            else -> null
        }
    }
}
