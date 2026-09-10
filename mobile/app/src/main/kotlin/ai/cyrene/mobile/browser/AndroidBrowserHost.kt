package ai.cyrene.mobile.browser

import android.annotation.SuppressLint
import android.app.Activity
import android.graphics.Bitmap
import android.graphics.Canvas
import android.os.Handler
import android.os.Looper
import android.util.Base64
import android.view.View
import android.view.ViewGroup
import android.webkit.*
import android.widget.FrameLayout
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.util.UUID

/** Native pages have no JavascriptInterface. Only the origin-restricted Workbench has RPC. */
@SuppressLint("SetJavaScriptEnabled", "JavascriptInterface")
class AndroidBrowserHost(private val activity: Activity, private val workbench: WebView, private val trustedOrigin: String,
    private val chooseFiles: (ValueCallback<Array<android.net.Uri>>) -> Unit,
) : AutoCloseable {
    @Volatile private var capability = UUID.randomUUID().toString() + UUID.randomUUID().toString()
    private val handler = Handler(Looper.getMainLooper())
    private val decor = activity.window.decorView as ViewGroup
    private val layer = FrameLayout(activity)
    private val tabs = linkedMapOf<String, Tab>()
    private val active = mutableMapOf<String, String>()
    private var visibleSession = ""
    private var shown = false
    private var obscured = false
    private var paused = false
    private var closed = false
    private var restoring = true
    private val preferences = activity.getSharedPreferences("native-browser-tabs", 0)
    private val pending = mutableSetOf<String>()
    private val credentials = mutableMapOf<String, Triple<String, String, Long>>()
    private val script = activity.assets.open("browser/page-commands.js").bufferedReader().use { it.readText() }
    private data class Tab(val id: String, val session: String, val page: WebView, var muted: Boolean = false, var loading: Boolean = false, var deferredUrl: String? = null, var ownerRound: String = "")

    init {
        decor.addView(layer, ViewGroup.LayoutParams(1, 1))
        layer.visibility = View.INVISIBLE
        workbench.addJavascriptInterface(this, "CyreneAndroidBrowser")
        runCatching {
            val saved = JSONObject(preferences.getString("state", "{}") ?: "{}")
            val rows = saved.optJSONArray("tabs") ?: JSONArray()
            for (index in 0 until minOf(rows.length(), 12)) {
                val row = rows.getJSONObject(index)
                val session = row.optString("sessionId")
                val url = row.optString("url")
                if (session.length <= 256 && BrowserUrlPolicy.allows(url)) {
                    create(session, url, false, true, row.optString("id").takeIf { it.matches(Regex("[a-f0-9-]{36}")) } ?: UUID.randomUUID().toString())
                }
            }
            val selected = saved.optJSONObject("active") ?: JSONObject()
            for (session in selected.keys()) tabs[selected.optString(session)]?.takeIf { it.session == session }?.let { active[session] = it.id }
        }
        restoring = false
        workbench.viewTreeObserver.addOnGlobalLayoutListener { if (!closed) show() }
        workbench.addOnLayoutChangeListener { _, _, _, _, _, _, _, _, _ ->
            if (!closed && !shown) layer.layoutParams = layer.layoutParams.apply { width = workbench.width.coerceAtLeast(1); height = workbench.height.coerceAtLeast(1) }
        }
    }

    fun beginDocument() {
        capability = UUID.randomUUID().toString() + UUID.randomUUID().toString()
        pending.clear(); shown = false; show()
    }

    fun authorizeMainFrame(url: String) {
        val uri = android.net.Uri.parse(url)
        if (!closed && url == workbench.url && uri.path == "/" &&
            "${uri.scheme}://${uri.encodedAuthority}" == trustedOrigin) {
            workbench.evaluateJavascript("window.__cyreneInitAndroidBrowser?.(${JSONObject.quote(capability)})", null)
        }
    }

    @JavascriptInterface fun request(id: String, raw: String, authority: String) {
        // addJavascriptInterface is visible to subframes too. Only trusted top-level
        // Workbench receives this per-document capability, never iframe previews.
        if (authority != capability) return
        if (raw.length > 1024 * 1024 || id.length > 80) return
        handler.post {
            if (authority != capability || closed || pending.size >= 32 || !pending.add(id)) return@post
            val timeout = Runnable { finish(id, failure("Android browser command timed out")) }
            handler.postDelayed(timeout, 40000)
            fun reply(result: JSONObject) { handler.removeCallbacks(timeout); finish(id, result) }
            try {
                val request = JSONObject(raw)
                val args = request.optJSONObject("args") ?: JSONObject()
                dispatch(request.getString("method"), args, ::reply)
            } catch (error: Exception) { reply(failure(error.message ?: "Invalid browser command")) }
        }
    }

    private fun finish(id: String, result: JSONObject) {
        if (closed || !pending.remove(id)) return
        workbench.evaluateJavascript("window.__cyreneAndroidBrowserResult?.(${JSONObject.quote(id)},$result)", null)
    }
    private fun failure(message: String) = JSONObject().put("ok", false).put("code", "ANDROID_BROWSER_UNAVAILABLE").put("error", message)
    private fun tabData(tab: Tab) = JSONObject().put("id", tab.id).put("tabId", tab.id).put("sessionId", tab.session)
        .put("url", tab.page.url ?: tab.deferredUrl ?: "about:blank").put("title", tab.page.title ?: "")
        .put("loading", tab.loading).put("muted", tab.muted).put("canGoBack", tab.page.canGoBack()).put("canGoForward", tab.page.canGoForward())
    private fun state(session: String) = JSONObject().put("ok", true).put("sessionId", session)
        .put("tabs", JSONArray(tabs.values.filter { it.session == session }.map(::tabData)))
        .put("activeTabId", active[session] ?: "").put("activeTab", tabs[active[session]]?.let(::tabData) ?: JSONObject.NULL)
    private fun manager() = JSONObject().put("ok", true).put("pages", JSONArray(tabs.values.map(::tabData)))
        .put("pageCount", tabs.size).put("downloads", JSONArray()).put("downloadCount", 0)
    private fun persist() {
        if (restoring || closed) return
        val data = JSONObject().put("tabs", JSONArray(tabs.values.map(::tabData))).put("active", JSONObject(active.toMap()))
        preferences.edit().putString("state", data.toString()).apply()
    }
    private fun publish(session: String) {
        persist()
        if (!closed) workbench.evaluateJavascript("window.__cyreneAndroidBrowserState?.(${state(session)},${manager()})", null)
    }
    private fun show() {
        tabs.values.forEach { it.page.visibility = if (it.id == active[visibleSession]) View.VISIBLE else View.INVISIBLE }
        layer.visibility = if (shown && !obscured && !paused && workbench.isShown && active[visibleSession] != null) View.VISIBLE else View.INVISIBLE
    }
    private fun create(session: String, url: String, activate: Boolean = true, defer: Boolean = false, id: String = UUID.randomUUID().toString(), ownerRound: String = ""): Tab {
        require(tabs.size < 12) { "Close a browser tab before opening another (limit 12)" }
        require(BrowserUrlPolicy.allows(url)) { "Only public HTTP(S) pages are supported" }
        val page = WebView(activity)
        val tab = Tab(id, session, page, deferredUrl = if (defer) url else null, ownerRound = ownerRound)
        tabs[tab.id] = tab
        page.settings.apply {
            javaScriptEnabled = true; domStorageEnabled = true
            allowFileAccess = false; allowContentAccess = false
            mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
            setSupportMultipleWindows(true); javaScriptCanOpenWindowsAutomatically = false
            builtInZoomControls = true; displayZoomControls = false
        }
        CookieManager.getInstance().setAcceptThirdPartyCookies(page, false)
        page.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest) = !BrowserUrlPolicy.allows(request.url.toString())
            override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse? {
                if (BrowserUrlPolicy.allows(request.url.toString())) return null
                return WebResourceResponse("text/plain", "UTF-8", 403, "Forbidden", emptyMap(), "Local resources are unavailable".byteInputStream())
            }
            override fun onPageStarted(view: WebView, url: String, icon: android.graphics.Bitmap?) { tab.loading = true; credentials.remove(tab.id); publish(session) }
            override fun onPageFinished(view: WebView, url: String) { if (url != view.url) return; tab.loading = false; CookieManager.getInstance().flush(); publish(session) }
            override fun onRenderProcessGone(view: WebView, detail: RenderProcessGoneDetail): Boolean { remove(tab); return true }
        }
        page.webChromeClient = object : WebChromeClient() {
            override fun onShowFileChooser(view: WebView, callback: ValueCallback<Array<android.net.Uri>>, parameters: FileChooserParams): Boolean {
                chooseFiles(callback); return true
            }
            override fun onReceivedTitle(view: WebView, title: String?) { publish(session) }
            override fun onCreateWindow(view: WebView, dialog: Boolean, gesture: Boolean, message: android.os.Message): Boolean {
                if (!gesture || tabs.size >= 12) return false
                val child = create(session, "about:blank")
                (message.obj as WebView.WebViewTransport).webView = child.page; message.sendToTarget(); return true
            }
            override fun onCloseWindow(window: WebView) { tabs.values.firstOrNull { it.page === window }?.let(::remove) }
        }
        layer.addView(page, FrameLayout.LayoutParams(-1, -1))
        if (activate || active[session] == null) active[session] = tab.id
        if (!defer) page.loadUrl(url)
        show(); publish(session)
        return tab
    }
    private fun remove(tab: Tab) {
        credentials.remove(tab.id)
        tabs.remove(tab.id); layer.removeView(tab.page); tab.page.stopLoading(); tab.page.destroy()
        if (active[tab.session] == tab.id) {
            active.remove(tab.session)
            tabs.values.lastOrNull { it.session == tab.session }?.let { active[tab.session] = it.id }
        }
        show(); publish(tab.session)
    }
    private fun evaluate(tab: Tab, method: String, args: JSONObject, reply: (JSONObject) -> Unit) {
        tab.page.evaluateJavascript("$script(${JSONObject.quote(method)},$args)") { value ->
            if (!closed) {
                val result = runCatching { JSONObject(value) }.getOrElse { failure("Page is not ready") }.put("tabId", tab.id)
                if (method == "inspect" && result.optBoolean("ok")) {
                    val token = UUID.randomUUID().toString()
                    credentials[tab.id] = Triple(token, tab.page.url ?: "", android.os.SystemClock.elapsedRealtime())
                    result.put("snapshot_token", token)
                }
                reply(result)
            }
        }
    }
    private fun afterLoaded(tab: Tab, id: String?, attempts: Int = 100, block: () -> Unit) {
        if (closed || tabs[tab.id] !== tab || (id != null && id !in pending)) return
        if (!tab.loading || attempts <= 0) block()
        else handler.postDelayed({ afterLoaded(tab, id, attempts - 1, block) }, 200)
    }
    private fun dispatch(method: String, args: JSONObject, reply: (JSONObject) -> Unit) {
        val session = args.optString("sessionId", "")
        require(session.length <= 256) { "Invalid browser session" }
        when (method) {
            "state", "setContext" -> { reply(state(session)); return }
            "managerState" -> { reply(manager()); return }
            "setObscured" -> { obscured = args.optBoolean("obscured"); show(); reply(JSONObject().put("ok", true)); return }
            "setBounds" -> {
                if (!args.optBoolean("visible") && visibleSession != session) { reply(JSONObject().put("ok", true)); return }
                visibleSession = session
                shown = args.optBoolean("visible") && args.optString("transition") != "prepare"
                if (shown) {
                    val scale = workbench.scale.coerceAtLeast(0.1f)
                    val host = IntArray(2); val parent = IntArray(2)
                    workbench.getLocationOnScreen(host); decor.getLocationOnScreen(parent)
                    val x = (args.optDouble("x", 0.0) * scale).toInt().coerceIn(0, workbench.width)
                    val y = (args.optDouble("y", 0.0) * scale).toInt().coerceIn(0, workbench.height)
                    val width = (args.optDouble("width", 0.0) * scale).toInt().coerceIn(1, (workbench.width - x).coerceAtLeast(1))
                    val height = (args.optDouble("height", 0.0) * scale).toInt().coerceIn(1, (workbench.height - y).coerceAtLeast(1))
                    layer.layoutParams = layer.layoutParams.apply { this.width = width; this.height = height }
                    layer.background = android.graphics.drawable.GradientDrawable().apply {
                        setColor(android.graphics.Color.WHITE)
                        cornerRadius = (args.optDouble("pageCornerRadius", 0.0) * scale).toFloat().coerceIn(0f, 128f)
                    }
                    layer.clipToOutline = true
                    layer.x = (host[0] - parent[0] + x).toFloat(); layer.y = (host[1] - parent[1] + y).toFloat()
                }
                show(); reply(JSONObject().put("ok", true)); return
            }
            "createTab" -> { create(session, args.optString("url", "about:blank"), args.optBoolean("activate", true), ownerRound = args.optString("roundId")); reply(state(session)); return }
            "closeSession" -> { tabs.values.filter { it.session == session }.toList().forEach(::remove); reply(state(session)); return }
        }
        if (method == "finishRound") {
            val round = args.optString("roundId")
            val owned = tabs.values.filter { it.session == session && round.isNotBlank() && it.ownerRound == round }
            val keep = owned.firstOrNull { it.id == active[session] } ?: owned.lastOrNull()
            val removed = owned.filter { it !== keep }
            removed.forEach(::remove)
            keep?.ownerRound = ""
            reply(JSONObject().put("ok", true).put("closedTabIds", JSONArray(removed.map { it.id }))); return
        }
        if (method !in setOf("navigate", "activateTab", "closeTab", "goBack", "goForward", "reload", "screenshot", "setMuted", "snapshot", "inspect", "visibleLinkMatches", "navigationGuard", "click", "clickRef", "clickText", "clickAt", "type", "typeRef", "scroll", "waitFor")) {
            reply(failure("Not yet supported by Android browser: $method").put("code", "ANDROID_BROWSER_UNSUPPORTED")); return
        }
        val tab = args.optString("tabId").takeIf { it.isNotBlank() }?.let { tabs[it]?.takeIf { tab -> tab.session == session } }
            ?: if (!args.has("tabId") || args.optString("tabId").isBlank()) tabs[active[session]] else null
        if (method == "navigationGuard") {
            val url = args.optString("url")
            require(BrowserUrlPolicy.allows(url)) { "Only public HTTP(S) pages are supported" }
            val reason = args.optString("reason")
            if (tab?.page?.url == url) { reply(failure("Already at the requested page").put("allowed", false).put("code", "ALREADY_AT_TARGET")); return }
            if (reason != "ui_unreachable") { reply(JSONObject().put("ok", true).put("allowed", true).put("targetUrl", url)); return }
            val credential = tab?.let { credentials.remove(it.id) }
            val valid = credential != null && credential.first == args.optString("snapshotToken") &&
                credential.second == tab.page.url && android.os.SystemClock.elapsedRealtime() - credential.third <= 120000
            if (!valid) { reply(failure("Take a fresh browser snapshot before bypassing page navigation").put("allowed", false).put("code", "SNAPSHOT_CREDENTIAL_INVALID")); return }
            evaluate(tab, "visibleLinkMatches", args) { scan ->
                val visible = (scan.optJSONArray("matches")?.length() ?: 0) > 0
                reply(scan.put("allowed", scan.optBoolean("ok") && !visible).put("code", if (visible) "VISIBLE_LINK_AVAILABLE" else "ANDROID_NAVIGATION_GUARD"))
            }
            return
        }
        if (method == "navigate") {
            val url = args.getString("url"); require(BrowserUrlPolicy.allows(url)) { "Only public HTTP(S) pages are supported" }
            val target = tab ?: create(session, "about:blank", ownerRound = args.optString("roundId"))
            target.deferredUrl = null; target.loading = true; target.page.loadUrl(url)
            afterLoaded(target, null) { evaluate(target, "snapshot", args, reply) }; return
        }
        require(tab != null) { "No browser page is open in this conversation" }
        tab.deferredUrl?.let { url ->
            tab.deferredUrl = null; tab.loading = true; tab.page.loadUrl(url)
            afterLoaded(tab, null) { dispatch(method, args, reply) }; return
        }
        when (method) {
            "activateTab" -> { active[session] = tab.id; show(); publish(session); reply(state(session)) }
            "closeTab" -> { remove(tab); reply(state(session)) }
            "goBack", "goForward", "reload" -> { when(method) { "goBack" -> tab.page.goBack(); "goForward" -> tab.page.goForward(); else -> tab.page.reload() }; reply(state(session)) }
            "screenshot" -> {
                require(tab.page.width > 0 && tab.page.height > 0) { "Show the browser page before capturing it" }
                val bitmap = Bitmap.createBitmap(tab.page.width, tab.page.height, Bitmap.Config.ARGB_8888)
                try {
                    tab.page.draw(Canvas(bitmap))
                    val bytes = ByteArrayOutputStream(); bitmap.compress(Bitmap.CompressFormat.PNG, 100, bytes)
                    require(bytes.size() < 5 * 1024 * 1024) { "Screenshot too large" }
                    val encoded = Base64.encodeToString(bytes.toByteArray(), Base64.NO_WRAP)
                    reply(JSONObject().put("ok", true).put("pngBase64", encoded).put("url", tab.page.url).put("title", tab.page.title))
                } finally { bitmap.recycle() }
            }
            "setMuted" -> { tab.muted = args.optBoolean("muted"); evaluate(tab, method, args, reply); publish(session) }
            "snapshot", "inspect", "visibleLinkMatches" -> evaluate(tab, method, args, reply)
            "click", "clickRef", "clickText", "clickAt", "type", "typeRef", "scroll" -> { credentials.remove(tab.id); evaluate(tab, method, args, reply) }
            "waitFor" -> waitFor(tab, args, System.currentTimeMillis() + args.optLong("timeoutMs", 5000).coerceIn(1, 30000), reply)
            else -> reply(failure("Not yet supported by Android browser: $method").put("code", "ANDROID_BROWSER_UNSUPPORTED"))
        }
    }
    private fun waitFor(tab: Tab, args: JSONObject, deadline: Long, reply: (JSONObject) -> Unit) {
        evaluate(tab, "waitFor", args) { result ->
            if (result.optBoolean("matched")) reply(result)
            else if (System.currentTimeMillis() >= deadline) reply(failure("Page condition timed out"))
            else handler.postDelayed({ if (!closed) waitFor(tab, args, deadline, reply) }, 150)
        }
    }
    fun handleBack(): Boolean {
        val page = tabs[active[visibleSession]]?.page
        if (shown && !obscured && layer.visibility == View.VISIBLE && page?.canGoBack() == true) {
            page.goBack(); return true
        }
        return false
    }
    fun pause() { paused = true; layer.visibility = View.INVISIBLE; tabs.values.forEach { it.page.onPause() } }
    fun resume() { paused = false; tabs.values.forEach { it.page.onResume() }; show() }
    override fun close() {
        persist(); CookieManager.getInstance().flush()
        closed = true; handler.removeCallbacksAndMessages(null); pending.clear()
        workbench.removeJavascriptInterface("CyreneAndroidBrowser")
        tabs.values.toList().forEach { layer.removeView(it.page); it.page.destroy() }; tabs.clear(); decor.removeView(layer)
    }
}
