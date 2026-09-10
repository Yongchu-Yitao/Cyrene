package ai.cyrene.mobile.desktop

import java.net.URI

/** Exact-build static resources only. HTML, API, ranges and unknown builds use the backend. */
internal class WorkbenchAssets(private val open: (String) -> java.io.InputStream) {
    private val version = runCatching {
        open("workbench/version.txt").bufferedReader().use { it.readText().trim() }
    }.getOrNull()

    fun response(request: ProxyRequest): ByteArray? = runCatching {
        if (version.isNullOrBlank() || !request.line.startsWith("GET ") ||
            request.values("range").isNotEmpty() || request.values("content-length").isNotEmpty() ||
            request.values("transfer-encoding").isNotEmpty()) return null
        val uri = URI(request.line.split(' ')[1])
        if (uri.rawQuery != "v=$version" || !uri.rawPath.startsWith("/static/app/")) return null
        val path = uri.rawPath.removePrefix("/static/app/")
        if (!path.matches(Regex("[A-Za-z0-9_./-]+")) || path.split('/').any { it == ".." || it == "." }) return null
        val mime = when (path.substringAfterLast('.')) {
            "js", "mjs" -> "application/javascript"
            "css" -> "text/css"
            "woff2" -> "font/woff2"
            "woff" -> "font/woff"
            "svg" -> "image/svg+xml"
            "png" -> "image/png"
            else -> return null
        }
        val body = open("workbench/app/$path").use { it.readBytes() }
        val header = "HTTP/1.1 200 OK\r\nContent-Type: $mime\r\nContent-Length: ${body.size}\r\nCache-Control: no-cache\r\nConnection: close\r\n\r\n"
        header.toByteArray(Charsets.ISO_8859_1) + body
    }.getOrNull()
}
