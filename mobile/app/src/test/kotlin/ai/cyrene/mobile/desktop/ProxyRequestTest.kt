package ai.cyrene.mobile.desktop

import org.junit.Assert.*
import org.junit.Test
import java.io.ByteArrayOutputStream

class ProxyRequestTest {
    @Test fun framingPreservesBufferedBodyAndNextRequest() {
        val input = ("POST /api/test HTTP/1.1\r\nContent-Length: 5\r\n\r\nhello" +
            "GET /next HTTP/1.1\r\n\r\n").byteInputStream().buffered()
        val out = ByteArrayOutputStream()
        readRequest(input)!!.copyBody(input, out)
        assertEquals("hello", out.toString())
        assertEquals("GET /next HTTP/1.1", readRequest(input)!!.line)
    }

    @Test fun chunkedUploadPreservesTrailersAndNextRequest() {
        val body = "5;ext=1\r\nhello\r\n0\r\nX-Checksum: yes\r\n\r\n"
        val input = ("POST / HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n" + body +
            "GET /next HTTP/1.1\r\n\r\n").byteInputStream().buffered()
        val out = ByteArrayOutputStream()
        readRequest(input)!!.copyBody(input, out)
        assertEquals(body, out.toString())
        assertEquals("GET /next HTTP/1.1", readRequest(input)!!.line)
    }

    @Test fun rejectsAmbiguousFraming() {
        for (headers in listOf("Content-Length: 1\r\nContent-Length: 2", "Transfer-Encoding: chunked\r\nContent-Length: 1")) {
            assertThrows(IllegalArgumentException::class.java) {
                readRequest(("POST / HTTP/1.1\r\n$headers\r\n\r\n").byteInputStream())
            }
        }
    }

    @Test fun localAssetsRequireExactVersionAndSafePath() {
        val assets = WorkbenchAssets { path ->
            when(path) {
                "workbench/version.txt" -> "build-hash".byteInputStream()
                "workbench/app/compiled/app.js" -> "original bytes".byteInputStream()
                else -> error("missing")
            }
        }
        fun get(path: String) = assets.response(ProxyRequest("GET $path HTTP/1.1", emptyList()))
        assertTrue(get("/static/app/compiled/app.js?v=build-hash")!!.toString(Charsets.UTF_8).endsWith("original bytes"))
        for (path in listOf("/api/health?v=build-hash", "/static/app/compiled/app.js?v=other",
            "/static/app/compiled/app.js", "/static/app/../secret?v=build-hash")) assertNull(get(path))
    }
}
