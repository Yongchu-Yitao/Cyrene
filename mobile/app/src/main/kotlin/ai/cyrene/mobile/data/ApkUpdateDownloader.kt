package ai.cyrene.mobile.data

import android.content.Context
import android.os.Environment
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext
import java.io.File
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import kotlin.coroutines.coroutineContext

data class ApkDownloadProgress(
    val downloadedBytes: Long,
    val totalBytes: Long,
)

object ApkUpdateDownloader {
    suspend fun download(
        context: Context,
        release: GithubRelease,
        asset: GithubApkAsset,
        onProgress: (ApkDownloadProgress) -> Unit,
    ): File = withContext(Dispatchers.IO) {
        val downloadsRoot = context.getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS)
            ?: File(context.filesDir, "downloads")
        val updateDirectory = File(
            downloadsRoot,
            "updates",
        ).apply { mkdirs() }
        val safeName = asset.name
            .replace(Regex("""[^A-Za-z0-9._-]"""), "_")
            .takeIf { it.endsWith(".apk", ignoreCase = true) }
            ?: "cyrene-mobile-${release.version.replace(Regex("""[^A-Za-z0-9._-]"""), "_")}.apk"
        val destination = File(updateDirectory, safeName)
        val partial = File(updateDirectory, "$safeName.part")
        partial.delete()

        val connection = (URL(asset.url).openConnection() as HttpURLConnection).apply {
            requestMethod = "GET"
            instanceFollowRedirects = true
            connectTimeout = 15_000
            readTimeout = 30_000
            setRequestProperty("Accept", "application/vnd.android.package-archive")
            setRequestProperty("User-Agent", "Cyrene-Mobile/${release.version}")
        }
        try {
            val status = connection.responseCode
            if (status !in 200..299) throw IOException("Download returned HTTP $status")
            val total = connection.contentLengthLong.coerceAtLeast(0L)
            var downloaded = 0L
            var lastProgressAt = 0L
            connection.inputStream.buffered().use { input ->
                partial.outputStream().buffered().use { output ->
                    val buffer = ByteArray(DEFAULT_BUFFER_SIZE)
                    while (true) {
                        coroutineContext.ensureActive()
                        val count = input.read(buffer)
                        if (count < 0) break
                        output.write(buffer, 0, count)
                        downloaded += count
                        val now = System.currentTimeMillis()
                        if (now - lastProgressAt >= 100L || (total > 0L && downloaded >= total)) {
                            withContext(Dispatchers.Main.immediate) {
                                onProgress(ApkDownloadProgress(downloaded, total))
                            }
                            lastProgressAt = now
                        }
                    }
                }
            }
            if (downloaded == 0L) throw IOException("Downloaded APK is empty")
            destination.delete()
            if (!partial.renameTo(destination)) {
                throw IOException("Could not finalize downloaded APK")
            }
            destination
        } catch (error: Throwable) {
            partial.delete()
            throw error
        } finally {
            connection.disconnect()
        }
    }
}
