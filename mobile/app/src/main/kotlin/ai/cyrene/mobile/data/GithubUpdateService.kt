package ai.cyrene.mobile.data

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL

data class GithubRelease(
    val version: String,
    val releaseUrl: String,
    val mainApk: GithubApkAsset?,
    val runtimeApk: GithubApkAsset?,
    val notes: String,
    val publishedAt: String,
) {
    val apkUrl: String? get() = mainApk?.url
    val apkName: String? get() = mainApk?.name
}

data class GithubApkAsset(
    val name: String,
    val url: String,
)

sealed interface UpdateCheckResult {
    data class UpdateAvailable(val release: GithubRelease) : UpdateCheckResult
    data class UpToDate(val latestVersion: String) : UpdateCheckResult
    data object NoReleases : UpdateCheckResult
}

object GithubUpdateService {
    private const val LATEST_RELEASE_URL =
        "https://api.github.com/repos/Yongchu-Yitao/Cyrene-mobile/releases/latest"

    suspend fun check(currentVersion: String): UpdateCheckResult = withContext(Dispatchers.IO) {
        val connection = (URL(LATEST_RELEASE_URL).openConnection() as HttpURLConnection).apply {
            requestMethod = "GET"
            connectTimeout = 10_000
            readTimeout = 10_000
            setRequestProperty("Accept", "application/vnd.github+json")
            setRequestProperty("X-GitHub-Api-Version", "2022-11-28")
            setRequestProperty("User-Agent", "Cyrene-Mobile/$currentVersion")
        }

        try {
            when (val status = connection.responseCode) {
                HttpURLConnection.HTTP_OK -> {
                    val release = parseRelease(connection.inputStream.bufferedReader().use { it.readText() })
                    if (isNewerVersion(release.version, currentVersion)) {
                        UpdateCheckResult.UpdateAvailable(release)
                    } else {
                        UpdateCheckResult.UpToDate(release.version)
                    }
                }
                HttpURLConnection.HTTP_NOT_FOUND -> UpdateCheckResult.NoReleases
                else -> throw IOException("GitHub returned HTTP $status")
            }
        } finally {
            connection.disconnect()
        }
    }

    internal fun parseRelease(json: String): GithubRelease {
        val root = JSONObject(json)
        val version = root.getString("tag_name")
        val releaseUrl = root.getString("html_url")
        val assets = root.optJSONArray("assets")
        val apkAssets = if (assets != null) {
            val apkAssets = (0 until assets.length())
                .mapNotNull(assets::optJSONObject)
                .filter { it.optString("name").endsWith(".apk", ignoreCase = true) }
            apkAssets.mapNotNull { asset ->
                val name = asset.optString("name").takeIf(String::isNotBlank)
                val url = asset.optString("browser_download_url").takeIf(String::isNotBlank)
                if (name != null && url != null) GithubApkAsset(name, url) else null
            }
        } else {
            emptyList()
        }
        val runtimeApk = apkAssets.firstOrNull {
            it.name.contains("runtime", ignoreCase = true)
        }
        val mainApk = apkAssets.firstOrNull {
            it.name.contains("cyrene-mobile", ignoreCase = true) &&
                !it.name.contains("runtime", ignoreCase = true)
        } ?: apkAssets.firstOrNull {
            !it.name.contains("runtime", ignoreCase = true)
        }
        return GithubRelease(
            version = version,
            releaseUrl = releaseUrl,
            mainApk = mainApk,
            runtimeApk = runtimeApk,
            notes = root.optString("body"),
            publishedAt = root.optString("published_at"),
        )
    }

    internal fun isNewerVersion(candidate: String, current: String): Boolean {
        val candidateVersion = ParsedVersion.from(candidate)
        val currentVersion = ParsedVersion.from(current)
        return candidateVersion != null &&
            currentVersion != null &&
            candidateVersion > currentVersion
    }

    private data class ParsedVersion(
        val numbers: List<Int>,
        val preRelease: List<String>,
    ) : Comparable<ParsedVersion> {
        override fun compareTo(other: ParsedVersion): Int {
            val componentCount = maxOf(numbers.size, other.numbers.size)
            repeat(componentCount) { index ->
                val comparison = (numbers.getOrNull(index) ?: 0)
                    .compareTo(other.numbers.getOrNull(index) ?: 0)
                if (comparison != 0) return comparison
            }

            if (preRelease.isEmpty() && other.preRelease.isNotEmpty()) return 1
            if (preRelease.isNotEmpty() && other.preRelease.isEmpty()) return -1
            repeat(maxOf(preRelease.size, other.preRelease.size)) { index ->
                val left = preRelease.getOrNull(index) ?: return -1
                val right = other.preRelease.getOrNull(index) ?: return 1
                val leftNumber = left.toIntOrNull()
                val rightNumber = right.toIntOrNull()
                val comparison = when {
                    leftNumber != null && rightNumber != null -> leftNumber.compareTo(rightNumber)
                    leftNumber != null -> -1
                    rightNumber != null -> 1
                    else -> left.compareTo(right, ignoreCase = true)
                }
                if (comparison != 0) return comparison
            }
            return 0
        }

        companion object {
            private val versionPattern =
                Regex("""(?:^|[^0-9])(\d+(?:\.\d+)*)(?:-([0-9A-Za-z.-]+))?""")

            fun from(raw: String): ParsedVersion? {
                val match = versionPattern.find(raw.substringBefore('+')) ?: return null
                val numbers = match.groupValues[1].split('.').map { it.toIntOrNull() ?: return null }
                val preRelease = match.groupValues[2]
                    .takeIf(String::isNotBlank)
                    ?.split('.')
                    .orEmpty()
                return ParsedVersion(numbers, preRelease)
            }
        }
    }
}
