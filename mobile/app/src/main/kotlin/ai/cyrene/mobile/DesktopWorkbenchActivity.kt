package ai.cyrene.mobile

import android.annotation.SuppressLint
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.view.View
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import android.webkit.CookieManager
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.lifecycle.ViewModelProvider
import ai.cyrene.mobile.desktop.WorkbenchModel
import ai.cyrene.mobile.desktop.WorkbenchProxy
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.net.HttpURLConnection
import java.net.URL

/** The bundled desktop Workbench, talking to the on-device Python backend. */
class DesktopWorkbenchActivity : ComponentActivity() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private lateinit var model: WorkbenchModel
    private lateinit var root: LinearLayout
    private lateinit var status: TextView
    private lateinit var progress: ProgressBar
    private lateinit var startupDetail: TextView
    private lateinit var retry: Button
    private lateinit var loading: LinearLayout
    private var pageFailed = false
    private var web: WebView? = null
    private var nativeBrowser: ai.cyrene.mobile.browser.AndroidBrowserHost? = null
    private var insetScrim: ai.cyrene.mobile.desktop.SystemInsetScrim? = null
    private var loadedOrigin: String? = null
    private var proxy: WorkbenchProxy? = null
    private var fileCallback: ValueCallback<Array<Uri>>? = null
    private var downloadUrl: String? = null
    private val picker = registerForActivityResult(ActivityResultContracts.OpenMultipleDocuments()) { uris ->
        fileCallback?.onReceiveValue(uris.takeIf { it.isNotEmpty() }?.toTypedArray()); fileCallback = null
    }
    private val saver = registerForActivityResult(ActivityResultContracts.CreateDocument("application/octet-stream")) { uri ->
        val url = downloadUrl; downloadUrl = null
        val current = proxy
        if (uri != null && url != null && current?.owns(url) == true) scope.launch {
            try {
                withContext(Dispatchers.IO) {
                    val connection = URL(url).openConnection() as HttpURLConnection
                    connection.instanceFollowRedirects = false
                    connection.connectTimeout = 15_000; connection.readTimeout = 60_000
                    connection.setRequestProperty("Cookie", current.cookie)
                    try {
                        check(connection.responseCode == 200) { "Download HTTP ${connection.responseCode}" }
                        contentResolver.openOutputStream(uri)?.use { output -> connection.inputStream.use { it.copyTo(output) } }
                            ?: error("Unable to write selected file")
                    } finally { connection.disconnect() }
                }
                android.widget.Toast.makeText(this@DesktopWorkbenchActivity, R.string.workbench_download_saved, android.widget.Toast.LENGTH_SHORT).show()
            } catch (failure: Exception) { android.widget.Toast.makeText(this@DesktopWorkbenchActivity, R.string.workbench_download_failed, android.widget.Toast.LENGTH_SHORT).show() }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        model = ViewModelProvider(this)[WorkbenchModel::class.java]
        root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        ViewCompat.setOnApplyWindowInsetsListener(root) { view, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            val keyboard = insets.getInsets(WindowInsetsCompat.Type.ime())
            view.setPadding(bars.left, bars.top, bars.right, maxOf(bars.bottom, keyboard.bottom)); insets
        }
        fun dp(value: Int) = (value * resources.displayMetrics.density).toInt()
        loading = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = android.view.Gravity.CENTER
            setPadding(dp(32), dp(24), dp(32), dp(24))
        }
        loading.addView(android.widget.ImageView(this).apply {
            setImageResource(R.drawable.ic_launcher_full)
            importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO
        }, LinearLayout.LayoutParams(dp(64), dp(64)))
        loading.addView(TextView(this).apply {
            setText(R.string.app_name); textSize = 28f
            setTypeface(typeface, android.graphics.Typeface.BOLD)
            gravity = android.view.Gravity.CENTER
            setPadding(0, dp(16), 0, dp(8))
        })
        status = TextView(this).apply {
            textSize = 15f; gravity = android.view.Gravity.CENTER
            setPadding(0, dp(8), 0, dp(24))
            accessibilityLiveRegion = View.ACCESSIBILITY_LIVE_REGION_POLITE
        }
        loading.addView(status)
        progress = ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal).apply {
            isIndeterminate = true; max = 100
        }
        loading.addView(progress, LinearLayout.LayoutParams(-1, dp(6)).apply {
            leftMargin = dp(16); rightMargin = dp(16)
        })
        startupDetail = TextView(this).apply {
            textSize = 13f; gravity = android.view.Gravity.CENTER
            setPadding(0, dp(12), 0, dp(12))
        }
        loading.addView(startupDetail)
        retry = Button(this).apply {
            setText(R.string.workbench_retry)
            visibility = View.GONE
            setOnClickListener { model.state.value.proxy?.let { loadWorkbench(it) } ?: model.start() }
        }
        loading.addView(retry)
        root.addView(loading, LinearLayout.LayoutParams(-1, 0, 1f))
        setContentView(root)
        scope.launch {
            model.state.collect { state ->
                if ((applicationInfo.flags and android.content.pm.ApplicationInfo.FLAG_DEBUGGABLE != 0)) java.io.File(filesDir, "workbench-state.json").writeText(
                    org.json.JSONObject().put("phase", state.phase).put("error", state.error).toString())
                if (state.phase != "ready") {
                    loading.visibility = View.VISIBLE
                    progress.visibility = if (state.phase in setOf("starting", "stopping")) View.VISIBLE else View.GONE
                    retry.visibility = if (state.phase in setOf("error", "stopped")) View.VISIBLE else View.GONE
                    status.setText(if (state.phase == "error") R.string.workbench_error else R.string.workbench_starting)
                    startupDetail.visibility = if (state.phase == "starting") View.VISIBLE else View.GONE
                    if (state.phase == "starting") showStartupProgress(state.startup)
                }
                if (state.proxy != null && loadedOrigin != state.proxy.origin) loadWorkbench(state.proxy)
                if (state.proxy == null && web != null) destroyWeb()
            }
        }
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                // Returning to the launcher keeps the workspace and unfinished input alive.
                if (nativeBrowser?.handleBack() != true) moveTaskToBack(true)
            }
        })
        model.start()
    }

    private fun showStartupProgress(value: ai.cyrene.mobile.runtime.protocol.StartupProgress) {
        val label = getString(when (value.stage) {
            "assets" -> R.string.workbench_stage_assets
            "verify", "verify_disk" -> R.string.workbench_stage_verify
            "unpack" -> R.string.workbench_stage_unpack
            "disk" -> R.string.workbench_stage_disk
            "boot" -> R.string.workbench_stage_boot
            "backend" -> R.string.workbench_stage_backend
            "backend_plugins" -> R.string.workbench_stage_plugins
            "backend_services" -> R.string.workbench_stage_services
            "ready", "page" -> R.string.workbench_stage_page
            else -> R.string.workbench_stage_connect
        })
        val percent = value.percent
        progress.isIndeterminate = percent == null
        if (percent != null) progress.setProgress(percent, true)
        progress.contentDescription = label
        startupDetail.visibility = View.VISIBLE
        startupDetail.text = if (percent == null) label else getString(R.string.workbench_stage_percent, label, percent)
    }

    private fun showPageError() {
        pageFailed = true
        web?.visibility = View.GONE
        loading.visibility = View.VISIBLE
        status.setText(R.string.workbench_error)
        progress.visibility = View.GONE
        startupDetail.visibility = View.GONE
        retry.visibility = View.VISIBLE
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun loadWorkbench(current: WorkbenchProxy) {
        destroyWeb()
        pageFailed = false
        loading.visibility = View.VISIBLE
        progress.visibility = View.VISIBLE
        retry.visibility = View.GONE
        status.setText(R.string.workbench_starting)
        showStartupProgress(ai.cyrene.mobile.runtime.protocol.StartupProgress("page"))
        proxy = current; loadedOrigin = current.origin
        val browser = WebView(this)
        if (android.os.Build.VERSION.SDK_INT >= 33) browser.setAutoHandwritingEnabled(false)
        web = browser
        nativeBrowser = ai.cyrene.mobile.browser.AndroidBrowserHost(this, browser, current.origin) { callback ->
            fileCallback?.onReceiveValue(null); fileCallback = callback
            picker.launch(arrayOf("*/*"))
        }
        insetScrim = ai.cyrene.mobile.desktop.SystemInsetScrim(window.decorView, browser)
        // Cosmetic-only bridge; navigation and subresources are restricted to our proxy origin.
        browser.addJavascriptInterface(object {
            @android.webkit.JavascriptInterface
            fun setDrawerOpen(open: Boolean) {
                runOnUiThread { if (web === browser) insetScrim?.show(open) }
            }
        }, "CyreneAndroid")
        browser.visibility = View.GONE
        browser.settings.apply {
            javaScriptEnabled = true; domStorageEnabled = true
            allowFileAccess = false; allowContentAccess = true
            setSupportMultipleWindows(false)
            useWideViewPort = true; loadWithOverviewMode = true
        }
        browser.webViewClient = object : WebViewClient() {
            override fun onPageStarted(view: WebView, url: String, icon: android.graphics.Bitmap?) {
                nativeBrowser?.beginDocument()
            }
            override fun onReceivedError(view: WebView, request: WebResourceRequest, error: android.webkit.WebResourceError) {
                if (request.isForMainFrame) {
                    showPageError()
                }
            }
            override fun onReceivedHttpError(view: WebView, request: WebResourceRequest, response: WebResourceResponse) {
                if (request.isForMainFrame) showPageError()
            }
            override fun onPageFinished(view: WebView, url: String) {
                nativeBrowser?.authorizeMainFrame(url)
                if (!pageFailed && web === view && current.owns(url)) {
                    loading.visibility = View.GONE
                    view.visibility = View.VISIBLE
                    android.util.Log.i("CyreneStartup", "workbench_page_loaded")
                }
                if ((applicationInfo.flags and android.content.pm.ApplicationInfo.FLAG_DEBUGGABLE != 0) && intent.getBooleanExtra("verify_workbench", false) && current.owns(url)) {
                    verifyWebView(view)
                }
            }
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                if (current.owns(request.url.toString())) return false
                if (request.isForMainFrame && request.hasGesture() && request.url.scheme in setOf("https", "http")) {
                    runCatching { startActivity(Intent(Intent.ACTION_VIEW, request.url)) }
                }
                return true
            }
            override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse? {
                if (current.owns(request.url.toString())) return null
                return WebResourceResponse("text/plain", "UTF-8", 403, "Forbidden", emptyMap(), "External resource blocked".byteInputStream())
            }
            override fun onRenderProcessGone(view: WebView, detail: android.webkit.RenderProcessGoneDetail): Boolean {
                destroyWeb(); showPageError()
                return true
            }
        }
        browser.webChromeClient = object : WebChromeClient() {
            override fun onProgressChanged(view: WebView, newProgress: Int) {
                if (web === view && !pageFailed) showStartupProgress(
                    ai.cyrene.mobile.runtime.protocol.StartupProgress("page", newProgress.toLong(), 100))
            }
            override fun onShowFileChooser(view: WebView, callback: ValueCallback<Array<Uri>>, params: FileChooserParams): Boolean {
                fileCallback?.onReceiveValue(null); fileCallback = callback
                picker.launch(params.acceptTypes.filter { it.isNotBlank() }.toTypedArray().ifEmpty { arrayOf("*/*") })
                return true
            }
        }
        browser.setDownloadListener { url, _, disposition, mime, _ ->
            if (current.owns(url)) {
                downloadUrl = url
                saver.launch(android.webkit.URLUtil.guessFileName(url, disposition, mime))
            } else android.widget.Toast.makeText(this@DesktopWorkbenchActivity, R.string.workbench_download_unsupported, android.widget.Toast.LENGTH_SHORT).show()
        }
        root.addView(browser, LinearLayout.LayoutParams(-1, 0, 1f))
        CookieManager.getInstance().apply {
            setAcceptCookie(true); setAcceptThirdPartyCookies(browser, false)
            setCookie(current.origin, current.sessionCookie) {
                if (web === browser) browser.loadUrl(current.origin + "/")
            }
        }
    }

    // Debug-only acceptance through the actual WebView cookie/fetch/EventSource stack.
    // The invalid POST must return validation failure and never creates a conversation.
    private fun verifyWebView(browser: WebView) {
        browser.evaluateJavascript("""
            (() => { if (window.__cyreneProbeStarted) return; window.__cyreneProbeStarted = true;
              (async () => { const result = {};
                try {
                  result.health = (await fetch('/api/health')).status;
                  result.projects = (await fetch('/v1/control/projects')).status;
                  result.post = (await fetch('/v1/control/chats', {method:'POST', headers:{'Content-Type':'application/json'}, body:'{"title":{}}'})).status;
                  result.events = await new Promise(resolve => {
                    const events = new EventSource('/api/events');
                    const timer = setTimeout(() => { events.close(); resolve(false); }, 20000);
                    events.onopen = () => { clearTimeout(timer); events.close(); resolve(true); };
                    events.onerror = () => { clearTimeout(timer); events.close(); resolve(false); };
                  });
                  result.title = document.title;
                  result.bodyCharacters = document.body.innerText.length;
                } catch (error) { result.error = String(error); }
                window.__cyreneProbeResult = JSON.stringify(result);
              })();
            })();
        """.trimIndent(), null)
        fun poll(remaining: Int) {
            if (web !== browser) return
            browser.evaluateJavascript("window.__cyreneProbeResult || null") { value ->
                if (value != "null") java.io.File(filesDir, "workbench-webview-probe.json").writeText(value)
                else if (remaining > 0) browser.postDelayed({ poll(remaining - 1) }, 1000)
            }
        }
        poll(90)
    }

    private fun destroyWeb() {
        nativeBrowser?.close(); nativeBrowser = null
        insetScrim?.close(); insetScrim = null
        proxy?.let { CookieManager.getInstance().setCookie(it.origin, "${it.cookieName}=; Path=/; Max-Age=0") }
        web?.let { root.removeView(it); it.stopLoading(); it.destroy() }
        web = null; loadedOrigin = null; proxy = null
    }

    override fun onResume() {
        super.onResume()
        web?.onResume()
        nativeBrowser?.resume()
        if (::model.isInitialized) model.resume()
    }

    override fun onPause() {
        nativeBrowser?.pause()
        web?.onPause()
        super.onPause()
    }

    override fun onDestroy() {
        fileCallback?.onReceiveValue(null); fileCallback = null
        destroyWeb(); scope.cancel(); super.onDestroy()
    }
}
