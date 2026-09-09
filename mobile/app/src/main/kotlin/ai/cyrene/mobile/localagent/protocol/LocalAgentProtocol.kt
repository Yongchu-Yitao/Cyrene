package ai.cyrene.mobile.localagent.protocol

import ai.cyrene.mobile.localagent.model.*
import org.json.JSONObject

object LocalAgentProtocol {
    fun parseModelEvent(value: JSONObject): ModelEvent {
        requireExactFields(value, setOf("schema", "model_turn_id", "sequence", "type", "payload"), "model_event")
        if (value.getString("schema") != MODEL_EVENT_SCHEMA) {
            throw ProtocolViolation("unsupported_model_event_schema", "Unsupported model event schema")
        }
        return ModelEvent(
            modelTurnId = value.getString("model_turn_id"),
            sequence = value.getLong("sequence"),
            type = ModelEventType.fromWire(value.getString("type")),
            payload = value.getJSONObject("payload").toString(),
        )
    }

    private fun requireExactFields(value: JSONObject, expected: Set<String>, label: String) {
        val actual = value.keys().asSequence().toSet()
        val unknown = actual - expected
        val missing = expected - actual
        if (unknown.isNotEmpty() || missing.isNotEmpty()) {
            throw ProtocolViolation(
                "invalid_${label}_fields",
                "$label fields mismatch; unknown=$unknown missing=$missing",
            )
        }
    }
}

class OrderedModelEventReducer {
    private val seen = mutableMapOf<String, Long>()

    fun accept(event: ModelEvent): EventAcceptance {
        val current = seen[event.modelTurnId] ?: 0L
        return when {
            event.sequence <= current -> EventAcceptance.DUPLICATE
            event.sequence != current + 1 -> EventAcceptance.GAP
            else -> {
                seen[event.modelTurnId] = event.sequence
                EventAcceptance.ACCEPTED
            }
        }
    }

    fun cursor(modelTurnId: String): Long = seen[modelTurnId] ?: 0L
}

enum class EventAcceptance { ACCEPTED, DUPLICATE, GAP }
