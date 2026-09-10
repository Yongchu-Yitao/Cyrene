package ai.cyrene.mobile.desktop

import org.junit.Assert.*
import org.junit.Test
import java.net.ServerSocket
import java.net.Socket
import java.net.URI
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

class WorkbenchProxyTest {
    private val token = "a".repeat(43)
    private fun headers(socket: Socket): String {
        val out = StringBuilder()
        while (!out.endsWith("\r\n\r\n")) {
            val value = socket.getInputStream().read()
            check(value >= 0); out.append(value.toChar())
        }
        return out.toString()
    }
    private fun connect(proxy: WorkbenchProxy) = Socket("127.0.0.1", URI(proxy.origin).port).apply { soTimeout = 3000 }
    private fun request(proxy: WorkbenchProxy, extra: String = "", cookie: Boolean = true) =
        "GET /api/health HTTP/1.1\r\nHost: ${URI(proxy.origin).authority}\r\n" +
            (if (cookie) "Cookie: ${proxy.cookie}\r\n" else "") + extra + "\r\n"

    @Test fun rejectsMissingCredentialAndCrossOriginWithoutContactingBackend() {
        ServerSocket(0).use { backend ->
            WorkbenchProxy("http://127.0.0.1:${backend.localPort}", token).use { proxy ->
                for (raw in listOf(request(proxy, cookie = false), request(proxy, "Origin: http://evil.test\r\n"),
                    request(proxy, "Sec-Fetch-Site: same-site\r\n"))) {
                    connect(proxy).use { client ->
                        client.getOutputStream().write(raw.toByteArray())
                        assertTrue(headers(client).startsWith("HTTP/1.1 403"))
                    }
                }
                assertFalse(proxy.owns("http://127.0.0.1:${backend.localPort}/"))
                assertFalse(proxy.owns("https://example.com/"))
            }
        }
    }

    @Test fun streamsPostBodyAndResponseAndReplacesUntrustedBearer() {
        ServerSocket(0).use { backend ->
            val pool = Executors.newSingleThreadExecutor()
            try {
                val received = pool.submit<String> {
                    backend.accept().use { socket ->
                        socket.soTimeout = 3000
                        val head = headers(socket)
                        val body = ByteArray(5)
                        java.io.DataInputStream(socket.getInputStream()).readFully(body)
                        assertEquals("hello", body.toString(Charsets.UTF_8))
                        socket.getOutputStream().write("HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\ndata: done\n\n".toByteArray())
                        head
                    }
                }
                WorkbenchProxy("http://127.0.0.1:${backend.localPort}", token).use { proxy ->
                    connect(proxy).use { client ->
                        val raw = request(proxy, "Content-Length: 5\r\nX-Cyrene-Token: wrong\r\n").replace("GET ", "POST ") + "hello"
                        client.getOutputStream().write(raw.toByteArray())
                        assertTrue(client.getInputStream().bufferedReader().readText().endsWith("data: done\n\n"))
                    }
                    val head = received.get(3, TimeUnit.SECONDS)
                    assertTrue(head.contains("X-Cyrene-Token: $token\r\n"))
                    assertFalse(head.contains("wrong")); assertFalse(head.lowercase().contains("cookie:"))
                }
            } finally { pool.shutdownNow() }
        }
    }

    @Test fun keepsUpgradeBidirectional() {
        ServerSocket(0).use { backend ->
            val pool = Executors.newSingleThreadExecutor()
            try {
                val upgraded = pool.submit {
                    backend.accept().use { socket ->
                        socket.soTimeout = 3000
                        assertTrue(headers(socket).contains("Connection: Upgrade\r\n"))
                        socket.getOutputStream().write("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n".toByteArray())
                        val value = socket.getInputStream().read()
                        socket.getOutputStream().write(value)
                    }
                }
                WorkbenchProxy("http://127.0.0.1:${backend.localPort}", token).use { proxy ->
                    connect(proxy).use { client ->
                        client.getOutputStream().write(request(proxy, "Upgrade: websocket\r\nConnection: Upgrade\r\nOrigin: ${proxy.origin}\r\n").toByteArray())
                        assertTrue(headers(client).startsWith("HTTP/1.1 101"))
                        client.getOutputStream().write(42)
                        assertEquals(42, client.getInputStream().read())
                    }
                    upgraded.get(3, TimeUnit.SECONDS)
                }
            } finally { pool.shutdownNow() }
        }
    }

    @Test fun reusesOneUpstreamAndChecksCredentialsOnEachRequest() {
        ServerSocket(0).use { backend ->
            val pool = Executors.newSingleThreadExecutor()
            try {
                val served = pool.submit {
                    backend.accept().use { socket ->
                        socket.soTimeout = 3000
                        repeat(2) {
                            assertTrue(headers(socket).contains("Connection: keep-alive"))
                            socket.getOutputStream().write("HTTP/1.1 200 OK\r\nContent-Length: 1\r\n\r\nx".toByteArray())
                        }
                        assertEquals(-1, socket.getInputStream().read())
                    }
                }
                WorkbenchProxy("http://127.0.0.1:${backend.localPort}", token).use { proxy ->
                    connect(proxy).use { client ->
                        repeat(2) {
                            client.getOutputStream().write(request(proxy).toByteArray())
                            assertTrue(headers(client).startsWith("HTTP/1.1 200"))
                            assertEquals('x'.code, client.getInputStream().read())
                        }
                        client.getOutputStream().write(request(proxy, cookie = false).toByteArray())
                        assertTrue(headers(client).startsWith("HTTP/1.1 403"))
                    }
                    served.get(3, TimeUnit.SECONDS)
                }
            } finally { pool.shutdownNow() }
        }
    }
}
