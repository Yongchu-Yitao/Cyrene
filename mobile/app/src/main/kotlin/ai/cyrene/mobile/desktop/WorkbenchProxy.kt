package ai.cyrene.mobile.desktop

import java.io.BufferedInputStream
import java.io.InputStream
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.net.Socket
import java.net.URI
import java.security.MessageDigest
import java.security.SecureRandom
import java.util.Base64
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.Executors
import java.util.concurrent.Semaphore

/** Per-WebView loopback gateway. The backend bearer never enters HTML or URLs. */
class WorkbenchProxy(backendUrl: String, private val backendToken: String,
                     assetOpen: ((String) -> InputStream)? = null) : AutoCloseable {
    private val assets = assetOpen?.let { WorkbenchAssets(it) }
    private val backend = URI(backendUrl).also {
        require(it.scheme == "http" && it.host == "127.0.0.1" && it.port in 1..65535)
        require(backendToken.matches(Regex("[A-Za-z0-9_-]{40,}")))
    }
    private val server = ServerSocket(0, 32, InetAddress.getByName("127.0.0.1"))
    private val executor = Executors.newCachedThreadPool { task ->
        Thread(task, "WorkbenchProxy").apply { isDaemon = true }
    }
    private val sockets = ConcurrentHashMap.newKeySet<Socket>()
    private val slots = Semaphore(32)
    private val secret = ByteArray(32).also { SecureRandom().nextBytes(it) }
        .let { Base64.getUrlEncoder().withoutPadding().encodeToString(it) }
    val cookieName = "cyrene_webview_" + secret.take(12)
    val cookie: String get() = "$cookieName=$secret"
    val origin: String = "http://127.0.0.1:${server.localPort}"
    val sessionCookie: String get() = "$cookie; Path=/; HttpOnly; SameSite=Strict"

    fun owns(url: String): Boolean = runCatching {
        val uri = URI(url)
        uri.scheme == "http" && uri.host == "127.0.0.1" && uri.port == server.localPort && uri.userInfo == null
    }.getOrDefault(false)

    init {
        executor.execute {
            while (!server.isClosed) {
                val socket = runCatching { server.accept() }.getOrNull() ?: break
                if (!slots.tryAcquire()) { socket.close(); continue }
                sockets.add(socket)
                runCatching { executor.execute { try { relay(socket) } finally {
                    sockets.remove(socket); socket.close(); slots.release()
                } } }.onFailure { sockets.remove(socket); socket.close(); slots.release() }
            }
        }
    }

    private fun relay(client: Socket) {
        runCatching {
            client.soTimeout = 15_000
            val input = BufferedInputStream(client.getInputStream(), 16 * 1024)
            // Retain buffered body/upgrade bytes, and validate EVERY request on
            // the persistent connection before forwarding it to the backend.
            val first = readRequest(input) ?: return
            if (!authorize(first, client)) return
            assets?.response(first)?.let { client.getOutputStream().write(it); return }
            val upstream = Socket()
            sockets.add(upstream)
            try {
                upstream.connect(InetSocketAddress(backend.host, backend.port), 15_000)
                upstream.tcpNoDelay = true
                client.tcpNoDelay = true
                client.soTimeout = 0
                val output = upstream.getOutputStream()
                executor.execute {
                    try {
                        var request = first
                        while (true) {
                            val upgrade = request.values("upgrade").singleOrNull()?.equals("websocket", true) == true
                            val rewritten = buildString {
                                append(request.line).append("\r\n")
                                request.headers.filterNot { it.first in setOf("host", "cookie", "x-cyrene-token", "connection", "proxy-authorization", "proxy-connection") }
                                    .forEach { (key, value) -> append(key).append(": ").append(value).append("\r\n") }
                                append("Host: 127.0.0.1:${backend.port}\r\nX-Cyrene-Token: $backendToken\r\n")
                                val close = request.line.endsWith("HTTP/1.0") || request.values("connection").any { it.split(',').any { word -> word.trim().equals("close", true) } }
                                append("Connection: ${if (upgrade) "Upgrade" else if (close) "close" else "keep-alive"}\r\n\r\n")
                            }
                            output.write(rewritten.toByteArray(Charsets.ISO_8859_1))
                            if (upgrade) { input.copyTo(output); break }
                            request.copyBody(input, output)
                            request = readRequest(input) ?: break
                            if (!authorize(request, client)) break
                        }
                        runCatching { upstream.shutdownOutput() }
                    } catch (_: Exception) {
                        // Invalid/truncated framing must not reach another request.
                        runCatching { upstream.close() }
                    }
                }
                // Streaming responses (including SSE) are relayed immediately;
                // never buffer a whole response or infer its message boundaries.
                upstream.getInputStream().copyTo(client.getOutputStream())
            } finally { sockets.remove(upstream); upstream.close() }
        }
    }

    private fun authorize(request: ProxyRequest, client: Socket): Boolean {
        val cookies = request.values("cookie").flatMap { it.split(';') }.map(String::trim)
        val authenticated = cookies.any { MessageDigest.isEqual(it.toByteArray(), cookie.toByteArray()) }
        val allowed = authenticated && request.values("origin").all { it == origin } &&
            request.values("sec-fetch-site").all { it == "same-origin" || it == "none" } &&
            request.values("host") == listOf("127.0.0.1:${server.localPort}")
        if (!allowed) client.getOutputStream().write("HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".toByteArray())
        return allowed
    }

    override fun close() {
        server.close()
        sockets.forEach { runCatching { it.close() } }
        executor.shutdownNow()
    }
}
