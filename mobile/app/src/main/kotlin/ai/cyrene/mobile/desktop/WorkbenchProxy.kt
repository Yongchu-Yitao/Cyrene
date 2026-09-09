package ai.cyrene.mobile.desktop

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
class WorkbenchProxy(backendUrl: String, private val backendToken: String) : AutoCloseable {
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
            val input = client.getInputStream()
            // Do not buffer beyond the headers: the remaining bytes may be a POST or WS frame.
            val bytes = ArrayList<Byte>()
            while (bytes.size < 65536) {
                val b = input.read()
                if (b < 0) return
                bytes.add(b.toByte())
                if (bytes.size >= 4 && bytes.takeLast(4) == listOf<Byte>(13, 10, 13, 10)) break
            }
            val raw = bytes.toByteArray().toString(Charsets.ISO_8859_1)
            require(raw.endsWith("\r\n\r\n"))
            val lines = raw.dropLast(4).split("\r\n")
            val request = lines.first().split(' ')
            require(request.size == 3 && request[1].startsWith('/') && !request[1].startsWith("//"))
            val headers = lines.drop(1).map { line ->
                require(':' in line && !line.startsWith(' ') && !line.startsWith('\t'))
                line.substringBefore(':').lowercase() to line.substringAfter(':').trim()
            }
            fun values(name: String) = headers.filter { it.first == name }.map { it.second }
            val cookies = values("cookie").flatMap { it.split(';') }.map(String::trim)
            val authenticated = cookies.any {
                MessageDigest.isEqual(it.toByteArray(), cookie.toByteArray())
            }
            val validOrigin = values("origin").all { it == origin }
            val validSite = values("sec-fetch-site").all { it == "same-origin" || it == "none" }
            if (!authenticated || !validOrigin || !validSite || values("host") != listOf("127.0.0.1:${server.localPort}")) {
                client.getOutputStream().write("HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".toByteArray())
                return
            }
            val websocket = values("upgrade").singleOrNull()?.equals("websocket", true) == true
            val upstream = Socket()
            sockets.add(upstream)
            try {
                upstream.connect(InetSocketAddress(backend.host, backend.port), 15_000)
                client.soTimeout = 0
                val rewritten = buildString {
                    append(lines.first()).append("\r\n")
                    headers.filterNot { it.first in setOf("host", "cookie", "x-cyrene-token", "connection", "proxy-authorization", "proxy-connection") }
                        .forEach { (key, value) -> append(key).append(": ").append(value).append("\r\n") }
                    append("Host: 127.0.0.1:${backend.port}\r\nX-Cyrene-Token: $backendToken\r\n")
                    // One HTTP request per connection; upgrades keep their bidirectional stream.
                    append("Connection: ${if (websocket) "Upgrade" else "close"}\r\n\r\n")
                }
                upstream.getOutputStream().write(rewritten.toByteArray(Charsets.ISO_8859_1))
                executor.execute { runCatching { input.copyTo(upstream.getOutputStream()) } }
                upstream.getInputStream().copyTo(client.getOutputStream())
            } finally { sockets.remove(upstream); upstream.close() }
        }
    }

    override fun close() {
        server.close()
        sockets.forEach { runCatching { it.close() } }
        executor.shutdownNow()
    }
}
