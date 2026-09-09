package ai.cyrene.mobile.localagent

import ai.cyrene.mobile.localagent.protocol.LocalAgentProtocol
import ai.cyrene.mobile.runtime.protocol.GuestRequest
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test
import java.io.File

class GoldenContractTest {
    private val root = File(System.getProperty("user.dir"), "../protocol/local-agent/v1").canonicalFile

    @Test fun providerEventAndGuestRequestFixturesRemainStrictlyReadable() {
        val event = LocalAgentProtocol.parseModelEvent(JSONObject(root.resolve("model-event.example.json").readText()))
        assertEquals("mt_example", event.modelTurnId)
        val guest = GuestRequest.parse(root.resolve("guest-request.example.json").readText())
        assertEquals("ls_example", guest.sessionId)
    }

    @Test fun fixedCommandMatrixHasNoGenericExecutionEscapeHatch() {
        val value = JSONObject(root.resolve("command-matrix.json").readText())
        assertFalse(value.getBoolean("arbitrary_command"))
        val desktopCommands = value.getJSONArray("desktop_commands")
        val localCommands = value.getJSONArray("local_session_commands")
        assertEquals(2, desktopCommands.length())
        assertEquals("settings.models.copy", desktopCommands.getString(0))
        assertEquals("settings.update", desktopCommands.getString(1))
        assertEquals(0, localCommands.length())
        for (index in 0 until desktopCommands.length()) {
            assertFalse(desktopCommands.getString(index).endsWith(".execute"))
        }
        val contracts = value.getJSONObject("payload_contracts")
        assertEquals(
            "model-configuration.example.json",
            contracts.getJSONObject("settings.models.copy").getString("response_models"),
        )
        assertEquals(
            "model-configuration.example.json",
            contracts.getJSONObject("settings.update").getString("request_models"),
        )
    }

    @Test fun copiedModelConfigurationUsesCanonicalGraph() {
        val value = JSONObject(root.resolve("model-configuration.example.json").readText())
        assertEquals(
            setOf("version", "revision", "connections", "profiles", "routes"),
            value.keys().asSequence().toSet(),
        )
        assertEquals(
            setOf("primary", "secondary", "vision", "embedding"),
            value.getJSONObject("routes").keys().asSequence().toSet(),
        )
        val profile = value.getJSONArray("profiles").getJSONObject(0)
        val connection = value.getJSONArray("connections").getJSONObject(0)
        assertEquals(connection.getString("id"), profile.getString("connection_id"))
        assertEquals(
            profile.getString("id"),
            value.getJSONObject("routes").getJSONArray("primary").getString(0),
        )
    }
}
