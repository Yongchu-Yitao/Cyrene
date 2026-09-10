package ai.cyrene.mobile.desktop

import java.io.ByteArrayOutputStream
import java.io.EOFException
import java.io.InputStream
import java.io.OutputStream

/** HTTP/1 request framing; response bytes remain a transparent streaming tunnel. */
internal data class ProxyRequest(val line: String, val headers: List<Pair<String, String>>) {
    fun values(name: String) = headers.filter { it.first == name }.map { it.second }

    fun copyBody(input: InputStream, output: OutputStream) {
        if (values("transfer-encoding").isNotEmpty()) {
            while (true) {
                val line = readProxyLine(input) ?: throw EOFException()
                val size = line.substringBefore(';').trim().toLong(16)
                require(size >= 0)
                output.write((line + "\r\n").toByteArray(Charsets.ISO_8859_1))
                if (size == 0L) {
                    var trailerBytes = 0
                    while (true) {
                        val trailer = readProxyLine(input) ?: throw EOFException()
                        trailerBytes += trailer.length + 2
                        require(trailerBytes <= 65536)
                        output.write((trailer + "\r\n").toByteArray(Charsets.ISO_8859_1))
                        if (trailer.isEmpty()) return
                    }
                }
                copyExactly(input, output, size)
                require(input.read() == 13 && input.read() == 10)
                output.write(byteArrayOf(13, 10))
            }
        }
        copyExactly(input, output, values("content-length").singleOrNull()?.toLong() ?: 0L)
    }
}

internal fun readProxyLine(input: InputStream): String? {
    val bytes = ByteArrayOutputStream()
    while (bytes.size() < 65536) {
        val value = input.read()
        if (value == -1) { if (bytes.size() == 0) return null; throw EOFException() }
        if (value == 13) {
            require(input.read() == 10)
            return bytes.toString("ISO-8859-1")
        }
        require(value != 10)
        bytes.write(value)
    }
    error("HTTP line too long")
}

internal fun readRequest(input: InputStream): ProxyRequest? {
    val line = readProxyLine(input) ?: return null
    val parts = line.split(' ')
    require(parts.size == 3 && parts[1].startsWith('/') && !parts[1].startsWith("//"))
    require(parts[2] in setOf("HTTP/1.0", "HTTP/1.1"))
    val headers = mutableListOf<Pair<String, String>>()
    var size = line.length + 2
    while (true) {
        val header = readProxyLine(input) ?: throw EOFException()
        size += header.length + 2
        require(size <= 65536)
        if (header.isEmpty()) break
        val name = header.substringBefore(':')
        require(':' in header && name.matches(Regex("[!#$%&'*+.^_`|~0-9A-Za-z-]+")))
        val value = header.substringAfter(':').trim()
        require(value.none { it.code < 32 && it != '\t' || it.code == 127 })
        headers.add(name.lowercase() to value)
    }
    return ProxyRequest(line, headers).also {
        val lengths = it.values("content-length")
        val encodings = it.values("transfer-encoding")
        require(lengths.size <= 1 && encodings.size <= 1)
        require(lengths.isEmpty() || encodings.isEmpty())
        require(lengths.all { value -> value.matches(Regex("[0-9]+")) && value.toLong() >= 0 })
        require(encodings.all { value -> value.equals("chunked", true) })
    }
}

private fun copyExactly(input: InputStream, output: OutputStream, size: Long) {
    var remaining = size
    val buffer = ByteArray(16 * 1024)
    while (remaining > 0) {
        val count = input.read(buffer, 0, minOf(buffer.size.toLong(), remaining).toInt())
        if (count < 0) throw EOFException()
        output.write(buffer, 0, count)
        remaining -= count
    }
}
