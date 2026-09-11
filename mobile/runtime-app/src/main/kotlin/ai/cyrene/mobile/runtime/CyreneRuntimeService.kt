package ai.cyrene.mobile.runtime

import android.app.Service
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import ai.cyrene.mobile.runtime.protocol.*
import org.json.JSONObject
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.Executors
import java.util.concurrent.Future

class CyreneRuntimeService : Service() {
    private val executor = Executors.newCachedThreadPool()
    private val pending = ConcurrentHashMap<String, Future<*>>()
    private lateinit var runtime: QemuRuntimeManager

    override fun onCreate() {
        super.onCreate()
        runtime = QemuRuntimeManager(this)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_HIBERNATE_DESKTOP) {
            executor.submit {
                try { runtime.hibernate() }
                catch (failure: Throwable) { android.util.Log.e("CyreneStartup", "hibernate_failed; next start uses current disk", failure) }
                finally {
                    stopForeground(STOP_FOREGROUND_REMOVE)
                    // Only this :qemu process exits. A paused VM must not run shutdown code.
                    android.os.Process.killProcess(android.os.Process.myPid())
                }
            }
            return START_NOT_STICKY
        }
        if (intent?.action == ACTION_STOP_DESKTOP) {
            executor.submit {
                runtime.shutdown()
                stopForeground(STOP_FOREGROUND_REMOVE)
                stopSelf()
            }
            return START_NOT_STICKY
        }
        if (intent?.action == ACTION_KEEP_DESKTOP) {
            getSystemService(NotificationManager::class.java).createNotificationChannel(
                NotificationChannel("desktop-runtime", "Cyrene Desktop Runtime", NotificationManager.IMPORTANCE_LOW)
            )
            val stop = PendingIntent.getService(this, 0,
                Intent(this, CyreneRuntimeService::class.java).setAction(ACTION_STOP_DESKTOP),
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
            val hibernate = PendingIntent.getService(this, 1,
                Intent(this, CyreneRuntimeService::class.java).setAction(ACTION_HIBERNATE_DESKTOP),
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
            val notification = Notification.Builder(this, "desktop-runtime")
                .setSmallIcon(android.R.drawable.stat_notify_sync)
                .setContentTitle("Cyrene Desktop Runtime")
                .setContentText("Local Linux runtime is active")
                .setOngoing(true)
                .addAction(Notification.Action.Builder(null, getString(R.string.runtime_hibernate), hibernate).build())
                .addAction(Notification.Action.Builder(null, "Stop", stop).build())
                .build()
            if (Build.VERSION.SDK_INT >= 34) {
                startForeground(4242, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
            } else {
                startForeground(4242, notification)
            }
        }
        // Do not silently resurrect a VM after OS termination or user stop.
        return START_NOT_STICKY
    }

    private val binder = object : IRuntimeService.Stub() {
        override fun submit(requestJson: String, callback: IRuntimeCallback) {
            val requestId = runCatching { JSONObject(requestJson).optString("request_id") }.getOrDefault("")
            val future = executor.submit {
                val response = try {
                    val request = GuestRequest.parse(requestJson)
                    var lastStage = ""
                    var lastUpdate = 0L
                    val started = android.os.SystemClock.elapsedRealtime()
                    runtime.handle(request) { progress ->
                        val now = android.os.SystemClock.elapsedRealtime()
                        val changed = progress.stage != lastStage
                        if (changed) android.util.Log.i("CyreneStartup", "${progress.stage} at ${now - started}ms (${request.operation.wireName})")
                        if (changed || now - lastUpdate >= 200 || progress.percent == 100) {
                            runCatching { callback.onProgress(request.requestId, progress.toJson()) }
                            lastStage = progress.stage; lastUpdate = now
                        }
                    }.also {
                        android.util.Log.i("CyreneStartup", "${request.operation.wireName} ${it.status} after ${android.os.SystemClock.elapsedRealtime() - started}ms")
                        if (request.operation == GuestOperation.DESKTOP_STOP && it.status == "success") {
                            stopForeground(STOP_FOREGROUND_REMOVE)
                            stopSelf()
                        }
                    }
                } catch (failure: GuestProtocolException) {
                    GuestResponse(requestId, "error", errorType = failure.code, message = failure.message)
                } catch (error: Throwable) {
                    GuestResponse(requestId, "error", errorType = "runtime_internal_error", message = error.message ?: "Runtime failed")
                }
                runCatching { callback.onResult(response.toJson()) }
                pending.remove(requestId)
            }
            if (requestId.isNotBlank()) pending[requestId] = future
        }

        override fun cancel(requestId: String) {
            runtime.cancel(requestId)
            pending.remove(requestId)?.cancel(true)
        }
    }

    override fun onBind(intent: Intent?): IBinder? =
        if (intent?.action == ACTION_BIND) binder else null

    override fun onDestroy() {
        pending.values.forEach { it.cancel(true) }
        executor.shutdownNow()
        // Guest shutdown may take seconds; never block Android's main thread.
        Thread { runtime.shutdown() }.start()
        super.onDestroy()
    }

    companion object {
        const val ACTION_BIND = "ai.cyrene.mobile.runtime.BIND"
        const val ACTION_KEEP_DESKTOP = "ai.cyrene.mobile.runtime.KEEP_DESKTOP"
        const val ACTION_HIBERNATE_DESKTOP = "ai.cyrene.mobile.runtime.HIBERNATE_DESKTOP"
        const val ACTION_STOP_DESKTOP = "ai.cyrene.mobile.runtime.STOP_DESKTOP"
    }
}
