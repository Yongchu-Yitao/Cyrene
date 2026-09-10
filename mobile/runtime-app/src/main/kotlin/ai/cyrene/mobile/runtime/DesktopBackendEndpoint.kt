package ai.cyrene.mobile.runtime

import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.net.Socket
import java.net.SocketTimeoutException
import java.security.SecureRandom
import java.util.Base64

/** Credentials are returned only through the signature-protected Binder API. */
class DesktopBackendEndpoint {
    val port: Int = ServerSocket(0, 1, InetAddress.getByName("127.0.0.1")).use { it.localPort }
    val token: String = ByteArray(32).also { SecureRandom().nextBytes(it) }
        .let { Base64.getUrlEncoder().withoutPadding().encodeToString(it) }
    val url: String get() = "http://127.0.0.1:$port"

    fun healthy(timeoutMs: Int = 5000): Boolean = runCatching {
        require(timeoutMs > 0)
        val deadline = System.nanoTime() + timeoutMs.toLong() * 1_000_000
        fun remaining(): Int {
            val nanos = deadline - System.nanoTime()
            if (nanos <= 0) throw SocketTimeoutException("Health check deadline expired")
            return ((nanos + 999_999) / 1_000_000).coerceAtMost(Int.MAX_VALUE.toLong()).toInt()
        }
        Socket().use { socket ->
            socket.connect(InetSocketAddress("127.0.0.1", port), remaining())
            socket.getOutputStream().write(
                ("GET /api/health HTTP/1.1\r\nHost: 127.0.0.1:$port\r\n" +
                    "X-Cyrene-Token: $token\r\nConnection: close\r\n\r\n").toByteArray(Charsets.US_ASCII)
            )
            val status = StringBuilder()
            val input = socket.getInputStream()
            while (status.length < 128) {
                socket.soTimeout = remaining()
                val byte = input.read()
                if (byte == -1 || byte == 10) break
                status.append(byte.toChar())
            }
            status.toString().startsWith("HTTP/1.1 200 ")
        }
    }.getOrDefault(false)
}
