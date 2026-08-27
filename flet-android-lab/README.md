# flet-android-lab

用纯 Python（Flet）+ VS Code 开发 Android 应用的实验项目。

- 技术路线：**路线 A —— Python 原生方案（Flet）**，基于 Flutter 渲染引擎，声明式 UI，无需学习 Dart/JS。
- 依赖管理：uv（`.venv` + `uv.lock`），Python 3.12。
- 当前 Flet 版本：0.86.x（入口为 `ft.run(main)`）。

## 快速开始

```bash
uv sync            # 安装依赖到 .venv
uv run flet run main.py   # 桌面窗口调试，支持热重载（Ctrl+S 触发）
```

修改 `main.py` 保存即可热重载，无需启动模拟器。

## 目录结构

```
flet-android-lab/
├── main.py            # 入门示例：计数器
├── pyproject.toml     # 项目与依赖（uv）
└── .python-version    # 3.12
```

## 真机调试与打包 APK

前置条件（本机当前未安装，需先补齐）：

1. **JDK 17+**：安装后配置 `JAVA_HOME`，并将 `%JAVA_HOME%\bin` 加入 `PATH`。
2. **Android SDK**：通过 Android Studio 或其 Command-line Tools 安装 SDK Platform + Build-tools，
   配置 `ANDROID_HOME` 环境变量。
3. 手机开启 **USB 调试** 并连接电脑，`adb devices` 能看到设备。

说明：`flet-cli`（0.86.5）已内置 Flutter 3.44.8 打包工具链（`flet --version` 可验证），
无需单独安装 Flutter SDK。

打包与安装：

```bash
uv run flet build apk          # 产物在 build/app/outputs/flutter-apk/
adb install build/app/outputs/flutter-apk/app-release.apk
```

## 与 Python 后端协同

Flet 前端可直接用标准库或 `httpx` 调用 RESTful 接口（如本仓库 `demo-api` 提供的服务）。
