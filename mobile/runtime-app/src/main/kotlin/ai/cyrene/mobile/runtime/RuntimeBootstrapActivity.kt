package ai.cyrene.mobile.runtime

import android.app.Activity
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.os.Bundle
import android.os.IBinder

/**
 * Invisible first-run bridge for Android variants that refuse to bind directly to a newly
 * installed service-only package. It briefly keeps the local Runtime service bound so the
 * foreground Cyrene Mobile client can establish its own connection.
 */
class RuntimeBootstrapActivity : Activity() {
    private var bound = false
    private val connection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName?, service: IBinder?) = finishSoon()
        override fun onServiceDisconnected(name: ComponentName?) = Unit
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.setDimAmount(0f)
        val intent = Intent(CyreneRuntimeService.ACTION_BIND)
            .setComponent(ComponentName(this, CyreneRuntimeService::class.java))
        bound = bindService(intent, connection, Context.BIND_AUTO_CREATE)
        finishSoon()
    }

    private fun finishSoon() {
        window.decorView.postDelayed({ finish() }, HOLD_SERVICE_MS)
    }

    override fun onDestroy() {
        if (bound) runCatching { unbindService(connection) }
        bound = false
        super.onDestroy()
    }

    companion object { private const val HOLD_SERVICE_MS = 2_000L }
}
