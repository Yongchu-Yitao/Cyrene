package ai.cyrene.mobile.protocol

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ProtocolCompatibilityTest {
    private val identity = MobileIdentity(
        signingPrivate = ByteArray(32) { it.toByte() },
        exchangePrivate = ByteArray(32) { (it + 32).toByte() },
    )

    @Test
    fun canonicalJsonMatchesPythonEncoding() {
        val value = JSONObject()
            .put("version", 1)
            .put("message", "你好")
        assertEquals("""{"message":"你好","version":1}""", CanonicalJson.encode(value))
    }

    @Test
    fun identityMatchesDesktopPythonFixture() {
        assertEquals(
            "A6EHv_POEL4dcN0Y50vAmWfk1jCbpQ1fHdyGZBJVMbg",
            b64Url(identity.signingPublic),
        )
        assertEquals(
            "NYBy1jZYgNGu6jKa35EhODhR7SGijjt16WXQ0s0WYlQ",
            b64Url(identity.exchangePublic),
        )
        assertEquals("dev_Vkdap1RjR0wChd9dvyvKtz2m", identity.deviceId)
        assertEquals(
            "5647 5aa7 5463 474c 0285 df5d bf2b cab7",
            identity.fingerprint,
        )
    }

    @Test
    fun signatureMatchesDesktopPythonFixture() {
        val content = """{"message":"你好","version":1}""".toByteArray()
        val signature = identity.sign(content)
        assertEquals(
            "uwF3vyFuldhnq4m9jnnCOAU7_Vpb2rasPrzuPtjmU_iApJdpTy3tGHci2fCeysfHLVWtBahEJDXK5hc33eHHAg",
            b64Url(signature),
        )
        assertTrue(CyreneCrypto.verify(identity.signingPublic, content, signature))
    }

    @Test
    fun hkdfMatchesDesktopPythonFixture() {
        val peer = Peer(
            deviceId = "dev_peer_fixture",
            name = "Fixture",
            signingPublic = b64Url(identity.signingPublic),
            exchangePublic = "eaYx7t4b-cmPEgMs3q3Q56B5OY_HhriMyEbsia-FpRo",
            fingerprint = "",
            host = "127.0.0.1",
            port = 37841,
            capabilities = emptyList(),
            projectScopes = emptyList(),
        )
        val key = CyreneCrypto.deriveEnvelopeKey(
            identity, peer, identity.deviceId, peer.deviceId
        )
        assertEquals("MMOCI0vAX0kp1cU1YZq2592zPoHp0fV0o5tOv6Muk5c", b64Url(key))
    }
}
