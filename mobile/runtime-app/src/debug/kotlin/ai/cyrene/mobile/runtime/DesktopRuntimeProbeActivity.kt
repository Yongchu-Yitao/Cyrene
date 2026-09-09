package ai.cyrene.mobile.runtime

import android.app.Activity
import android.os.Bundle
import android.util.Base64
import ai.cyrene.mobile.runtime.protocol.*
import org.json.JSONObject
import java.io.File
import java.util.UUID

/** No model keys or response tokens are written to the probe result. */
class DesktopRuntimeProbeActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val timeoutMs = intent.getLongExtra("timeout_ms", 300_000).coerceIn(300_000, 900_000)
        Thread {
            val manager = QemuRuntimeManager(this)
            val output = File(filesDir, "desktop-runtime-probe.json")
            fun progress(stage: String) = output.writeText(JSONObject()
                .put("stage", stage).put("completed", false)
                .put("time_ms", System.currentTimeMillis()).toString(2))
            fun request(operation: GuestOperation, payload: JSONObject = JSONObject()) = manager.handle(
                GuestRequest("probe_${UUID.randomUUID()}", "ls_desktop_probe", null, operation,
                    System.currentTimeMillis() + timeoutMs, 1, payload)
            ).also { check(it.status == "success") { it.message ?: "Probe failed" } }
            val result = try {
                progress("starting_backend")
                request(GuestOperation.DESKTOP_START)
                progress("backend_ready")
                request(GuestOperation.SESSION_MOUNT)
                val script = assets.open("full_runtime.py").use { it.readBytes() }
                    .let { Base64.encodeToString(it, Base64.NO_WRAP) }
                progress("testing_tools")
                val execution = request(GuestOperation.EXEC_START, JSONObject().put("command",
                    "printf '%s' '$script' | base64 -d | python"))
                JSONObject().put("success", execution.payload.getInt("exit_code") == 0)
                    .put("backend", request(GuestOperation.DESKTOP_STATUS).payload)
                    .put("tools", execution.payload)
            } catch (failure: Throwable) {
                JSONObject().put("success", false).put("error", failure.message)
            }
            // Persist findings before native shutdown so a stuck emulator does
            // not hide a completed test or its failure from device diagnostics.
            output.writeText(result.put("stage", "stopping_vm").toString(2))
            manager.shutdown()
            output.writeText(result.put("stage", "completed").put("completed", true).toString(2))
            runOnUiThread { finish() }
        }.start()
    }
}
