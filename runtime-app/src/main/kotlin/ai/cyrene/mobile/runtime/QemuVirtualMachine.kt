package ai.cyrene.mobile.runtime

import android.content.Context
import android.net.ConnectivityManager
import android.net.LocalSocket
import android.net.LocalSocketAddress
import android.os.Build
import android.util.Base64
import android.util.Log
import com.max2idea.android.limbo.jni.VMExecutor
import java.io.BufferedReader
import java.io.BufferedWriter
import java.io.File
import java.io.InputStreamReader
import java.io.OutputStreamWriter
import java.io.RandomAccessFile
import java.nio.channels.FileLock
import java.util.UUID
import java.util.concurrent.Executors
import java.util.concurrent.Future
import java.util.concurrent.TimeUnit

data class VmHealth(
    val version: String,
    val network: String,
    val workspace: String,
)

data class VmCommandResult(val stdout: String, val stderr: String, val exitCode: Int)

class QemuVirtualMachine(
    private val context: Context,
    private val bundle: RuntimeImageBundle,
    private val rootfsDisk: File,
    private val desktopPort: Int? = null,
) : AutoCloseable {
    private val executor = VMExecutor()
    private val vmThread = nativeVmThread
    private val socketPath = File(context.filesDir, "qemu-serial.sock")
    private var future: Future<*>? = null
    private var socket: LocalSocket? = null
    private var reader: BufferedReader? = null
    private var writer: BufferedWriter? = null
    private var ownershipFile: RandomAccessFile? = null
    private var ownershipLock: FileLock? = null
    @Volatile private var nativeResult: String? = null
    @Volatile private var started = false
    @Volatile var lastHealth: VmHealth? = null
        private set

    @Synchronized
    fun start(deadlineEpochMs: Long): VmHealth {
        if (started) return ping(deadlineEpochMs)
        require(Build.SUPPORTED_64_BIT_ABIS.any { it == "arm64-v8a" || it == "x86_64" }) {
            "QEMU runtime requires a 64-bit ARM or x86 Android device"
        }
        // A debug probe or a recreated service must not open a writable disk
        // while another process still owns the native VM and serial socket.
        ownershipFile = RandomAccessFile(File(context.filesDir, "qemu-process.lock"), "rw")
        ownershipLock = ownershipFile!!.channel.tryLock()
        check(ownershipLock != null) { "Another Runtime process owns the Linux VM" }
        rootfsDisk.parentFile?.mkdirs()
        if (socketPath.exists()) socketPath.delete()
        require(bundle.guestArch in setOf("x86_64", "aarch64")) { "Unsupported guest architecture" }
        val armGuest = bundle.guestArch == "aarch64"
        val libraryPath = File(context.applicationInfo.nativeLibraryDir, "libqemu-system-${bundle.guestArch}.so")
        val libraryLocation = libraryPath.takeIf(File::isFile)?.absolutePath ?: libraryPath.name
        val upstreamDns = upstreamDnsAddresses()
        File(bundle.directory, "etc/resolv.conf").apply {
            parentFile?.mkdirs()
            writeText(upstreamDns.joinToString(separator = "\n", postfix = "\n") { "nameserver $it" })
        }
        Log.i(SERIAL_TAG, "QEMU slirp upstream DNS: ${upstreamDns.joinToString()}")
        val parameters = arrayOf(
            libraryPath.name,
            "-machine", if (armGuest) "virt" else "pc",
            "-cpu", if (armGuest) "cortex-a72" else if (bundle.desktopBackend) "max" else "qemu64",
            "-smp", "1",
            "-m", bundle.memoryMiB.toString(),
            "-kernel", bundle.kernel.absolutePath,
            "-initrd", bundle.initramfs.absolutePath,
            "-append", bundle.kernelAppend,
            "-nodefaults",
            "-display", "none",
            "-monitor", "none",
            "-chardev", "socket,id=cyrene,path=${socketPath.absolutePath},server=on,wait=off",
            "-serial", "chardev:cyrene",
            "-drive", "if=none,id=rootfs,file=${rootfsDisk.absolutePath},format=raw,cache=writeback",
            "-device", "virtio-blk-pci,drive=rootfs",
            // The guest uses QEMU's DNS proxy at 10.0.2.3. Limbo redirects
            // slirp's host /etc/resolv.conf read into bundle.directory, where
            // the active Android network's upstream resolvers were written
            // above. Do not pass dns=<upstream>: that option changes the
            // *guest-visible proxy address* and would make 10.0.2.3 stop
            // answering whenever a real phone reports a different resolver.
            "-netdev", "user,id=net0,restrict=off" +
                (desktopPort?.let { ",hostfwd=tcp:127.0.0.1:$it-:4243" } ?: ""),
            "-device", "virtio-net-pci,netdev=net0",
            // Headless ARM guests otherwise have very few entropy sources;
            // Python crypto and systemd random-seed may wait for CRNG readiness.
            "-object", "rng-builtin,id=cyrene-rng",
            "-device", "virtio-rng-pci,rng=cyrene-rng",
            "-no-reboot",
            "-overcommit", "mem-lock=off",
            "-L", bundle.directory.absolutePath,
        )
        future = vmThread.submit {
            nativeResult = runCatching {
                executor.start(
                    context.filesDir.absolutePath,
                    bundle.directory.absolutePath,
                    libraryPath.name,
                    libraryLocation,
                    0,
                    parameters,
                )
            }.fold({ it }, { "QEMU failed: ${it.message}" })
        }
        connect(deadlineEpochMs)
        started = true
        val health = waitForReady(deadlineEpochMs)
        // Linux's canonical TTY discipline truncates lines around 4 KiB. The
        // serial protocol carries base64 commands on one line, so switch the
        // guest console to raw line delivery before accepting real work.
        val console = if (armGuest) "ttyAMA0" else "ttyS0"
        val serialSetup = execute("stty -icanon -echo min 1 time 0 </dev/$console", deadlineEpochMs)
        check(serialSetup.exitCode == 0) {
            "Unable to configure the QEMU command channel: ${serialSetup.stderr.ifBlank { serialSetup.stdout }}"
        }
        return health
    }

    @Synchronized
    fun ping(deadlineEpochMs: Long): VmHealth {
        ensureRunning()
        val id = token()
        send("CYRENE_PING $id -")
        val line = readUntil(deadlineEpochMs) { it.startsWith("CYRENE_PONG $id ") }
        val parts = line.split(' ', limit = 5)
        check(parts.size == 5) { "Malformed QEMU guest health response" }
        return VmHealth(parts[2], parts[3], parts[4]).also { lastHealth = it }
    }

    private fun waitForReady(deadlineEpochMs: Long): VmHealth {
        val line = readUntil(deadlineEpochMs) { it.startsWith("CYRENE_VM_READY ") }
        val parts = line.split(' ', limit = 4)
        check(parts.size == 4) { "Malformed QEMU guest ready response" }
        return VmHealth(parts[1], parts[2], parts[3]).also { lastHealth = it }
    }

    @Synchronized
    fun execute(command: String, deadlineEpochMs: Long): VmCommandResult {
        ensureRunning()
        require(command.toByteArray(Charsets.UTF_8).size <= MAX_COMMAND_BYTES) {
            "QEMU guest command exceeds the ${MAX_COMMAND_BYTES / 1024} KiB protocol limit"
        }
        val id = token()
        val payload = Base64.encodeToString(command.toByteArray(), Base64.NO_WRAP)
        send("CYRENE_EXEC $id $payload")
        val line = try {
            readUntil(deadlineEpochMs) { it.startsWith("CYRENE_RESULT $id ") }
        } catch (error: Throwable) {
            close()
            throw error
        }
        val parts = line.split(' ', limit = 5)
        check(parts.size == 5) { "Malformed QEMU guest command response" }
        fun decode(value: String): String = if (value == "-") "" else
            Base64.decode(value, Base64.DEFAULT).toString(Charsets.UTF_8)
        return VmCommandResult(decode(parts[3]), decode(parts[4]), parts[2].toInt())
    }

    @Synchronized
    override fun close() {
        if (started) {
            runCatching {
                val id = token()
                send("CYRENE_SHUTDOWN $id -")
            }
            // Give the guest time to flush SQLite and stop its backend before
            // terminating the emulator. A stuck guest still has a bounded exit.
            val shutdownSeconds = if (bundle.guestArch == "aarch64") 30L else 10L
            val stopped = runCatching { future?.get(shutdownSeconds, TimeUnit.SECONDS); true }.getOrDefault(false)
            if (!stopped) runCatching { executor.stop(0) }
        }
        runCatching { reader?.close() }
        runCatching { writer?.close() }
        runCatching { socket?.close() }
        reader = null
        writer = null
        socket = null
        started = false
        future?.let { runCatching { it.get(5, TimeUnit.SECONDS) } }
        future = null
        // Limbo dlcloses QEMU before returning, but QEMU has registered pthread
        // TLS destructors inside that library. Terminating this worker would
        // call unmapped code. Reuse one process-lifetime worker instead.
        runCatching { ownershipLock?.release() }
        runCatching { ownershipFile?.close() }
        ownershipLock = null
        ownershipFile = null
    }

    fun describeFailure(): String = nativeResult ?: "QEMU guest is not running"

    private fun connect(deadlineEpochMs: Long) {
        var lastError: Throwable? = null
        while (System.currentTimeMillis() < deadlineEpochMs) {
            if (future?.isDone == true) error(nativeResult ?: "QEMU stopped before its serial channel opened")
            val candidate = LocalSocket()
            try {
                candidate.connect(LocalSocketAddress(socketPath.absolutePath, LocalSocketAddress.Namespace.FILESYSTEM))
                socket = candidate
                reader = BufferedReader(InputStreamReader(candidate.inputStream, Charsets.UTF_8))
                writer = BufferedWriter(OutputStreamWriter(candidate.outputStream, Charsets.UTF_8))
                return
            } catch (error: Throwable) {
                lastError = error
                runCatching { candidate.close() }
                Thread.sleep(100)
            }
        }
        error("Timed out connecting to QEMU guest serial channel: ${lastError?.message}")
    }

    private fun send(line: String) {
        writer?.apply { write(line); newLine(); flush() } ?: error("QEMU serial channel is unavailable")
    }

    private fun readUntil(deadlineEpochMs: Long, predicate: (String) -> Boolean): String {
        while (System.currentTimeMillis() < deadlineEpochMs) {
            val remaining = (deadlineEpochMs - System.currentTimeMillis()).coerceIn(1, 2_000).toInt()
            socket?.soTimeout = remaining
            val line = try {
                reader?.readLine()
            } catch (_: java.io.InterruptedIOException) {
                null
            } catch (error: java.io.IOException) {
                if (error.message?.contains("Try again", ignoreCase = true) == true ||
                    error.message?.contains("EAGAIN", ignoreCase = true) == true
                ) null else throw error
            }
            if (line != null) {
                Log.d(SERIAL_TAG, line.take(4000))
                if (predicate(line)) return line
            }
            if (future?.isDone == true) error(nativeResult ?: "QEMU stopped unexpectedly")
        }
        error("QEMU guest request timed out")
    }

    private fun ensureRunning() {
        check(started && future?.isDone != true) { describeFailure() }
    }

    private fun token(): String = UUID.randomUUID().toString().replace("-", "")

    private fun upstreamDnsAddresses(): List<String> {
        val connectivity = context.getSystemService(ConnectivityManager::class.java)
        return runCatching {
            connectivity.getLinkProperties(connectivity.activeNetwork)
                ?.dnsServers
                ?.mapNotNull { it.hostAddress?.substringBefore('%') }
                ?.filter(String::isNotBlank)
                ?.distinct()
                ?.take(3)
                ?.takeIf { it.isNotEmpty() }
        }.getOrNull() ?: listOf("1.1.1.1")
    }

    companion object {
        private val nativeVmThread = Executors.newSingleThreadExecutor { work ->
            Thread(work, "CyreneNativeVM").apply { isDaemon = true }
        }
        private const val MAX_COMMAND_BYTES = 256 * 1024
        private const val SERIAL_TAG = "CyreneQemuSerial"
    }
}
