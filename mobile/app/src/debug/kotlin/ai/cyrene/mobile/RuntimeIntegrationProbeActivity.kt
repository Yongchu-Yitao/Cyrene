package ai.cyrene.mobile

import android.app.Activity
import android.os.Bundle
import android.util.Log
import ai.cyrene.mobile.desktop.RuntimeCompanionClient
import ai.cyrene.mobile.runtime.protocol.GuestOperation
import kotlinx.coroutines.runBlocking
import org.json.JSONObject
import java.io.File

/** Debug-only proof of the full main-app -> Binder -> QEMU guest path. */
class RuntimeIntegrationProbeActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Thread {
            val result = runCatching {
                RuntimeCompanionClient(this).use { runtime ->
                    runBlocking {
                        val writerSession = "ls_binder_probe_writer"
                        val readerSession = "ls_binder_probe_reader"
                        val content = "written-through-signed-binder"
                        val mountWriter = runtime.submit(
                            writerSession,
                            GuestOperation.SESSION_MOUNT,
                            timeoutMs = 180_000,
                        )
                        check(mountWriter.status == "success") { mountWriter.message ?: "writer mount failed" }
                        intent.getStringExtra("command_b64")?.let { encoded ->
                            val command = android.util.Base64.decode(encoded, android.util.Base64.DEFAULT).toString(Charsets.UTF_8)
                            val execution = runtime.submit(writerSession, GuestOperation.EXEC_START,
                                JSONObject().put("command", command), timeoutMs = 120_000)
                            return@runBlocking JSONObject().put("status", execution.status)
                                .put("message", execution.message).put("execution", execution.payload).toString()
                        }
                        val write = runtime.submit(
                            writerSession,
                            GuestOperation.FS_WRITE,
                            JSONObject().put("path", "binder-proof.txt").put("content", content),
                            timeoutMs = 30_000,
                        )
                        check(write.status == "success") { write.message ?: "write failed" }
                        val mountReader = runtime.submit(
                            readerSession,
                            GuestOperation.SESSION_MOUNT,
                            timeoutMs = 30_000,
                        )
                        check(mountReader.status == "success") { mountReader.message ?: "reader mount failed" }
                        val read = runtime.submit(
                            readerSession,
                            GuestOperation.FS_READ,
                            JSONObject().put("path", "binder-proof.txt"),
                            timeoutMs = 30_000,
                        )
                        check(read.status == "success" && read.payload.getString("content") == content) {
                            read.message ?: "cross-session read mismatch"
                        }
                        val traversal = runtime.submit(
                            readerSession,
                            GuestOperation.FS_READ,
                            JSONObject().put("path", "../etc/passwd"),
                            timeoutMs = 30_000,
                        )
                        check(traversal.status == "error") { "workspace traversal was not rejected" }
                        val execution = runtime.submit(
                            readerSession,
                            GuestOperation.EXEC_START,
                            JSONObject().put(
                                "command",
                                "uname -a; printf 'shared='; cat binder-proof.txt; command -v apk",
                            ),
                            timeoutMs = 30_000,
                        )
                        check(execution.status == "success") { execution.message ?: "guest execution failed" }
                        val longCommand = "printf 'BINDER_LONG_COMMAND_OK\\n'; # " + "y".repeat(12_000)
                        val longExecution = runtime.submit(
                            readerSession,
                            GuestOperation.EXEC_START,
                            JSONObject().put("command", longCommand),
                            timeoutMs = 30_000,
                        )
                        check(longExecution.status == "success" &&
                            longExecution.payload.optInt("exit_code", -1) == 0 &&
                            longExecution.payload.optString("stdout").contains("BINDER_LONG_COMMAND_OK")) {
                            longExecution.message ?: "long Binder command was truncated"
                        }
                        val health = runtime.submit(
                            readerSession,
                            GuestOperation.HEALTH_CHECK,
                            timeoutMs = 30_000,
                        )
                        JSONObject()
                            .put("binder_connected", true)
                            .put("mount_writer", mountWriter.payload)
                            .put("mount_reader", mountReader.payload)
                            .put("cross_session_read", read.payload)
                            .put(
                                "workspace_escape_rejected",
                                JSONObject()
                                    .put("rejected", true)
                                    .put("error_type", traversal.errorType),
                            )
                            .put("execution", execution.payload)
                            .put("long_command", JSONObject().put("bytes", longCommand.toByteArray().size)
                                .put("result", longExecution.payload))
                            .put("health", health.payload)
                            .toString()
                    }
                }
            }.fold({ it }, { JSONObject().put("error", it.stackTraceToString()).toString() })
            File(filesDir, "runtime-integration-probe.json").writeText(result)
            Log.i(TAG, result)
            runOnUiThread { finish() }
        }.start()
    }

    companion object { private const val TAG = "CyreneBinderProbe" }
}
