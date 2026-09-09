plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
}

// Keep the user-visible version in sync with the shared backend.
val projectMetadata = providers.fileContents(
    rootProject.layout.projectDirectory.file("../pyproject.toml"),
).asText.get()
val projectSection = Regex("(?ms)^\\[project]\\s*\\n(.*?)(?=^\\[|\\z)")
    .find(projectMetadata)?.groupValues?.get(1)
    ?: throw GradleException("Missing [project] section in pyproject.toml")
val cyreneVersion = Regex("(?m)^version\\s*=\\s*\"([^\"]+)\"\\s*(?:#.*)?$")
    .find(projectSection)?.groupValues?.get(1)
    ?: throw GradleException("Missing literal project.version in pyproject.toml")

android {
    namespace = "ai.cyrene.mobile"
    compileSdk = 35

    defaultConfig {
        applicationId = "ai.cyrene.mobile"
        minSdk = 28
        targetSdk = 35
        versionCode = CyreneVersion.androidCode(cyreneVersion)
        versionName = cyreneVersion
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        vectorDrawables.useSupportLibrary = true
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions.jvmTarget = "17"
    androidResources.noCompress += "gzip"
    packaging.jniLibs.useLegacyPackaging = true
    packaging.resources.excludes += setOf(
        "META-INF/versions/9/OSGI-INF/MANIFEST.MF",
        "META-INF/DEPENDENCIES",
        "META-INF/LICENSE",
        "META-INF/LICENSE.txt",
        "META-INF/NOTICE",
        "META-INF/NOTICE.txt",
    )
}

dependencies {
    implementation(project(":runtime-protocol"))
    implementation(project(":runtime-app"))
    implementation(libs.activity.ktx)
    implementation(libs.lifecycle.viewmodel.ktx)
    implementation(libs.coroutines.android)
    implementation(libs.androidx.core.ktx)
    testImplementation(libs.junit)
    testImplementation(libs.json)
}
