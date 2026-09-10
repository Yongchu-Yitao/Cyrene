package ai.cyrene.mobile.browser

import java.net.URI

/** Keep the privileged Workbench and local Android services out of page WebViews. */
internal object BrowserUrlPolicy {
    fun allows(raw: String): Boolean = runCatching {
        if (raw.length > 8192) return false
        if (raw == "about:blank") return true
        val uri = URI(raw)
        val host = (uri.host ?: return false).lowercase().removeSuffix(".")
        uri.scheme in setOf("https", "http") && uri.rawUserInfo == null &&
            host != "localhost" && !host.endsWith(".localhost") &&
            !host.contains(':') && !host.startsWith('[') &&
            !host.matches(Regex("[0-9.]+")) && !host.startsWith("0x")
    }.getOrDefault(false)
}
