plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.chaquo.python")
}

android {
    namespace = "com.novelgen.app"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.novelgen.app"
        minSdk = 26
        targetSdk = 35
        versionCode = 64
        versionName = "2.7.8"

        ndk {
            abiFilters += listOf("arm64-v8a")
        }
    }

    // 本地/CI release 签名：通过 Gradle 属性注入，避免把密码写入源码。
    signingConfigs {
        create("release") {
            val storeFilePath = providers.gradleProperty("RELEASE_STORE_FILE").orNull
            val storePasswordValue = providers.gradleProperty("RELEASE_STORE_PASSWORD").orNull
            val keyAliasValue = providers.gradleProperty("RELEASE_KEY_ALIAS").orNull
            val keyPasswordValue = providers.gradleProperty("RELEASE_KEY_PASSWORD").orNull
            if (!storeFilePath.isNullOrBlank() && !storePasswordValue.isNullOrBlank()
                && !keyAliasValue.isNullOrBlank() && !keyPasswordValue.isNullOrBlank()) {
                storeFile = file(storeFilePath)
                storePassword = storePasswordValue
                keyAlias = keyAliasValue
                keyPassword = keyPasswordValue
            }
        }
    }

    buildTypes {
        debug {
            // Internal debug APK: bypasses WeChat-login gating only. Provider API Key
            // remains runtime-configurable and is never embedded in the APK.
            versionNameSuffix = "-internal"
            buildConfigField("boolean", "INTERNAL_BUILD", "true")
        }
        release {
            isMinifyEnabled = true
            buildConfigField("boolean", "INTERNAL_BUILD", "false")
            val hasReleaseSigning = providers.gradleProperty("RELEASE_STORE_FILE").orNull?.isNotBlank() == true
                && providers.gradleProperty("RELEASE_STORE_PASSWORD").orNull?.isNotBlank() == true
                && providers.gradleProperty("RELEASE_KEY_ALIAS").orNull?.isNotBlank() == true
                && providers.gradleProperty("RELEASE_KEY_PASSWORD").orNull?.isNotBlank() == true
            if (hasReleaseSigning) signingConfig = signingConfigs.getByName("release")
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    buildFeatures {
        buildConfig = true
        viewBinding = true
    }

    // Compress native Python libraries so the internal APK stays within common
    // chat-file limits; Android extracts them at install time on minSdk 26+.
    packaging {
        jniLibs {
            useLegacyPackaging = true
        }
    }
}

chaquopy {
    defaultConfig {
        version = "3.13"
        pip {
            // v3.6.3: 禁用 pip cache 防 safe-delete 拦截
            // v2.5.67: 默认 PyPI 源国内不可达（pydantic==1.10.26 报 from versions: none）→ 切清华镜像
            options("--no-cache-dir", "--no-clean",
                    "--index-url", "https://pypi.tuna.tsinghua.edu.cn/simple")
            install("fastapi==0.125.0")
            install("uvicorn==0.52.1")
            install("openai==1.39.0")
            install("pydantic==1.10.26")
            install("httpx==0.27.2")
            install("edge-tts==6.1.12")
        }
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.webkit:webkit:1.12.1")
    implementation("com.google.android.material:material:1.12.0")
    implementation("androidx.swiperefreshlayout:swiperefreshlayout:1.1.0")
    implementation("androidx.constraintlayout:constraintlayout:2.2.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.9.0")
}
