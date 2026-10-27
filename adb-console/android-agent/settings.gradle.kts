pluginManagement {
    // 国内镜像只用于本地加速；GitHub Actions 等 CI 环境访问 maven.aliyun.com 会被拒绝，
    // 而镜像排在首位时解析失败会直接中断构建（Gradle 不会自动回退到下一个仓库），
    // 因此 CI 下（存在 CI 环境变量）跳过镜像，直接用官方源。
    val useMirror = System.getenv("CI") == null
    repositories {
        if (useMirror) {
            maven { url = uri("https://maven.aliyun.com/repository/google") }
            maven { url = uri("https://maven.aliyun.com/repository/central") }
            maven { url = uri("https://maven.aliyun.com/repository/gradle-plugin") }
        }
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}
dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    val useMirror = System.getenv("CI") == null
    repositories {
        if (useMirror) {
            maven { url = uri("https://maven.aliyun.com/repository/google") }
            maven { url = uri("https://maven.aliyun.com/repository/central") }
            maven { url = uri("https://maven.aliyun.com/repository/gradle-plugin") }
        }
        google()
        mavenCentral()
    }
}
rootProject.name = "rc-agent"
include(":app")
