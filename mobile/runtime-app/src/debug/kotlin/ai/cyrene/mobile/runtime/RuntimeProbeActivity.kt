package ai.cyrene.mobile.runtime

import android.app.Activity
import android.os.Bundle
import android.util.Base64
import android.util.Log
import ai.cyrene.mobile.runtime.protocol.GuestOperation
import ai.cyrene.mobile.runtime.protocol.GuestRequest
import org.json.JSONObject
import java.io.File
import java.util.UUID

/** Debug-only, emulator-driven proof that commands execute in the QEMU guest. */
class RuntimeProbeActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Thread {
            val manager = QemuRuntimeManager(this)
            val result = try {
                runCatching {
                val sessionId = "ls_runtime_probe"
                val timeoutMs = intent.getLongExtra("timeout_ms", 180_000).coerceIn(180_000, 900_000)
                val deadline = System.currentTimeMillis() + timeoutMs
                val mount = manager.handle(request(sessionId, GuestOperation.SESSION_MOUNT, JSONObject(), deadline))
                check(mount.status == "success") { mount.message ?: "mount failed" }
                val sharedContent = "shared-by-$sessionId"
                val write = manager.handle(
                    request(
                        sessionId,
                        GuestOperation.FS_WRITE,
                        JSONObject().put("path", "cross-session-proof.txt").put("content", sharedContent),
                        deadline,
                    )
                )
                check(write.status == "success") { write.message ?: "shared write failed" }
                val secondSessionId = "ls_runtime_probe_second"
                val secondMount = manager.handle(
                    request(secondSessionId, GuestOperation.SESSION_MOUNT, JSONObject(), deadline)
                )
                check(secondMount.status == "success") { secondMount.message ?: "second mount failed" }
                val sharedRead = manager.handle(
                    request(
                        secondSessionId,
                        GuestOperation.FS_READ,
                        JSONObject().put("path", "cross-session-proof.txt"),
                        deadline,
                    )
                )
                check(sharedRead.status == "success") { sharedRead.message ?: "shared read failed" }
                check(sharedRead.payload.getString("content") == sharedContent) {
                    "sessions did not observe the same workspace"
                }
                val exported = manager.handle(
                    request(
                        secondSessionId,
                        GuestOperation.ARTIFACT_EXPORT,
                        JSONObject().put("path", "cross-session-proof.txt")
                            .put("offset", 0).put("limit", 256 * 1024),
                        deadline,
                    )
                )
                check(exported.status == "success") { exported.message ?: "artifact export failed" }
                check(
                    Base64.decode(exported.payload.getString("content_base64"), Base64.DEFAULT)
                        .toString(Charsets.UTF_8) == sharedContent
                ) { "artifact export content mismatch" }
                val command = intent.getStringExtra("command_b64")?.let {
                    Base64.decode(it, Base64.DEFAULT).toString(Charsets.UTF_8)
                } ?: intent.getStringExtra("command") ?: DEFAULT_COMMAND
                val execution = manager.handle(
                    request(sessionId, GuestOperation.EXEC_START, JSONObject().put("command", command), deadline)
                )
                check(execution.status == "success") { execution.message ?: "execution failed" }
                val longCommand = "printf 'LONG_COMMAND_OK\\n'; # " + "x".repeat(12_000)
                val longExecution = manager.handle(
                    request(
                        sessionId,
                        GuestOperation.EXEC_START,
                        JSONObject().put("command", longCommand),
                        deadline,
                    )
                )
                check(longExecution.status == "success") { longExecution.message ?: "long command failed" }
                check(longExecution.payload.optInt("exit_code", -1) == 0 &&
                    longExecution.payload.optString("stdout").contains("LONG_COMMAND_OK")) {
                    "QEMU serial channel truncated a command larger than the canonical TTY limit"
                }
                val health = manager.handle(request(sessionId, GuestOperation.HEALTH_CHECK, JSONObject(), deadline))
                JSONObject()
                    .put("mount", mount.payload)
                    .put(
                        "cross_session",
                        JSONObject()
                            .put("writer_session", sessionId)
                            .put("reader_session", secondSessionId)
                            .put("content", sharedRead.payload.getString("content"))
                            .put("same_vm_workspace", true),
                    )
                    .put("artifact_export", exported.payload)
                    .put("execution", execution.payload)
                    .put("long_command", JSONObject().put("bytes", longCommand.toByteArray().size)
                        .put("result", longExecution.payload))
                    .put("health", health.payload)
                    .toString()
                }.fold({ it }, { JSONObject().put("error", it.stackTraceToString()).toString() })
            } finally {
                manager.shutdown()
            }
            File(filesDir, "probe-result.json").writeText(result)
            Log.i(TAG, result)
            // This debug Activity shares the dedicated :qemu process with the
            // production Binder service. End the probe process after persisting
            // its result so no native Limbo/QEMU singleton or disk lock can leak
            // into a subsequent app run.
            runOnUiThread { finishAndRemoveTask() }
            android.os.Process.killProcess(android.os.Process.myPid())
        }.start()
    }

    private fun request(
        sessionId: String,
        operation: GuestOperation,
        payload: JSONObject,
        deadline: Long,
    ) = GuestRequest(
        requestId = "probe_${UUID.randomUUID().toString().replace("-", "")}",
        sessionId = sessionId,
        subagentId = null,
        operation = operation,
        deadlineEpochMs = deadline,
        sequence = System.nanoTime(),
        payload = payload,
    )

    companion object {
        private const val TAG = "CyreneRuntimeProbe"
        private const val DEFAULT_COMMAND =
            "uname -a; cat /etc/alpine-release; id; pwd; " +
                "echo SHARED_VM_OK > shared-proof.txt; " +
                "apk update >/tmp/apk-update.log && echo NETWORK_OK; tail -n 2 /tmp/apk-update.log"
    }
}
