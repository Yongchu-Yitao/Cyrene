package ai.cyrene.mobile.runtime

import android.net.LocalSocket
import android.net.LocalSocketAddress
import org.json.JSONObject
import java.io.File

internal class QmpConnection(path: File, private val deadline: Long) : AutoCloseable {
    private val socket = LocalSocket()
    private val reader: java.io.BufferedReader
    private val writer: java.io.BufferedWriter
    init {
        socket.connect(LocalSocketAddress(path.absolutePath, LocalSocketAddress.Namespace.FILESYSTEM))
        socket.soTimeout = remaining()
        reader = socket.inputStream.bufferedReader()
        writer = socket.outputStream.bufferedWriter()
        check(JSONObject(line()).has("QMP")) { "Missing QMP greeting" }
        command("qmp_capabilities")
    }
    private fun remaining() = (deadline - System.currentTimeMillis()).coerceIn(1, Int.MAX_VALUE.toLong()).toInt()
    private fun line(): String {
        check(System.currentTimeMillis() < deadline) { "QMP deadline expired" }
        socket.soTimeout = remaining()
        return reader.readLine() ?: error("QMP disconnected")
    }
    fun command(name: String, args: JSONObject? = null): Any {
        writer.write(JSONObject().put("execute", name).apply { if (args != null) put("arguments", args) }.toString())
        writer.newLine(); writer.flush()
        while (true) {
            val reply = JSONObject(line())
            check(!reply.has("error")) { "QMP $name failed: ${reply.optJSONObject("error")?.optString("desc")}" }
            if (reply.has("return")) return reply.get("return")
        }
    }
    fun monitor(text: String) {
        val result = command("human-monitor-command", JSONObject().put("command-line", text)).toString()
        check(result.isBlank()) { "QEMU snapshot failed: $result" }
    }
    override fun close() = socket.close()
}
