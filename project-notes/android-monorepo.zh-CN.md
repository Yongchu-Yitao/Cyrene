# Android 统一仓库与自动构建

## 开发入口

Android 源码已从 `Yongchu-Yitao/Cyrene-mobile` 导入 `Cyrene/mobile/`。
导入使用未 squash 的 Git subtree 合并，保留原仓库全部祖先提交；目录是普通文件，
不是 submodule，克隆 Cyrene 不需要额外初始化子仓库。今后的修改统一提交到 Cyrene。
旧仓库保留历史和迁移提示，不再双向同步，也没有重写或删除历史。

```text
Cyrene/
  src/cyrene/                    共享 Python 后端
    workbench/webui/             共享 Workbench
  mobile/
    app/                        Android 主 App
    runtime-app/                私有 QEMU 服务与运行时资源
    runtime-protocol/           Binder 协议
    runtime-image/desktop/      ARM64 桌面镜像构建器
    ci/                        构建输入校验
  .github/workflows/android.yml
```

`mobile/runtime-image/desktop/build.py` 默认从自身路径定位 Cyrene 仓库根目录。
构建会复制同一 checkout 的 `src/cyrene/` 和必要项目文件；不会复制 `mobile/`、
开发者数据库、工作目录或本地环境。仍可用 `--source` 显式指定另一个 checkout。

## 自动更新范围

`main` 上的 `mobile/`、`src/cyrene/`、Python 依赖声明/锁文件、默认人格、LICENSE
或 Android workflow 更新时，会触发 **Android single APK**；相关 PR 同样触发，
也支持 Actions 页面手动运行。仅修改文档不会重建大镜像。

1. ARM64 Linux runner 构建当前提交的 Workbench，执行移动手势测试和镜像打包边界测试。
2. 从当前共享后端生成新的 Debian ARM64 镜像，并用本次运行的临时 RSA 密钥签名。
3. x64 Linux runner 验证签名和所有输入摘要，运行选定 Android 单元测试并构建单 APK。
4. 校验 APK 签名，上传 APK、SHA-256、运行时 manifest 和准确的 Git 提交号。

从 Actions 对应成功运行的 `Cyrene-Android-debug-<commit>` artifact 下载。
APK 保留 7 天，中间运行时资源保留 3 天。失败运行不会标记为成功交付。
临时私钥在任务结束时删除，不上传 artifact，不需要仓库签名 secrets。

这些是实验 Debug 包；CI 的 Android debug 签名不是稳定的发行签名，不能保证跨运行覆盖安装。
正式分发需要另行配置持久发行签名、版本号递增和发布策略。
Android 显示版本 `versionName` 已直接读取根目录 `pyproject.toml` 的 `[project].version`；
Android 安装更新序号 `versionCode` 由统一版本自动派生，不单独维护。
显示版本读取失败或版本格式超出编码范围会中止构建，不会回退到旧移动端版本。
规则与边界见[Android 外壳收尾记录](android-shell-cleanup.zh-CN.md)。
自动构建不等于自动更新已安装的手机 App，也没有添加应用内更新器。

## 本地构建

需要 Docker Buildx/Linux 引擎、Python 3.12+、Node 22、JDK 17、Android SDK 35 和 OpenSSL。
完整镜像构建占用大量临时空间，CI 在开始时要求至少 20 GiB 可用空间；这只是最低检查，
依赖增长仍可能需要更多。GitHub runner 的磁盘是有限的，失败时应查看磁盘与依赖下载日志。
参见 [GitHub runner 规格](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)。

从 Cyrene 根目录执行：

```sh
npm ci --prefix src/cyrene/workbench/webui
npm run build --prefix src/cyrene/workbench/webui
# 使用仓库外的专用开发密钥；不要提交私钥。
python3 mobile/runtime-image/desktop/build.py \
  --arch arm64 --output mobile/build/unified-assets \
  --signing-key /absolute/path/to/development-key.pem
cd mobile
python3 ci/verify-runtime.py build/unified-assets
./gradlew :app:assembleDebug -Dorg.gradle.jvmargs=-Xmx6g --max-workers=2 --no-daemon
```

镜像输出目录必须不存在。已有干净签名资源可通过
`-PcyreneDesktopAssets=/absolute/path/to/assets` 复用；资源必须与要验证的源码版本匹配。
APK 位于 `mobile/app/build/outputs/apk/debug/app-debug.apk`。

## 验证边界

这次迁移不改变 App 的应用 ID、数据目录或运行时架构。原有磁盘迁移、QEMU SIGILL、
真机性能和系统桥接限制仍然存在，详见 [Android 移植总结](android-port-summary.zh-CN.md)。
打包和单元测试通过不等同于真机功能验收。CI 从干净源码完整重建镜像，不使用开发手机的数据盘。
