# Android shell uses platform WebView and Binder; no legacy provider reflection.
# Workbench calls this method by name; external page WebViews do not register it.
-keepclassmembers class ai.cyrene.mobile.browser.AndroidBrowserHost {
    @android.webkit.JavascriptInterface <methods>;
}
