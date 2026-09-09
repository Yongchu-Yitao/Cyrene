plugins {
    alias(libs.plugins.android.library)
    alias(libs.plugins.kotlin.android)
}

android {
    namespace = "ai.cyrene.mobile.runtime"
    compileSdk = 35
    defaultConfig {
        minSdk = 28
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions.jvmTarget = "17"
    buildFeatures.buildConfig = true
    // The disk is already gzip-compressed; recompressing it exhausts the
    // Android asset worker heap without materially reducing APK size.
    androidResources.noCompress += "gzip"
    sourceSets.getByName("debug").assets.srcDir(rootProject.file("runtime-image/desktop/probes"))
    // The launcher requires the desktop image; never silently package Alpine.
    val desktopAssets = providers.gradleProperty("cyreneDesktopAssets").orNull
        ?.let { rootProject.file(it) } ?: rootProject.file("build/unified-assets")
    sourceSets.getByName("main").assets.setSrcDirs(listOf(desktopAssets))
    tasks.named("preBuild").configure {
        doFirst {
            require(desktopAssets.resolve("runtime/manifest.sig").isFile) {
                "Build signed desktop assets in build/unified-assets or set -PcyreneDesktopAssets"
            }
            val manifest = desktopAssets.resolve("runtime/manifest.json").readText()
            require(Regex("\"desktop_backend\"\\s*:\\s*true").containsMatchIn(manifest)) {
                "The single APK requires a desktop backend image"
            }
        }
    }

}

dependencies {
    implementation(project(":runtime-protocol"))
    testImplementation(libs.junit)
}
