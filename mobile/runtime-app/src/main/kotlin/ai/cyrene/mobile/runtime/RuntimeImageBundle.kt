package ai.cyrene.mobile.runtime

import android.content.Context
import org.json.JSONObject
import java.io.File
import java.security.KeyFactory
import java.security.MessageDigest
import java.security.Signature
import java.security.spec.X509EncodedKeySpec
import java.util.Base64
import java.util.zip.GZIPInputStream

data class RuntimeImageBundle(
    val directory: File,
    val kernel: File,
    val initramfs: File,
    val rootfsTemplate: File,
    val version: String,
    val engine: String,
    val guestArch: String,
    val desktopBackend: Boolean = false,
    val memoryMiB: Int = 256,
    val kernelAppend: String = "console=ttyS0 rdinit=/init panic=-1 loglevel=4",
)

class RuntimeImageVerifier(private val context: Context) {
    fun verifyAndExtract(): RuntimeImageBundle {
        val assets = context.assets
        val manifestBytes = assets.open("runtime/manifest.json").use { it.readBytes() }
        val signatureBytes = assets.open("runtime/manifest.sig").use { it.readBytes() }
        val publicKeyPem = assets.open("runtime/runtime-public-key.pem").bufferedReader().use { it.readText() }
        val publicKeyBytes = Base64.getMimeDecoder().decode(
            publicKeyPem.replace("-----BEGIN PUBLIC KEY-----", "")
                .replace("-----END PUBLIC KEY-----", "")
        )
        val publicKey = KeyFactory.getInstance("RSA").generatePublic(X509EncodedKeySpec(publicKeyBytes))
        val signature = Signature.getInstance("SHA256withRSA")
        signature.initVerify(publicKey)
        signature.update(manifestBytes)
        check(signature.verify(signatureBytes)) { "Runtime image manifest signature is invalid" }

        val manifest = JSONObject(manifestBytes.toString(Charsets.UTF_8))
        check(manifest.getString("schema") == "cyrene-runtime-image-v2") { "Unsupported runtime image schema" }
        val output = File(context.filesDir, "qemu-image/${manifest.getString("version")}").apply { mkdirs() }
        val kernel = extractVerified(output, manifest.getJSONObject("kernel"))
        val initramfs = extractVerified(output, manifest.getJSONObject("initramfs"))
        val rootfsTemplate = extractGzipVerified(output, manifest.getJSONObject("rootfs"))
        val hostResolver = extractVerified(output, manifest.getJSONObject("host_resolver"))
        val firmware = manifest.getJSONArray("firmware")
        for (index in 0 until firmware.length()) extractVerified(output, firmware.getJSONObject(index))
        // Limbo redirects QEMU's absolute host-file reads into this bundle.
        // Install the already signature- and digest-verified resolver input at
        // the exact path used by slirp; do not trust an arbitrary local file.
        val resolverTarget = File(output, "etc/resolv.conf")
        resolverTarget.parentFile?.mkdirs()
        if (!resolverTarget.isFile || !resolverTarget.readBytes().contentEquals(hostResolver.readBytes())) {
            hostResolver.copyTo(resolverTarget, overwrite = true)
        }
        return RuntimeImageBundle(
            directory = output,
            kernel = kernel,
            initramfs = initramfs,
            rootfsTemplate = rootfsTemplate,
            version = manifest.getString("version"),
            engine = manifest.getString("engine"),
            guestArch = manifest.getString("guest_arch"),
            desktopBackend = manifest.optBoolean("desktop_backend", false),
            memoryMiB = manifest.optInt("memory_mib", 256).also {
                require(it in 256..4096) { "Invalid guest memory budget" }
            },
            kernelAppend = manifest.optString("kernel_append", "console=ttyS0 rdinit=/init panic=-1 loglevel=4"),
        )
    }

    private fun extractVerified(output: File, entry: JSONObject): File {
        val name = entry.getString("file")
        require(name.matches(Regex("[A-Za-z0-9._-]+"))) { "Invalid runtime asset name" }
        val expected = entry.getString("sha256")
        val target = File(output, name)
        if (!target.isFile || sha256(target) != expected) {
            val temp = File(output, ".$name.tmp")
            context.assets.open("runtime/$name").use { input -> temp.outputStream().use(input::copyTo) }
            check(sha256(temp) == expected) { "Runtime asset digest mismatch: $name" }
            check(temp.renameTo(target) || run {
                temp.copyTo(target, overwrite = true)
                check(sha256(target) == expected) { "Runtime copied asset digest mismatch: $name" }
                temp.delete()
            }) {
                "Unable to install runtime asset: $name"
            }
        }
        // Either the existing file or the atomically renamed temporary file
        // has already been hashed. Avoid hashing multi-GB assets twice.
        return target
    }

    private fun extractGzipVerified(output: File, entry: JSONObject): File {
        val compressed = extractVerified(output, entry)
        val installedName = entry.getString("installed_file")
        require(installedName.matches(Regex("[A-Za-z0-9._-]+"))) { "Invalid runtime output name" }
        val expected = entry.getString("unpacked_sha256")
        val target = File(output, installedName)
        if (!target.isFile || sha256(target) != expected) {
            val temp = File(output, ".$installedName.tmp")
            GZIPInputStream(compressed.inputStream(), 64 * 1024).use { input ->
                SparseDisk.copy(input, temp)
            }
            check(sha256(temp) == expected) { "Runtime unpacked digest mismatch: $installedName" }
            check(temp.renameTo(target) || run {
                temp.inputStream().use { SparseDisk.copy(it, target) }
                check(sha256(target) == expected) { "Runtime copied disk digest mismatch" }
                temp.delete()
            }) {
                "Unable to install runtime output: $installedName"
            }
        }
        return target
    }

    private fun sha256(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { input ->
            val buffer = ByteArray(DEFAULT_BUFFER_SIZE)
            while (true) {
                val count = input.read(buffer)
                if (count < 0) break
                digest.update(buffer, 0, count)
            }
        }
        return digest.digest().joinToString("") { "%02x".format(it) }
    }
}
