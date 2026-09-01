# flet-android-lab

用纯 Python（Flet）+ VS Code 开发 Android 应用的实验项目，当前是一个**「数日子」App**：
记录一个日期，展示"已过多少天"（正计时）或"倒计时还剩多少天"。

- 技术路线：**路线 A —— Python 原生方案（Flet）**，基于 Flutter 渲染引擎，声明式 UI，无需学习 Dart/JS。
- 依赖管理：uv（`.venv` + `uv.lock`），Python 3.12，零第三方依赖（Flet 之外全部标准库）。
- 当前 Flet 版本：0.86.x（入口 `ft.run(main)`，对话框走 `page.show_dialog / pop_dialog`）。

## 功能

- 添加 / 编辑 / 删除记录（名称 + 一次性日期）
- 自动计算并展示：未来 `还有 N 天`（含 `就是今天`）、过去 `已 N 天`
- 排序：倒计时临近的在前，已过的新→旧在后

## 如何启动

前置：安装 [uv](https://docs.astral.sh/uv/)（已装可跳过）：

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

启动（在 `flet-android-lab` 目录下执行）：

```bash
cd flet-android-lab
uv sync                    # 首次运行：安装依赖到 .venv
uv run flet run main.py    # 启动桌面窗口
```

- 弹出桌面窗口即启动成功。
- **热重载**：`flet run` 会监视 `main.py`，保存后窗口内自动重跑新代码，无需重启命令（界面状态会重置）。
- **不要热重载**：当普通 Python 程序直接跑——`uv run python main.py`（无监视器，行为完全一样）。
- 数据库文件默认在系统应用数据目录；想换位置可设环境变量 `DAY_ENTRIES_DB=<路径>` 后再启动。

## 代码结构

| 文件 | 职责 |
|---|---|
| `main.py` | Flet UI：列表、新增/编辑对话框（名称 + 年/月/日下拉）、删除 |
| `core.py` | 纯函数：`days_until` 天数计算、`label` 文案、`sort_items` 排序（无 IO，边界已断言：跨年/当天/2月29） |
| `storage.py` | `sqlite3` 标准库 + 裸 SQL CRUD（参数化 `?` 占位，不用 ORM） |
| `log.py` | structlog 配置：默认 Console 彩色格式（非 JSON） |

日期选择采用年/月/日下拉：Flet 0.86 的 `DatePicker` 与表单 `AlertDialog` 的对话框叠加行为未验证，先取稳妥方案；保存时用 `date.fromisoformat` 校验非法日期（如 2 月 30 日）。

## 数据存储

- SQLite 库文件：桌面端优先放在系统应用数据目录（`page.storage_paths.get_application_support_directory()`），
  取不到时回退到项目 `data/day_entries.db`（`data/` 已 gitignore）。
- 也可用环境变量 `DAY_ENTRIES_DB` 指定库文件路径。

表结构（`storage.py` 自动建表）：

```sql
CREATE TABLE IF NOT EXISTS day_entries (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    date       TEXT NOT NULL,      -- ISO YYYY-MM-DD，可直接字典序比较
    created_at TEXT NOT NULL
)
```

库文件损坏时自动把坏文件改名 `.corrupt-<时间戳>` 并重建空库。

## 日志

structlog（与 `demo-api` 同栈），**默认 Console 彩色格式，不输出 JSON**；日志打到 stdout，
覆盖：启动与库文件路径、增/改/删、坏库自愈（error 级）。

```powershell
$env:LOG_JSON = "1"     # 需要机器可读日志时再开 JSON
$env:LOG_LEVEL = "DEBUG"
```

## 本机调试 vs 真机

- **本机（桌面窗口）调试：零额外安装**，按上文"如何启动"即可，覆盖日常开发的绝大部分场景
  （UI 逻辑、增删改查、SQLite 读写全部可验证）。
- **真机 / 打包 APK：仅在想把 App 装进手机日常使用时才需要**，用于验证桌面覆盖不了的部分：
  小屏与触摸适配、虚拟键盘遮挡、App 沙箱存储、生命周期（切后台）、最终安装包本身。

### 打包 APK 的前置条件（本机当前未装，用到再装）

1. **JDK 17+**：安装后配置 `JAVA_HOME`，并将 `%JAVA_HOME%\bin` 加入 `PATH`。
2. **Android SDK**：通过 Android Studio 或其 Command-line Tools 安装 SDK Platform + Build-tools，
   配置 `ANDROID_HOME` 环境变量。
3. 手机开启 **USB 调试** 并连接电脑，`adb devices` 能看到设备（真机调试 / `adb install` 用）。

说明：`flet-cli`（0.86.5）已内置 Flutter 3.44.8 打包工具链（`flet --version` 可验证），
无需单独安装 Flutter SDK。SQLite 数据在手机上位于 App 沙箱目录。

打包与安装：

```bash
uv run flet build apk          # 产物在 build/app/outputs/flutter-apk/
adb install build/app/outputs/flutter-apk/app-release.apk
```
