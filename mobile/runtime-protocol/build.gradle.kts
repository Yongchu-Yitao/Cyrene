plugins {
    alias(libs.plugins.android.library)
    alias(libs.plugins.kotlin.android)
}

android {
    namespace = "ai.cyrene.mobile.runtime.protocol"
    compileSdk = 35
    defaultConfig { minSdk = 28 }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions.jvmTarget = "17"
    buildFeatures.aidl = true
}
