package ai.cyrene.mobile.protocol

import ai.cyrene.mobile.R
import org.bouncycastle.crypto.digests.SHA256Digest
import org.bouncycastle.crypto.generators.HKDFBytesGenerator
import org.bouncycastle.crypto.modes.ChaCha20Poly1305
import org.bouncycastle.crypto.params.AEADParameters
import org.bouncycastle.crypto.params.Ed25519PrivateKeyParameters
import org.bouncycastle.crypto.params.Ed25519PublicKeyParameters
import org.bouncycastle.crypto.params.HKDFParameters
import org.bouncycastle.crypto.params.KeyParameter
import org.bouncycastle.crypto.params.X25519PrivateKeyParameters
import org.bouncycastle.crypto.params.X25519PublicKeyParameters
import org.bouncycastle.crypto.signers.Ed25519Signer
import org.json.JSONObject
import java.security.MessageDigest
import java.security.SecureRandom
import java.util.UUID
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

data class MobileIdentity(
    val signingPrivate: ByteArray,
    val exchangePrivate: ByteArray,
) {
    private val signing = Ed25519PrivateKeyParameters(signingPrivate, 0)
    private val exchange = X25519PrivateKeyParameters(exchangePrivate, 0)
    val signingPublic: ByteArray get() = signing.generatePublicKey().encoded
    val exchangePublic: ByteArray get() = exchange.generatePublicKey().encoded
    val deviceId: String
        get() = "dev_" + b64Url(sha256(signingPublic).copyOfRange(0, 18))
    val fingerprint: String
        get() = sha256(signingPublic).hex().take(32).chunked(4).joinToString(" ")

    fun sign(bytes: ByteArray): ByteArray {
        val signer = Ed25519Signer()
        signer.init(true, signing)
        signer.update(bytes, 0, bytes.size)
        return signer.generateSignature()
    }

    fun publicIdentity(name: String): JSONObject = JSONObject()
        .put("device_id", deviceId)
        .put("device_name", name)
        .put("signing_public_key", b64Url(signingPublic))
        .put("exchange_public_key", b64Url(exchangePublic))
        .put("fingerprint", fingerprint)

    fun sharedSecret(peerExchange: ByteArray): ByteArray {
        val result = ByteArray(32)
        exchange.generateSecret(
            X25519PublicKeyParameters(peerExchange, 0),
            result,
            0,
        )
        return result
    }

    companion object {
        fun generate(): MobileIdentity {
            val random = SecureRandom()
            return MobileIdentity(
                Ed25519PrivateKeyParameters(random).encoded,
                X25519PrivateKeyParameters(random).encoded,
            )
        }
    }
}

data class Peer(
    val deviceId: String,
    val name: String,
    val signingPublic: String,
    val exchangePublic: String,
    val fingerprint: String,
    val host: String,
    val port: Int,
    val capabilities: List<String>,
    val projectScopes: List<String>,
)

object CyreneCrypto {
    fun verify(publicKey: ByteArray, content: ByteArray, signature: ByteArray): Boolean =
        runCatching {
            val verifier = Ed25519Signer()
            verifier.init(false, Ed25519PublicKeyParameters(publicKey, 0))
            verifier.update(content, 0, content.size)
            verifier.verifySignature(signature)
        }.getOrDefault(false)

    fun hmacHex(secret: String, content: ByteArray): String {
        val mac = Mac.getInstance("HmacSHA256")
        mac.init(SecretKeySpec(secret.toByteArray(), "HmacSHA256"))
        return mac.doFinal(content).hex()
    }

    fun deriveEnvelopeKey(
        identity: MobileIdentity,
        peer: Peer,
        sender: String,
        recipient: String,
    ): ByteArray {
        val context = listOf(sender, recipient).sorted().joinToString("|").toByteArray()
        val generator = HKDFBytesGenerator(SHA256Digest())
        generator.init(
            HKDFParameters(
                identity.sharedSecret(b64UrlDecode(peer.exchangePublic)),
                sha256(context),
                "cyrene-remote-envelope-v1".toByteArray(),
            )
        )
        return ByteArray(32).also { generator.generateBytes(it, 0, it.size) }
    }

    fun deriveSessionBundleKey(
        identity: MobileIdentity,
        peer: Peer,
        sessionId: String,
        generation: Long,
        objectId: String,
    ): ByteArray {
        require(sessionId.startsWith("ls_") && generation > 0 && objectId.startsWith("sha256:"))
        val salt = sha256("$sessionId|$generation".toByteArray())
        val generator = HKDFBytesGenerator(SHA256Digest())
        generator.init(
            HKDFParameters(
                identity.sharedSecret(b64UrlDecode(peer.exchangePublic)),
                salt,
                "cyrene-local-session-object-v1|$objectId".toByteArray(),
            )
        )
        return ByteArray(32).also { generator.generateBytes(it, 0, it.size) }
    }

    fun encrypt(key: ByteArray, nonce: ByteArray, aad: ByteArray, plain: ByteArray): ByteArray =
        aead(true, key, nonce, aad, plain)

    fun decrypt(key: ByteArray, nonce: ByteArray, aad: ByteArray, cipher: ByteArray): ByteArray =
        aead(false, key, nonce, aad, cipher)

    private fun aead(
        encrypting: Boolean,
        key: ByteArray,
        nonce: ByteArray,
        aad: ByteArray,
        input: ByteArray,
    ): ByteArray {
        val cipher = ChaCha20Poly1305()
        cipher.init(encrypting, AEADParameters(KeyParameter(key), 128, nonce, aad))
        val output = ByteArray(cipher.getOutputSize(input.size))
        var length = cipher.processBytes(input, 0, input.size, output, 0)
        length += cipher.doFinal(output, length)
        return output.copyOf(length)
    }
}

object EnvelopeCodec {
    fun encode(
        identity: MobileIdentity,
        peer: Peer,
        kind: String,
        payload: JSONObject,
    ): JSONObject {
        val nonce = ByteArray(12).also(SecureRandom()::nextBytes)
        val header = JSONObject()
            .put("version", 1)
            .put("message_id", "msg_" + UUID.randomUUID().toString().replace("-", ""))
            .put("sender_device_id", identity.deviceId)
            .put("recipient_device_id", peer.deviceId)
            .put("kind", kind)
            .put("timestamp", System.currentTimeMillis() / 1000)
            .put("nonce", b64Url(nonce))
        val aad = CanonicalJson.encode(header).toByteArray()
        val key = CyreneCrypto.deriveEnvelopeKey(
            identity, peer, identity.deviceId, peer.deviceId
        )
        val cipher = CyreneCrypto.encrypt(
            key, nonce, aad, CanonicalJson.encode(payload).toByteArray()
        )
        val unsigned = JSONObject(header.toString()).put("ciphertext", b64Url(cipher))
        return JSONObject(unsigned.toString()).put(
            "signature",
            b64Url(identity.sign(CanonicalJson.encode(unsigned).toByteArray()))
        )
    }

    fun decode(
        identity: MobileIdentity,
        peer: Peer,
        envelope: JSONObject,
        message: ((Int) -> String)? = null,
    ): Pair<String, JSONObject> {
        fun error(id: Int, fallback: String): String = message?.invoke(id) ?: fallback
        require(envelope.optInt("version") == 1) {
            error(R.string.error_envelope_version, "Incompatible envelope protocol version")
        }
        require(envelope.optString("sender_device_id") == peer.deviceId) {
            error(R.string.error_envelope_sender, "Unexpected sending device")
        }
        require(envelope.optString("recipient_device_id") == identity.deviceId) {
            error(R.string.error_envelope_recipient, "Unexpected receiving device")
        }
        val timestamp = envelope.optLong("timestamp")
        require(kotlin.math.abs(System.currentTimeMillis() / 1000 - timestamp) <= 300) {
            error(R.string.error_envelope_clock, "Device clocks differ by more than five minutes")
        }
        val unsigned = CanonicalJson.without(envelope, "signature")
        require(
            CyreneCrypto.verify(
                b64UrlDecode(peer.signingPublic),
                CanonicalJson.encode(unsigned).toByteArray(),
                b64UrlDecode(envelope.getString("signature")),
            )
        ) {
            error(R.string.error_envelope_signature, "Response signature verification failed")
        }
        val header = JSONObject()
        listOf(
            "version", "message_id", "sender_device_id", "recipient_device_id",
            "kind", "timestamp", "nonce"
        ).forEach { header.put(it, unsigned.get(it)) }
        val nonce = b64UrlDecode(header.getString("nonce"))
        require(nonce.size == 12) {
            error(R.string.error_envelope_nonce, "Invalid nonce format")
        }
        val key = CyreneCrypto.deriveEnvelopeKey(
            identity, peer, peer.deviceId, identity.deviceId
        )
        val plaintext = CyreneCrypto.decrypt(
            key,
            nonce,
            CanonicalJson.encode(header).toByteArray(),
            b64UrlDecode(unsigned.getString("ciphertext")),
        )
        return envelope.getString("kind") to JSONObject(plaintext.toString(Charsets.UTF_8))
    }
}

fun sha256(value: ByteArray): ByteArray = MessageDigest.getInstance("SHA-256").digest(value)
