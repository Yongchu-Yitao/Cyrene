package ai.cyrene.mobile

import android.app.UiModeManager
import android.content.Context
import android.os.Build

internal fun syncApplicationNightMode(context: Context, theme: String) {
    if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S) return

    val uiModeManager = context.getSystemService(UiModeManager::class.java)
    val nightMode = when (theme) {
        "dark" -> UiModeManager.MODE_NIGHT_YES
        "light" -> UiModeManager.MODE_NIGHT_NO
        else -> uiModeManager.nightMode
    }
    uiModeManager.setApplicationNightMode(nightMode)
}
