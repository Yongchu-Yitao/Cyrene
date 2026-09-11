package ai.cyrene.mobile.runtime

import java.io.File
import java.io.FileOutputStream
import java.util.Properties

/** A checkpoint is a one-use capability, consumed before QEMU can run again. */
internal class CheckpointStore(private val file: File, private val syncDirectory: () -> Unit = {}) {
    data class Saved(val fingerprint: String, val token: String)
    fun consume(fingerprint: String): Saved? {
        val saved = runCatching {
            val p = Properties().apply { file.inputStream().use { load(it) } }
            Saved(p.getProperty("fingerprint"), p.getProperty("token"))
        }.getOrNull()
        invalidate()
        return saved?.takeIf { it.fingerprint == fingerprint && it.token.matches(Regex("[A-Za-z0-9_-]{43}")) }
    }
    fun invalidate() {
        if (file.exists()) check(file.delete()) { "Unable to invalidate old resume state" }
        syncDirectory()
    }
    fun publish(saved: Saved) {
        val temp = File(file.parentFile, file.name + ".tmp")
        FileOutputStream(temp).use { output ->
            Properties().apply {
                setProperty("fingerprint", saved.fingerprint); setProperty("token", saved.token)
            }.store(output, null)
            output.fd.sync()
        }
        check(temp.renameTo(file)) { "Unable to publish resume state" }
        syncDirectory()
    }
}
