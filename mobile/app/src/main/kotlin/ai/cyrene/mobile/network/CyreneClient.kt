package ai.cyrene.mobile.network

import android.content.Context
import ai.cyrene.mobile.R
import ai.cyrene.mobile.protocol.CanonicalJson
import ai.cyrene.mobile.protocol.CyreneCrypto
import ai.cyrene.mobile.protocol.EnvelopeCodec
import ai.cyrene.mobile.protocol.MobileIdentity
import ai.cyrene.mobile.protocol.Peer
import ai.cyrene.mobile.protocol.b64Url
import ai.cyrene.mobile.protocol.b64UrlDecode
import ai.cyrene.mobile.protocol.sha256
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedInputStream
import java.io.BufferedOutputStream
import java.net.HttpURLConnection
import java.net.Inet4Address
import java.net.Inet6Address
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.ConnectException
import java.net.NoRouteToHostException
import java.net.ServerSocket
import java.net.Socket
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import java.net.URL
import java.time.Instant
import java.time.OffsetDateTime
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap

data class PairingOffer(
    val peer: Peer,
    val response: String,
    val expiresAt: String,
)

internal fun parseProtocolInstant(value: String): Instant =
    if (value.endsWith("Z", ignoreCase = true)) {
        Instant.parse(value)
    } else {
        OffsetDateTime.parse(value).toInstant()
    }

internal fun directControlPortCandidates(savedPort: Int): List<Int> =
    (listOf(savedPort) + (37940 downTo 37841))
        .filter { it in 1..65535 }
        .distinct()

class CyreneClient(
    private val identity: MobileIdentity,
    private val deviceName: String,
    private val context: Context,
) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val pending = ConcurrentHashMap<String, CompletableDeferred<JSONObject>>()
    private var listener: LegacyResponseListener? = null

    suspend fun claim(host: String, port: Int, pairingKey: String): PairingOffer {
        EndpointPolicy.validate(host, port, context)
        val normalizedKey = pairingKey.uppercase().filter(Char::isLetterOrDigit)
        require(normalizedKey.length == 10) { text(R.string.error_pair_key_format) }
        val result = post(host, port, "/v1/pairing/claim", JSONObject().put("pairing_key", normalizedKey))
        require(result.status == 200) { mapHttpError(result) }
        val invitationText = result.body.getString("invitation")
        val invitation = JSONObject(b64UrlDecode(invitationText).toString(Charsets.UTF_8))
        require(invitation.optInt("version") == 1) { text(R.string.error_desktop_protocol) }
        require(invitation.optString("kind") == "cyrene_pairing_invitation") {
            text(R.string.error_invalid_pair_invite)
        }
        val expiresAt = invitation.getString("expires_at")
        require(parseProtocolInstant(expiresAt).isAfter(Instant.now())) {
            text(R.string.error_pair_key_expired)
        }
        val device = invitation.getJSONObject("device")
        val signingPublic = device.getString("signing_public_key")
        val signingBytes = b64UrlDecode(signingPublic)
        val expectedDeviceId = "dev_" + b64Url(sha256(signingBytes).copyOfRange(0, 18))
        require(device.getString("device_id") == expectedDeviceId) { text(R.string.error_desktop_identity) }
        val unsigned = CanonicalJson.without(invitation, "signature")
        require(
            CyreneCrypto.verify(
                signingBytes,
                CanonicalJson.encode(unsigned).toByteArray(),
                b64UrlDecode(invitation.getString("signature")),
            )
        ) { text(R.string.error_pair_signature) }
        val calculatedFingerprint = sha256(signingBytes).joinToString("") {
            "%02x".format(it)
        }.take(32).chunked(4).joinToString(" ")
        require(device.getString("fingerprint") == calculatedFingerprint) {
            text(R.string.error_pair_fingerprint)
        }
        val peer = Peer(
            deviceId = expectedDeviceId,
            name = device.optString("device_name", "Cyrene Desktop"),
            signingPublic = signingPublic,
            exchangePublic = device.getString("exchange_public_key"),
            fingerprint = calculatedFingerprint,
            host = host,
            port = port,
            capabilities = invitation.optJSONArray("granted_capabilities").strings(),
            projectScopes = invitation.optJSONArray("granted_project_scopes").strings(),
        )
        val response = JSONObject()
            .put("version", 1)
            .put("kind", "cyrene_pairing_response")
            .put("pairing_id", invitation.getString("pairing_id"))
            .put("secret", invitation.getString("secret"))
            .put("device", identity.publicIdentity(deviceName))
        val proofPayload = CanonicalJson.without(response, "secret")
        response.put(
            "proof",
            CyreneCrypto.hmacHex(
                response.getString("secret"),
                CanonicalJson.encode(proofPayload).toByteArray(),
            )
        )
        response.put(
            "signature",
            b64Url(identity.sign(CanonicalJson.encode(response).toByteArray())),
        )
        return PairingOffer(
            peer,
            b64Url(CanonicalJson.encode(response).toByteArray()),
            expiresAt,
        )
    }

    suspend fun complete(offer: PairingOffer): Peer {
        val listenerPort = ensureLegacyListener(offer.peer)
        val request = JSONObject()
            .put("response", offer.response)
            .put("listener_port", listenerPort)
            .put("transport_mode", "request_response")
            .put(
                "client_features",
                JSONArray(listOf("inline_response_v1", "durable_run_events", "chunked_files")),
            )
        var result = post(offer.peer.host, offer.peer.port, "/v1/pairing/complete", request)
        if (result.status == 400 && result.body.optString("error").contains("transport", true)) {
            request.remove("transport_mode")
            request.remove("client_features")
            result = post(offer.peer.host, offer.peer.port, "/v1/pairing/complete", request)
        }
        require(result.status == 200) { mapHttpError(result) }
        return offer.peer
    }

    suspend fun command(
        peer: Peer,
        command: String,
        projectId: String = "",
        payload: JSONObject = JSONObject(),
        timeoutSeconds: Long = 40,
        idempotencyKey: String? = null,
    ): JSONObject {
        ensureLegacyListener(peer)
        val requestId = "request_" + UUID.randomUUID().toString().replace("-", "")
        val body = JSONObject()
            .put("request_id", requestId)
            .put("command", command)
            .put("project_id", projectId)
            .put(
                "idempotency_key",
                if (!idempotencyKey.isNullOrBlank()) {
                    idempotencyKey
                } else if (command in SIDE_EFFECTS || command.startsWith("local.")) {
                    "idem_${identity.deviceId.takeLast(8)}_${UUID.randomUUID().toString().replace("-", "")}"
                } else "",
            )
            .put("payload", payload)
        val envelope = EnvelopeCodec.encode(identity, peer, "command", body)
        val wrapper = JSONObject().put("envelope", envelope)

        val inline = post(peer.host, peer.port, "/v1/control/request", wrapper, timeoutSeconds.toInt())
        val responsePayload = if (inline.status == 200 && inline.body.has("envelope")) {
            decodeResponse(peer, inline.body.getJSONObject("envelope"), requestId)
        } else if (inline.status == 404 || inline.status == 405) {
            val deferred = CompletableDeferred<JSONObject>()
            pending[requestId] = deferred
            try {
                val legacy = post(peer.host, peer.port, "/v1/control/envelope", wrapper)
                require(legacy.status == 202) { mapHttpError(legacy) }
                withTimeout(timeoutSeconds * 1000) { deferred.await() }
            } finally {
                pending.remove(requestId)
            }
        } else {
            throw IllegalStateException(mapHttpError(inline))
        }
        val result = responsePayload.optJSONObject("result") ?: JSONObject()
        if (result.optBoolean("ok", true).not()) {
            throw IllegalStateException(
                result.optString(
                    "error",
                    result.optString("code", text(R.string.error_command_rejected)),
                )
            )
        }
        return result
    }

    /**
     * Recover from a desktop listener fallback changing after a restart.
     *
     * The listener can move within the documented 37841..37940 range when
     * another Cyrene process owns the saved port. A successful encrypted
     * capabilities command proves that the endpoint owns the paired identity;
     * an unrelated open port can therefore never replace the trusted peer.
     */
    suspend fun recoverEndpoint(peer: Peer): Peer = withContext(Dispatchers.IO) {
        var lastError: Throwable? = null
        for (port in directControlPortCandidates(peer.port)) {
            if (!tcpReachable(peer.host, port)) continue
            val candidate = if (port == peer.port) peer else peer.copy(port = port)
            val verified = runCatching {
                command(candidate, "capabilities.read", timeoutSeconds = 5)
            }.onFailure { lastError = it }.isSuccess
            if (verified) return@withContext candidate
        }
        throw lastError ?: IllegalStateException(
            "Failed to connect to /${peer.host}:${peer.port}",
        )
    }

    private fun tcpReachable(host: String, port: Int): Boolean = runCatching {
        Socket().use { socket ->
            socket.connect(InetSocketAddress(host, port), 180)
        }
    }.isSuccess

    private fun decodeResponse(peer: Peer, envelope: JSONObject, requestId: String): JSONObject {
        val (kind, payload) = EnvelopeCodec.decode(identity, peer, envelope) { id -> text(id) }
        require(kind == "response") { text(R.string.error_not_response) }
        require(payload.getString("request_id") == requestId) { text(R.string.error_response_mismatch) }
        return payload
    }

    private fun ensureLegacyListener(peer: Peer): Int {
        listener?.let {
            it.peer = peer
            return it.port
        }
        val server = LegacyResponseListener(identity, peer) { envelope ->
            runCatching {
                val (kind, payload) = EnvelopeCodec.decode(identity, peer, envelope) { id -> text(id) }
                when (kind) {
                    "response" -> {
                        val requestId = payload.optString("request_id")
                        pending[requestId]?.complete(payload)
                    }
                    "peer_revoked" -> pending.values.forEach {
                        it.completeExceptionally(SecurityException(text(R.string.error_device_revoked)))
                    }
                }
            }
        }
        listener = server
        server.start(scope)
        return server.port
    }

    fun close() {
        listener?.close()
        listener = null
        scope.cancel()
    }

    private suspend fun post(
        host: String,
        port: Int,
        path: String,
        body: JSONObject,
        timeoutSeconds: Int = 35,
    ): HttpResult = withContext(Dispatchers.IO) {
        try {
            val renderedHost = if (host.contains(":")) "[$host]" else host
            val connection = URL("http://$renderedHost:$port$path").openConnection() as HttpURLConnection
            try {
                connection.instanceFollowRedirects = false
                connection.requestMethod = "POST"
                connection.connectTimeout = 5_000
                connection.readTimeout = timeoutSeconds * 1_000
                connection.doOutput = true
                connection.setRequestProperty("Content-Type", "application/json")
                connection.setRequestProperty("Cache-Control", "no-store")
                val bytes = body.toString().toByteArray()
                connection.setFixedLengthStreamingMode(bytes.size)
                connection.outputStream.use { it.write(bytes) }
                val status = connection.responseCode
                val stream = if (status in 200..399) connection.inputStream else connection.errorStream
                val responseText = stream?.bufferedReader()?.use { it.readText() }.orEmpty()
                HttpResult(status, runCatching { JSONObject(responseText) }.getOrElse { JSONObject() })
            } finally {
                connection.disconnect()
            }
        } catch (error: Exception) {
            throw IllegalStateException(mapNetworkError(host, port, error), error)
        }
    }

    private fun mapNetworkError(host: String, port: Int, error: Exception): String {
        val detail = error.message.orEmpty().lowercase()
        return when {
            error is NoRouteToHostException ||
                detail.contains("network is unreachable") ||
                detail.contains("no route to host") ->
                text(R.string.error_network_unreachable, host, port)
            error is SocketTimeoutException ->
                text(R.string.error_connection_timeout, host, port)
            error is ConnectException || detail.contains("connection refused") ->
                text(R.string.error_connection_refused, host, port)
            error is UnknownHostException ->
                text(R.string.error_unknown_host, host)
            else -> text(R.string.error_connection_detail, host, port, error.message.orEmpty())
        }
    }

    private fun mapHttpError(result: HttpResult): String = when (result.status) {
        400 -> {
            val detail = result.body.optString("error").lowercase()
            when {
                detail.contains("pairing") &&
                    (detail.contains("invalid") || detail.contains("expired") ||
                        detail.contains("already") || detail.contains("used")) ->
                    text(R.string.error_pair_key_invalid_or_expired)
                else -> text(R.string.error_http_bad_request)
            }
        }
        403 -> text(R.string.error_http_forbidden)
        404 -> text(R.string.error_http_not_found)
        409 -> text(R.string.error_http_conflict)
        413 -> text(R.string.error_http_too_large)
        429 -> text(R.string.error_http_rate_limit)
        503 -> text(R.string.error_http_unavailable)
        else -> result.body.optString("error", text(R.string.error_http_generic, result.status))
    }

    private fun text(id: Int, vararg args: Any): String = context.getString(id, *args)

    private data class HttpResult(val status: Int, val body: JSONObject)

    companion object {
        private val SIDE_EFFECTS = setOf(
            "chats.create", "chats.update", "chats.delete", "chats.send",
            "runs.guide", "runs.interrupt",
            "tasks.create", "tasks.dispatch", "tasks.approve_plan", "tasks.run_step",
            "tasks.pause", "tasks.resume", "tasks.cancel", "approvals.respond",
            "settings.update", "shell.open", "shell.write", "shell.close",
            "settings.openai_oauth.login", "settings.openai_oauth.logout",
        )
    }
}

private class LegacyResponseListener(
    private val identity: MobileIdentity,
    @Volatile var peer: Peer,
    private val onEnvelope: (JSONObject) -> Unit,
) {
    private val socket = (37841..37940).firstNotNullOfOrNull { candidate ->
        runCatching { ServerSocket(candidate) }.getOrNull()
    } ?: ServerSocket(0)
    val port: Int get() = socket.localPort
    private var job: Job? = null

    fun start(scope: CoroutineScope) {
        job = scope.launch {
            while (!socket.isClosed) {
                val client = runCatching { socket.accept() }.getOrNull() ?: break
                launch {
                    client.use {
                        val input = BufferedInputStream(it.getInputStream())
                        val headerBytes = ArrayList<Byte>()
                        var matched = 0
                        while (headerBytes.size < 8192 && matched < 4) {
                            val value = input.read()
                            if (value < 0) break
                            headerBytes.add(value.toByte())
                            matched = when {
                                matched == 0 && value == 13 -> 1
                                matched == 1 && value == 10 -> 2
                                matched == 2 && value == 13 -> 3
                                matched == 3 && value == 10 -> 4
                                value == 13 -> 1
                                else -> 0
                            }
                        }
                        val headers = headerBytes.toByteArray().toString(Charsets.US_ASCII)
                        val first = headers.lineSequence().firstOrNull().orEmpty()
                        val length = Regex("(?i)Content-Length:\\s*(\\d+)")
                            .find(headers)?.groupValues?.get(1)?.toIntOrNull() ?: 0
                        var status = 400
                        if (first.startsWith("POST /v1/control/envelope ") && length in 2..25_165_824) {
                            val bodyBytes = ByteArray(length)
                            var offset = 0
                            while (offset < length) {
                                val count = input.read(bodyBytes, offset, length - offset)
                                if (count < 0) break
                                offset += count
                            }
                            require(offset == length) { "incomplete response envelope" }
                            val body = bodyBytes.toString(Charsets.UTF_8)
                            val envelope = JSONObject(body).optJSONObject("envelope")
                            if (envelope != null &&
                                envelope.optString("recipient_device_id") == identity.deviceId &&
                                envelope.optString("sender_device_id") == peer.deviceId
                            ) {
                                onEnvelope(envelope)
                                status = 202
                            }
                        }
                        val response = if (status == 202) """{"accepted":true}""" else """{"error":"invalid request"}"""
                        val bytes = response.toByteArray()
                        BufferedOutputStream(it.getOutputStream()).use { output ->
                            output.write(
                                ("HTTP/1.1 $status ${if (status == 202) "Accepted" else "Bad Request"}\r\n" +
                                    "Content-Type: application/json\r\nContent-Length: ${bytes.size}\r\n" +
                                    "Cache-Control: no-store\r\nConnection: close\r\n\r\n").toByteArray()
                            )
                            output.write(bytes)
                            output.flush()
                        }
                    }
                }
            }
        }
    }

    fun close() {
        runCatching { socket.close() }
        job?.cancel()
    }
}

object EndpointPolicy {
    fun validate(hostInput: String, port: Int, context: Context) {
        val host = hostInput.trim().removePrefix("[").removeSuffix("]")
        require(host.isNotBlank()) { context.getString(R.string.error_host_required) }
        require("://" !in host && "/" !in host && "?" !in host && "#" !in host) {
            context.getString(R.string.error_host_url)
        }
        require(port in 1024..65535) { context.getString(R.string.error_port_range) }
        val looksIpv4 = host.matches(Regex("""\d{1,3}(\.\d{1,3}){3}"""))
        val looksIpv6 = ":" in host && host.matches(Regex("""[0-9a-fA-F:.%]+"""))
        require(looksIpv4 || looksIpv6) { context.getString(R.string.error_host_numeric) }
        val address = InetAddress.getByName(host.substringBefore("%"))
        val tailscale = address is Inet4Address &&
            (address.address[0].toInt() and 0xff) == 100 &&
            (address.address[1].toInt() and 0xc0) == 64
        val allowed = address.isSiteLocalAddress || address.isLinkLocalAddress ||
            address.isLoopbackAddress || tailscale ||
            (address is Inet6Address && address.address[0].toInt() and 0xfe == 0xfc)
        require(allowed) { context.getString(R.string.error_host_scope) }
    }
}

private fun JSONArray?.strings(): List<String> =
    if (this == null) emptyList() else (0 until length()).map { getString(it) }
