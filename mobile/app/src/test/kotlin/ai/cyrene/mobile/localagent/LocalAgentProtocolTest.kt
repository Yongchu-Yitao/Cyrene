package ai.cyrene.mobile.localagent

import ai.cyrene.mobile.localagent.model.MODEL_EVENT_SCHEMA
import ai.cyrene.mobile.localagent.model.ProtocolViolation
import ai.cyrene.mobile.localagent.protocol.EventAcceptance
import ai.cyrene.mobile.localagent.protocol.LocalAgentProtocol
import ai.cyrene.mobile.localagent.protocol.OrderedModelEventReducer
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.fail
import org.junit.Test

class LocalAgentProtocolTest {
    @Test fun modelEventsAreGapCheckedAndOnlyKnownTypesAccepted() {
        fun event(sequence: Long) = LocalAgentProtocol.parseModelEvent(
            JSONObject().put("schema", MODEL_EVENT_SCHEMA).put("model_turn_id", "mt_1")
                .put("sequence", sequence).put("type", "message.delta")
                .put("payload", JSONObject().put("delta", "x"))
        )
        val reducer = OrderedModelEventReducer()
        assertEquals(EventAcceptance.ACCEPTED, reducer.accept(event(1)))
        assertEquals(EventAcceptance.DUPLICATE, reducer.accept(event(1)))
        assertEquals(EventAcceptance.GAP, reducer.accept(event(3)))
        assertEquals(EventAcceptance.ACCEPTED, reducer.accept(event(2)))
    }

    @Test fun modelEventsRejectUnknownFieldsAndTypes() {
        val value = JSONObject().put("schema", MODEL_EVENT_SCHEMA).put("model_turn_id", "mt_1")
            .put("sequence", 1).put("type", "unknown.event").put("payload", JSONObject())
        assertEquals("unknown_model_event", assertFailsWith<ProtocolViolation> {
            LocalAgentProtocol.parseModelEvent(value)
        }.code)
        value.put("type", "turn.started").put("unexpected", true)
        assertEquals("invalid_model_event_fields", assertFailsWith<ProtocolViolation> {
            LocalAgentProtocol.parseModelEvent(value)
        }.code)
    }

    private inline fun <reified T : Throwable> assertFailsWith(block: () -> Unit): T {
        try {
            block()
        } catch (error: Throwable) {
            if (error is T) return error
            throw error
        }
        fail("Expected ${T::class.java.simpleName}")
        throw AssertionError()
    }
}
