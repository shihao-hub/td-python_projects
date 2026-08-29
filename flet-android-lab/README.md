# flet-android-lab

用纯 Python（Flet）+ VS Code 开发 Android 应用的实验项目，当前是一个**纪念日 App**：
记录一个日期，展示"已过多少天"或"倒计时还剩多少天"。

- 技术路线：**路线 A —— Python 原生方案（Flet）**，基于 Flutter 渲染引擎，声明式 UI，无需学习 Dart/JS。
- 依赖管理：uv（`.venv` + `uv.lock`），Python 3.12，零第三方依赖（Flet 之外全部标准库）。
- 当前 Flet 版本：0.86.x（入口 `ft.run(main)`，对话框走 `page.show_dialog / pop_dialog`）。

## 功能

- 添加 / 编辑 / 删除纪念日（名称 + 一次性日期）
- 自动计算并展示：未来 `还有 N 天`（含 `就是今天`）、过去 `已 N 天`
- 排序：倒计时临近的在前，已过的新→旧在后

## 快速开始

```bash
uv sync                    # 安装依赖到 .venv
uv run flet run main.py    # 桌面窗口调试，支持热重载（Ctrl+S 触发）
```

## 代码结构

| 文件 | 职责 |
|---|---|
| `main.py` | Flet UI：列表、新增/编辑对话框（名称 + 年/月/日下拉）、删除 |
| `core.py` | 纯函数：`days_until` 天数计算、`label` 文案、`sort_items` 排序（无 IO，边界已断言：跨年/当天/2月29） |
| `storage.py` | `sqlite3` 标准库 + 裸 SQL CRUD（参数化 `?` 占位，不用 ORM） |

日期选择采用年/月/日下拉：Flet 0.86 的 `DatePicker` 与表单 `AlertDialog` 的对话框叠加行为未验证，先取稳妥方案；保存时用 `date.fromisoformat` 校验非法日期（如 2 月 30 日）。

## 数据存储

- SQLite 库文件：桌面端优先放在系统应用数据目录（`page.storage_paths.get_application_support_directory()`），
  取不到时回退到项目 `data/anniversaries.db`（`data/` 已 gitignore）。
- 也可用环境变量 `ANNIVERSARY_DB` 指定库文件路径。

表结构（`storage.py` 自动建表）：

```sql
CREATE TABLE IF NOT EXISTS anniversaries (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    date       TEXT NOT NULL,      -- ISO YYYY-MM-DD，可直接字典序比较
    created_at TEXT NOT NULL
)
```

库文件损坏时自动把坏文件改名 `.corrupt-<时间戳>` 并重建空库。

## 真机调试与打包 APK

前置条件（本机当前未安装，需先补齐）：

1. **JDK 17+**：安装后配置 `JAVA_HOME`，并将 `%JAVA_HOME%\bin` 加入 `PATH`。
2. **Android SDK**：通过 Android Studio 或其 Command-line Tools 安装 SDK Platform + Build-tools，
   配置 `ANDROID_HOME` 环境变量。
3. 手机开启 **USB 调试** 并连接电脑，`adb devices` 能看到设备。

说明：`flet-cli`（0.86.5）已内置 Flutter 3.44.8 打包工具链（`flet --version` 可验证），
无需单独安装 Flutter SDK。SQLite 数据在手机上位于 App 沙箱目录。

打包与安装：

```bash
uv run flet build apk          # 产物在 build/app/outputs/flutter-apk/
adb install build/app/outputs/flutter-apk/app-release.apk
```
