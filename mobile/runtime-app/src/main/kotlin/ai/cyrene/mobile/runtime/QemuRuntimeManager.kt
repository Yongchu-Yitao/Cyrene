package ai.cyrene.mobile.runtime

import android.content.Context
import android.util.Base64
import ai.cyrene.mobile.runtime.protocol.*
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.util.concurrent.ConcurrentHashMap

/**
 * Shared QEMU system VM hosted by the separately-signed Runtime APK.
 * Android owns the control plane and shared workspace; every Bash command runs
 * inside the signed Alpine guest through a private serial protocol.
 */
class QemuRuntimeManager(private val context: Context) {
    private val root = File(context.filesDir, "runtime").apply { mkdirs() }
    private val mounted = ConcurrentHashMap.newKeySet<String>()
    private val vmLock = Any()
    private val desktopLock = Any()
    @Volatile private var image: RuntimeImageBundle? = null
    @Volatile private var vm: QemuVirtualMachine? = null
    @Volatile private var desktopEndpoint: DesktopBackendEndpoint? = null

    fun handle(request: GuestRequest, report: (StartupProgress) -> Unit = {}): GuestResponse {
        if (System.currentTimeMillis() >= request.deadlineEpochMs) {
            return error(request, "deadline_expired", "Request deadline expired")
        }
        return try {
            when (request.operation) {
                GuestOperation.DESKTOP_START -> desktopStart(request, report)
                GuestOperation.DESKTOP_STATUS -> desktopStatus(request)
                GuestOperation.DESKTOP_STOP -> desktopStop(request)
                GuestOperation.HELLO, GuestOperation.HEALTH_CHECK -> health(request, report)
                GuestOperation.SESSION_MOUNT -> mount(request)
                GuestOperation.SESSION_UNMOUNT -> unmount(request)
                GuestOperation.RESOURCE_USAGE -> resourceUsage(request)
                GuestOperation.SHUTDOWN -> shutdown(request)
                GuestOperation.FS_STAT -> fsStat(request)
                GuestOperation.FS_LIST -> fsList(request)
                GuestOperation.FS_READ -> fsRead(request)
                GuestOperation.FS_WRITE -> fsWrite(request)
                GuestOperation.FS_WRITE_CHUNK -> fsWriteChunk(request)
                GuestOperation.FS_PATCH -> fsPatch(request)
                GuestOperation.FS_GLOB -> fsGlob(request)
                GuestOperation.FS_GREP -> fsGrep(request)
                GuestOperation.EXEC_START -> exec(request)
                GuestOperation.EXEC_SIGNAL -> signal(request)
                GuestOperation.EXEC_WAIT, GuestOperation.EXEC_STDIN ->
                    error(request, "unsupported_operation", "This runtime uses bounded synchronous command execution")
                GuestOperation.ARTIFACT_EXPORT -> artifact(request)
            }
        } catch (error: SecurityException) {
            error(request, "workspace_escape", error.message ?: "Path leaves the session workspace")
        } catch (error: IllegalArgumentException) {
            error(request, "invalid_arguments", error.message ?: "Invalid runtime arguments")
        } catch (error: Throwable) {
            error(request, "runtime_error", error.message ?: "Runtime operation failed")
        }
    }

    fun cancel(requestId: String) {
        synchronized(vmLock) {
            vm?.close()
            vm = null
            desktopEndpoint = null
        }
    }

    fun shutdown() {
        synchronized(vmLock) {
            vm?.close()
            vm = null
            desktopEndpoint = null
        }
        mounted.clear()
    }

    private fun health(request: GuestRequest, report: (StartupProgress) -> Unit): GuestResponse {
        val verified = ensureImage(report)
        val guest = vm?.lastHealth
        return success(request, JSONObject()
            .put("runtime_available", true)
            .put("desktop_backend_available", verified.desktopBackend)
            .put("vm_running", vm != null)
            .put("image_signature_verified", true)
            .put("image_version", verified.version)
            .put("engine", verified.engine)
            .put("guest_arch", verified.guestArch)
            .put("guest_network", guest?.network ?: "stopped")
            .put("guest_workspace", guest?.workspace ?: "stopped")
            .put("isolation", "qemu_system_tcg_shared_persistent_root")
            .put("session_running", request.sessionId in mounted)
            .put("active_sessions", mounted.size)
            .put("free_bytes", root.usableSpace))
    }

    private fun desktopStart(request: GuestRequest, report: (StartupProgress) -> Unit): GuestResponse = synchronized(desktopLock) {
        // Reattach to a live backend before touching the image, guest serial channel or systemd.
        desktopEndpoint?.takeIf { it.healthy(healthBudget(request)) }?.let { endpoint ->
            report(StartupProgress("ready"))
            return@synchronized success(request, JSONObject().put("ready", true)
                .put("url", endpoint.url).put("token", endpoint.token)
                .put("auth_header", "X-Cyrene-Token"))
        }
        require(ensureImage(report).desktopBackend) { "Install a Runtime APK built with signed desktop assets" }
        ensureVm(request.deadlineEpochMs, report)
        report(StartupProgress("backend"))
        val endpoint = checkNotNull(desktopEndpoint)
        val result = checkNotNull(vm).execute(
            "/opt/cyrene-mobile/desktop-control start ${quote(endpoint.token)}", request.deadlineEpochMs
        )
        check(result.exitCode == 0) { "Desktop backend failed to start: ${result.stderr}" }
        android.util.Log.i("CyreneStartup", "backend_start_command_complete")
        var nextServiceCheck = System.currentTimeMillis() + 30_000
        var nextProgressCheck = 0L
        var lastProgress = ""
        while (System.currentTimeMillis() < request.deadlineEpochMs) {
            val remaining = (request.deadlineEpochMs - System.currentTimeMillis()).coerceIn(1, 5000).toInt()
            if (endpoint.healthy(remaining)) {
                report(StartupProgress("ready"))
                return@synchronized success(request, JSONObject().put("ready", true)
                    .put("url", endpoint.url).put("token", endpoint.token)
                    .put("auth_header", "X-Cyrene-Token"))
            }
            check(desktopEndpoint === endpoint) { "Workspace runtime was stopped" }
            if (System.currentTimeMillis() >= nextProgressCheck) {
                // The guest HTTP server cannot expose progress before plugin loading.
                // Read only a small atomic snapshot, at most once every five seconds.
                runCatching {
                    val snapshot = checkNotNull(vm).execute(
                        "cat /run/cyrene/startup-progress.json 2>/dev/null || true",
                        minOf(request.deadlineEpochMs, System.currentTimeMillis() + 5_000),
                    ).stdout.trim()
                    if (snapshot.isNotEmpty() && snapshot != lastProgress) {
                        val progress = StartupProgress.parse(snapshot)
                        if (progress.stage in setOf("backend_plugins", "backend_services")) {
                            report(progress)
                            lastProgress = snapshot
                        }
                    }
                }
                nextProgressCheck = System.currentTimeMillis() + 5_000
            }
            if (System.currentTimeMillis() >= nextServiceCheck) {
                val checkResult = checkNotNull(vm).execute(
                    "systemctl show cyrene-desktop -p ActiveState -p SubState -p Result -p ExecMainStatus",
                    minOf(request.deadlineEpochMs, System.currentTimeMillis() + 15_000),
                )
                android.util.Log.i("CyreneStartup", "backend_service: ${checkResult.stdout.trim()}")
                check(!checkResult.stdout.lineSequence().any { it == "ActiveState=failed" || it == "ActiveState=inactive" }) {
                    "Workspace service stopped before becoming ready: ${checkResult.stdout.trim()}"
                }
                nextServiceCheck = System.currentTimeMillis() + 30_000
            }
            // Bound polling so it does not compete with Python initialization.
            Thread.sleep(1000)
        }
        error(request, "desktop_start_timeout", "Backend health deadline expired; inspect guest journalctl -u cyrene-desktop")
    }

    private fun desktopStatus(request: GuestRequest): GuestResponse = success(request, JSONObject()
        .put("available", image?.desktopBackend ?: false)
        .put("ready", desktopEndpoint?.healthy(healthBudget(request)) ?: false))

    private fun healthBudget(request: GuestRequest): Int =
        (request.deadlineEpochMs - System.currentTimeMillis()).coerceIn(1, 5000).toInt()

    private fun desktopStop(request: GuestRequest): GuestResponse = synchronized(vmLock) {
        if (ensureImage().desktopBackend && vm != null) {
            val result = checkNotNull(vm).execute("/opt/cyrene-mobile/desktop-control stop", request.deadlineEpochMs)
            check(result.exitCode == 0) { "Unable to stop desktop backend" }
        }
        success(request, JSONObject().put("ready", false))
    }

    private fun mount(request: GuestRequest): GuestResponse {
        ensureRootDisk()
        val guest = ensureVm(request.deadlineEpochMs)
        mounted += request.sessionId
        return success(request, JSONObject()
            .put("image_version", guest.version)
            .put("engine", ensureImage().engine)
            .put("workspace", "shared")
            .put("network", guest.network)
            .put("workspace_state", guest.workspace)
            .put("already_mounted", false))
    }

    private fun unmount(request: GuestRequest): GuestResponse {
        mounted -= request.sessionId
        return success(request, JSONObject().put("vm_running", vm != null).put("workspace", "shared"))
    }

    private fun shutdown(request: GuestRequest): GuestResponse {
        shutdown()
        return success(request, JSONObject().put("vm_running", false))
    }

    private fun fsStat(request: GuestRequest): GuestResponse {
        val path = path(request, request.payload.getString("path"))
        val result = guest(request, "p=${quote(path)}; if [ -f \"\$p\" ]; then printf 'file %s' \"\$(stat -c %s \"\$p\")\"; " +
            "elif [ -d \"\$p\" ]; then printf 'directory 0'; elif [ -e \"\$p\" ]; then printf 'other 0'; else printf 'missing 0'; fi")
        val parts = result.stdout.trim().split(' ', limit = 2)
        val kind = parts.firstOrNull() ?: "missing"
        val size = parts.getOrNull(1)?.toLongOrNull() ?: 0
        return success(request, JSONObject().put("exists", kind != "missing").put("is_file", kind == "file")
            .put("is_directory", kind == "directory").put("size", size).put("path", path))
    }

    private fun fsList(request: GuestRequest): GuestResponse {
        val directory = path(request, request.payload.optString("path", "."))
        val script = "p=${quote(directory)}; [ -d \"\$p\" ] || exit 44; " +
            "for f in \"\$p\"/* \"\$p\"/.[!.]* \"\$p\"/..?*; do [ -e \"\$f\" ] || continue; " +
            "n=\${f##*/}; b=\$(printf '%s' \"\$n\" | base64 | tr -d '\\n'); " +
            "if [ -d \"\$f\" ]; then printf '%s d 0\\n' \"\$b\"; else printf '%s f %s\\n' \"\$b\" \"\$(stat -c %s \"\$f\")\"; fi; done | head -n 1000"
        val result = guest(request, script)
        val entries = JSONArray()
        result.stdout.lineSequence().filter(String::isNotBlank).forEach { line ->
            val parts = line.split(' ', limit = 3)
            if (parts.size == 3) {
                val name = decode(parts[0])
                val child = if (directory == ".") name else "$directory/$name"
                entries.put(JSONObject().put("name", name).put("path", child)
                    .put("is_directory", parts[1] == "d").put("size", parts[2].toLongOrNull() ?: 0))
            }
        }
        return success(request, JSONObject().put("entries", entries))
    }

    private fun fsRead(request: GuestRequest): GuestResponse {
        val file = path(request, request.payload.getString("path"))
        val offset = request.payload.optLong("offset", 0).coerceAtLeast(0)
        val limit = request.payload.optInt("limit", 256 * 1024).coerceIn(1, 256 * 1024)
        val start = offset + 1
        val result = guest(request, "p=${quote(file)}; [ -f \"\$p\" ] || exit 44; " +
            "s=\$(stat -c %s \"\$p\"); b=\$(tail -c +$start \"\$p\" | head -c $limit | base64 | tr -d '\\n'); printf '%s %s\\n' \"\$s\" \"\$b\"")
        val parts = result.stdout.trimEnd().split(' ', limit = 2)
        val size = parts.firstOrNull()?.toLongOrNull() ?: 0
        val bytes = parts.getOrNull(1)?.takeIf(String::isNotBlank)?.let { Base64.decode(it, Base64.DEFAULT) } ?: byteArrayOf()
        return success(request, JSONObject().put("path", file)
            .put("content", bytes.toString(Charsets.UTF_8)).put("bytes", bytes.size)
            .put("truncated", offset + bytes.size < size))
    }

    private fun fsWrite(request: GuestRequest): GuestResponse {
        val file = path(request, request.payload.getString("path"))
        val content = request.payload.getString("content")
        require(content.toByteArray().size <= 512 * 1024) { "Write exceeds 512 KiB" }
        val payload = Base64.encodeToString(content.toByteArray(), Base64.NO_WRAP)
        val temp = ".cyrene-${request.requestId}.tmp"
        val result = guest(request, "p=${quote(file)}; mkdir -p \"\$(dirname \"\$p\")\"; " +
            "printf '%s' ${quote(payload)} | base64 -d > ${quote(temp)} && mv ${quote(temp)} \"\$p\"; stat -c %s \"\$p\"")
        return success(request, JSONObject().put("path", file).put("bytes", result.stdout.trim().toLong()))
    }

    private fun fsWriteChunk(request: GuestRequest): GuestResponse {
        val file = path(request, request.payload.getString("path"))
        val offset = request.payload.getLong("offset")
        require(offset >= 0) { "Chunk offset must be non-negative" }
        val bytes = Base64.decode(request.payload.getString("content_base64"), Base64.DEFAULT)
        require(bytes.size <= 256 * 1024) { "Chunk exceeds 256 KiB" }
        val encoded = Base64.encodeToString(bytes, Base64.NO_WRAP)
        val target = quote(file)
        val write = if (offset == 0L) {
            "mkdir -p \"\$(dirname \"\$p\")\"; printf '%s' ${quote(encoded)} | base64 -d > \"\$p\""
        } else {
            "[ -f \"\$p\" ] && [ \"\$(stat -c %s \"\$p\")\" -eq $offset ] || exit 45; " +
                "printf '%s' ${quote(encoded)} | base64 -d >> \"\$p\""
        }
        val result = guest(
            request,
            "p=$target; $write; stat -c %s \"\$p\"",
        )
        val nextOffset = result.stdout.trim().toLong()
        require(nextOffset == offset + bytes.size) { "Runtime wrote an unexpected chunk length" }
        return success(
            request,
            JSONObject().put("path", file).put("offset", offset)
                .put("bytes", bytes.size).put("next_offset", nextOffset),
        )
    }

    private fun fsPatch(request: GuestRequest): GuestResponse {
        val file = path(request, request.payload.getString("path"))
        val old = request.payload.getString("old_string")
        val replacement = request.payload.getString("new_string")
        require(old.isNotEmpty()) { "old_string must not be empty" }
        val read = fsRead(request.copy(payload = JSONObject().put("path", file).put("limit", 256 * 1024)))
        require(read.status == "success" && !read.payload.optBoolean("truncated")) { "Patch target is unavailable or too large" }
        val content = read.payload.getString("content")
        val first = content.indexOf(old)
        require(first >= 0) { "old_string was not found" }
        require(content.indexOf(old, first + old.length) < 0) { "old_string is not unique" }
        fsWrite(request.copy(payload = JSONObject().put("path", file)
            .put("content", content.replaceRange(first, first + old.length, replacement))))
        return success(request, JSONObject().put("path", file).put("changed", true))
    }

    private fun fsGlob(request: GuestRequest): GuestResponse {
        ensureMounted(request)
        val pattern = request.payload.getString("pattern").take(500)
        require(pattern.isNotBlank() && !pattern.startsWith("/") && ".." !in pattern.split('/')) { "Glob must stay in workspace" }
        val result = guest(request, "find . -mindepth 1 -path ${quote("./$pattern")} -print 2>/dev/null | head -n 2000 | " +
            "while IFS= read -r p; do printf '%s\\n' \"\$(printf '%s' \"\${p#./}\" | base64 | tr -d '\\n')\"; done")
        val matches = JSONArray()
        result.stdout.lineSequence().filter(String::isNotBlank).forEach { matches.put(decode(it)) }
        return success(request, JSONObject().put("matches", matches))
    }

    private fun fsGrep(request: GuestRequest): GuestResponse {
        val base = path(request, request.payload.optString("path", "."))
        val regex = request.payload.getString("pattern").take(500)
        val result = guest(request, "grep -r -n -E ${quote(regex)} ${quote(base)} 2>/dev/null | head -n 1000")
        val matches = JSONArray()
        val linePattern = Regex("^(.*?):(\\d+):(.*)$")
        result.stdout.lineSequence().forEach { line ->
            linePattern.matchEntire(line)?.let { match ->
                matches.put(JSONObject().put("path", match.groupValues[1].removePrefix("./"))
                    .put("line", match.groupValues[2].toInt()).put("text", match.groupValues[3].take(4000)))
            }
        }
        return success(request, JSONObject().put("matches", matches))
    }

    private fun exec(request: GuestRequest): GuestResponse {
        ensureMounted(request)
        val command = request.payload.getString("command")
        require(command.isNotBlank() && command.length <= 32_768) { "Command is empty or too long" }
        ensureVm(request.deadlineEpochMs)
        val result = checkNotNull(vm) { "QEMU guest is unavailable" }
            .execute(command, request.deadlineEpochMs)
        return success(request, JSONObject().put("stdout", result.stdout.take(MAX_OUTPUT))
            .put("stderr", result.stderr.take(MAX_OUTPUT)).put("exit_code", result.exitCode)
            .put("command", command.take(500)).put("executed_in", "qemu_guest"))
    }

    private fun signal(request: GuestRequest): GuestResponse {
        val running = vm != null
        if (running) cancel(request.payload.optString("request_id"))
        return success(request, JSONObject().put("stopped", running).put("vm_reboot_required", running))
    }

    private fun artifact(request: GuestRequest): GuestResponse {
        val path = request.payload.getString("path")
        val stat = fsStat(request.copy(payload = JSONObject().put("path", path)))
        require(stat.payload.optBoolean("is_file")) { "Artifact does not exist" }
        val offset = request.payload.optLong("offset", 0).coerceAtLeast(0)
        val limit = request.payload.optInt("limit", 256 * 1024).coerceIn(1, 256 * 1024)
        val file = stat.payload.getString("path")
        val size = stat.payload.getLong("size")
        require(offset <= size) { "Artifact offset exceeds file size" }
        val result = guest(request, "p=${quote(file)}; tail -c +${offset + 1} \"\$p\" | head -c $limit | base64 | tr -d '\\n'")
        val bytes = result.stdout.takeIf(String::isNotBlank)?.let { Base64.decode(it, Base64.DEFAULT) }
            ?: byteArrayOf()
        return success(request, JSONObject().put("path", file).put("size", size)
            .put("offset", offset).put("bytes", bytes.size)
            .put("content_base64", Base64.encodeToString(bytes, Base64.NO_WRAP))
            .put("complete", offset + bytes.size >= size))
    }

    private fun resourceUsage(request: GuestRequest): GuestResponse {
        val bytes = if (request.sessionId in mounted && vm != null) {
            guest(request, "du -sk . | awk '{print \$1 * 1024}'").stdout.trim().toLongOrNull() ?: 0
        } else 0
        return success(request, JSONObject().put("active_sessions", mounted.size).put("vm_running", vm != null)
            .put("workspace_bytes", bytes).put("workspace_capacity_bytes", rootfsDisk().length())
            .put("free_bytes", root.usableSpace))
    }

    private fun ensureMounted(request: GuestRequest) {
        require(request.sessionId in mounted) { "Runtime session is not mounted" }
    }

    private fun ensureImage(report: (StartupProgress) -> Unit = {}): RuntimeImageBundle = image ?: synchronized(vmLock) {
        image ?: RuntimeImageVerifier(context).verifyAndExtract(report).also { image = it }
    }

    private fun ensureVm(deadlineEpochMs: Long, report: (StartupProgress) -> Unit = {}): VmHealth = synchronized(vmLock) {
        val current = vm
        if (current != null) return@synchronized current.ping(deadlineEpochMs)
        ensureRootDisk(report)
        report(StartupProgress("boot"))
        val endpoint = if (ensureImage().desktopBackend) DesktopBackendEndpoint() else null
        val created = QemuVirtualMachine(context, ensureImage(), rootfsDisk(), endpoint?.port)
        try {
            created.start(deadlineEpochMs).also { vm = created; desktopEndpoint = endpoint }
        } catch (error: Throwable) {
            created.close()
            throw IllegalStateException("Unable to boot signed Linux VM: ${error.message}", error)
        }
    }

    private fun ensureRootDisk(report: (StartupProgress) -> Unit = {}) {
        val disk = rootfsDisk()
        if (disk.isFile) return
        val template = ensureImage().rootfsTemplate
        disk.parentFile?.mkdirs()
        val temp = File(disk.parentFile, ".rootfs.ext4.tmp")
        report(StartupProgress("disk", 0, template.length()))
        template.inputStream().use { input -> SparseDisk.copy(input, temp) { done -> report(StartupProgress("disk", done, template.length())) } }
        check(temp.renameTo(disk) || run {
            temp.inputStream().use { SparseDisk.copy(it, disk) }; temp.delete()
        }) {
            "Unable to create persistent Linux root disk"
        }
    }

    private fun rootfsDisk() = File(root, "vm-state/${ensureImage().version}-rootfs.ext4")

    private fun path(request: GuestRequest, raw: String): String {
        ensureMounted(request)
        require(raw.isNotBlank() && raw.indexOf('\u0000') < 0) { "Path is invalid" }
        if (raw.startsWith('/') || ".." in raw.split('/')) {
            throw SecurityException("Path leaves the shared workspace")
        }
        return raw.split('/').filter { it.isNotBlank() && it != "." }.joinToString("/").ifBlank { "." }
    }

    private fun guest(request: GuestRequest, command: String): VmCommandResult {
        ensureMounted(request)
        ensureVm(request.deadlineEpochMs)
        val result = checkNotNull(vm).execute(command, request.deadlineEpochMs)
        require(result.exitCode == 0) { result.stderr.ifBlank { "Linux guest operation failed with exit ${result.exitCode}" } }
        return result
    }

    private fun quote(value: String): String = "'" + value.replace("'", "'\\''") + "'"

    private fun decode(value: String): String = Base64.decode(value, Base64.DEFAULT).toString(Charsets.UTF_8)

    private fun success(request: GuestRequest, payload: JSONObject = JSONObject()) =
        GuestResponse(request.requestId, "success", payload)

    private fun error(request: GuestRequest, type: String, message: String) =
        GuestResponse(request.requestId, "error", errorType = type, message = message)

    companion object {
        private const val MAX_OUTPUT = 512 * 1024
    }
}
