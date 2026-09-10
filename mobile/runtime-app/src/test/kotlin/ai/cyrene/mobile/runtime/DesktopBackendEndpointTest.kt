package ai.cyrene.mobile.runtime

import org.junit.Assert.*
import org.junit.Test
import java.net.InetAddress
import java.net.ServerSocket
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

class DesktopBackendEndpointTest {
    @Test fun healthDeadlineIncludesTheEntireStatusLine() {
        val endpoint = DesktopBackendEndpoint()
        ServerSocket(endpoint.port, 1, InetAddress.getByName("127.0.0.1")).use { server ->
            val worker = Executors.newSingleThreadExecutor()
            val response = worker.submit {
                server.accept().use { socket ->
                    try {
                        for (byte in "HTTP/1.1 200 OK\r\n".toByteArray()) {
                            socket.getOutputStream().write(byte.toInt())
                            Thread.sleep(40)
                        }
                    } catch (_: java.io.IOException) { /* Client deadline closed the socket. */ }
                }
            }
            try {
                // Each individual read is faster than 250 ms, but the full
                // response is slower. It must not renew the timeout per byte.
                assertFalse(endpoint.healthy(250))
            } finally { response.cancel(true); worker.shutdownNow() }
        }
    }

    @Test fun healthUsesAuthenticatedLoopbackRequest() {
        val endpoint = DesktopBackendEndpoint()
        ServerSocket(endpoint.port, 1, InetAddress.getByName("127.0.0.1")).use { server ->
            val worker = Executors.newSingleThreadExecutor()
            try {
                val request = worker.submit<String> {
                    server.accept().use { socket ->
                        socket.soTimeout = 2000
                        val reader = socket.getInputStream().bufferedReader()
                        val headers = generateSequence { reader.readLine()?.takeIf { it.isNotEmpty() } }.toList()
                        socket.getOutputStream().write("HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n".toByteArray())
                        headers.joinToString("\n")
                    }
                }
                assertTrue(endpoint.healthy())
                val headers = request.get(3, TimeUnit.SECONDS)
                assertTrue(headers.startsWith("GET /api/health HTTP/1.1"))
                assertTrue(headers.contains("X-Cyrene-Token: ${endpoint.token}"))
            } finally { worker.shutdownNow() }
        }
    }

    @Test fun stoppedBackendIsNotHealthyAndCredentialsAreUnique() {
        val first = DesktopBackendEndpoint()
        val second = DesktopBackendEndpoint()
        assertFalse(first.healthy(100))
        assertNotEquals(first.token, second.token)
        assertTrue(first.token.matches(Regex("[A-Za-z0-9_-]{43}")))
    }
}
