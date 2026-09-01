# PyStand 踩坑实验记录（skill 化素材）

> 2026-08-24 实测。环境：Windows 11 x64，无系统级 Python，无 VS/MinGW。
> 可用工具：Git Bash（curl）、`/c/Windows/System32/tar.exe`（bsdtar，可解 zip）、uv（managed 完整 Python 3.12）、PowerShell。
> 结论：**全流程打通**，GUI/CLI/中文路径/图标/改名/tkinter/第三方依赖全部实测通过，产物在 `dist/DemoApp/`（~51MB）。

## 一、最终产物结构（实测可运行）

```
dist/DemoApp/
├── DemoApp.exe            # PyStand-x64-GUI.exe 改名 + rcedit 换图标
├── _pystand_static.int    # 启动脚本（exe 改名后必须叫这个名，且优先级最高）
├── PyStand-CLI.exe        # CLI 变体（调试用），同目录配套 PyStand-CLI.int
├── PyStand-CLI.int
├── app/
│   ├── main.py            # 用户源码（tkinter + requests demo）
│   └── requirements.txt
├── runtime/               # python-3.12.5-embed-amd64.zip 解压物
│   ├── python.exe / pythonw.exe / python3.dll / python312.dll / python312.zip ...
│   ├── python312._pth     # ★ 已改：加 ../site-packages + 取消 import site 注释
│   ├── _tkinter.pyd       # ★ 从完整版 Python 3.12 复制
│   ├── tcl86t.dll / tk86t.dll
│   ├── tcl/{tcl8, tcl8.6, tk8.6}   # ★ tcl/tk 数据目录
│   └── Lib/site-packages/ # get-pip 装的 pip 本体（发布可删，见瘦身）
└── site-packages/         # ★ 用户依赖（pip --target 装到这里）+ tkinter 纯 py 包
    ├── tkinter/           # ★ 从完整版 Lib/tkinter 复制
    ├── requests/ ...
    └── bin/               # （可删）pip --target 生成的 console scripts
```

## 二、可复刻流程（skill 的骨架）

```bash
# 0. 常量
PY_VER=3.12.5
APP=DemoApp
EMBED_URL="https://www.python.org/ftp/python/${PY_VER}/python-${PY_VER}-embed-amd64.zip"
PYSTAND_URL="https://github.com/skywind3000/PyStand/releases/download/1.1.5/PyStand-v1.1.5-exe.zip"   # tag 无 v 前缀！
RCEDIT_URL="https://github.com/electron/rcedit/releases/download/v2.0.0/rcedit-x64.exe"               # 最新就是 v2.0.0

# 1. 下载（curl -L --retry 3）
# 2. 解压：embed zip → $APP/runtime/；PyStand zip 取 PyStand-x64-GUI/PyStand.exe
#    ★ Git Bash 的 tar 是 GNU tar 不认 zip，必须用 /c/Windows/System32/tar.exe（bsdtar）
# 3. pip 引导：
cd runtime
curl -sLO https://bootstrap.pypa.io/get-pip.py
./python.exe get-pip.py                       # pip 装进 runtime/Lib/site-packages
# 4. ★ 改 python312._pth（文件名含版本号，需探测）：
#    追加一行 ../site-packages，并把 #import site 取消注释
# 5. 装依赖到 HOME 的 site-packages（PyStand 只认 HOME 下的目录）：
./python.exe -m pip install -r ../app/requirements.txt --target ../site-packages
#    ★ --target 会生成 bin/ 目录（console scripts），发布时删掉
# 6. tkinter（可选，embed 包没有且 pip 装不了）：从同版本完整安装复制 4 件套
#    Lib/tkinter → ../site-packages/tkinter
#    DLLs/{_tkinter.pyd,tcl86t.dll,tk86t.dll} → ./
#    tcl/{tcl8,tcl8.6,tk8.6} → ./tcl/
#    （本实验源：C:\Users\shawn.zhang\AppData\Roaming\uv\python\cpython-3.12-windows-x86_64-none）
# 7. 写 _pystand_static.int（exe 尚叫 PyStand.exe 时先写 PyStand.int）
# 8. 图标：rcedit-x64.exe PyStand.exe --set-icon app.ico
# 9. 改名 mv PyStand.exe DemoApp.exe；启动脚本改名/复制为 _pystand_static.int
# 10. 验证（见第四节）
```

## 三、坑清单（skill 必须处理的点，按杀伤力排序）

### 坑 1：`_pystand_static.int` 优先级最高（本次最大"灵异"）
`DetectScript()` 的查找顺序（源码实证）：**`<exe去扩展名>.int` 检查之前，先查 `_pystand_static.int`，存在就用它**；同名 `.int/.py/.pyw` 都没有才弹错误框。
→ 后果：目录里只要有 `_pystand_static.int`，**所有** PyStand exe（包括 `PyStand-CLI.exe`）都执行它。实验中 CLI 版"神秘挂起"1 小时，真相 = 它在跑 GUI demo 的 mainloop（窗口显示、stdout 不写、debug 日志不出现）。产物本身没坏。
→ **skill 规则：发布目录只保留一个启动脚本**；调试 CLI 版时必须临时挪走 `_pystand_static.int`。

### 坑 2：`._pth` 隔离模式 vs PyStand 的路径注入
embed 包默认 `python312._pth` = 完全隔离（sys.path 固定为 `python312.zip` + `.`）。PyStand 启动用 `-I -s -S` + 注入脚本 `site.addsitedir(HOME 下 . / lib / site-packages / runtime)`。
→ **pip 直接装会进 `runtime\Lib\site-packages`，PyStand 看不见**。解法：`pip install --target ..\site-packages`，并编辑 `._pth` 加 `../site-packages`（+ 取消 `import site` 注释），后者让 `runtime\python.exe` 也能直接 import 用户依赖，方便调试。
→ `._pth` 文件名带版本（`python312._pth`），skill 要按实际版本探测。

### 坑 3：GUI 版异常 = 隐藏 MessageBox
GUI 变体无控制台：stdout/stderr 重定向 devnull；**`.int` 抛异常时弹 MessageBox**——在无头/自动化环境表现为"进程挂着不死"。
→ skill 的启动脚本模板应自带 `try/except` 写日志文件；自动化验证时警惕"进程活着≠工作正常"，用窗口标题（`Get-Process | Select MainWindowTitle`）或日志文件判定。

### 坑 4：CLI 变体需要真控制台环境
CLI 变体在无控制台的后台/服务环境（本次：命令执行器的后台通道）可能挂起。有真终端时一切正常（多次实测秒退）。
→ **交付默认用 GUI 变体**；CLI 变体仅用于开发调试。

### 坑 5：tkinter 四件套
embed 包从不带 tkinter（cpython#99566），pip 无解。必须从**同版本、同位数完整安装**复制：
`Lib/tkinter`（纯 py）、`DLLs/_tkinter.pyd`、`DLLs/tcl86t.dll + tk86t.dll`、`tcl/` 数据目录。
→ 3.12.x 内 ABI 兼容：uv 的 cpython-3.12（3.12.11 构建）配 3.12.5 embed 实测 OK。
→ `TCL_LIBRARY/TK_LIBRARY` 可不设：tcl 自动发现了 `runtime/tcl/tcl8.6`（`info library` 实证），但启动脚本里 setdefault 更稳。
→ 复制源优先级：本机同版本安装 > uv managed > 下载同版本安装器静默装临时目录。

### 坑 6：下载细节
- PyStand release **tag 无 `v` 前缀**（`1.1.5` 不是 `v1.1.5`），错 URL 返回 9 字节 "Not Found"
- rcedit 最新是 **v2.0.0**（v2.1.0 不存在）
- GitHub API（`/releases/latest`）可拿准确 `browser_download_url`

### 坑 7：Git Bash 工具链
- `tar` = GNU tar，**不认 zip** → 用 `/c/Windows/System32/tar.exe`（bsdtar）
- **中文路径 mojibake**：bash（UTF-8）→ Windows API（GBK）转码错乱，曾导致 `rm -rf` 报 Device or resource busy 且显示乱码名；**PowerShell `Remove-Item` 可正常删**
- 后台任务里 `cd X && ./exe &` 的 `&` 作用域坑：`&` 把整个 `cd && exe` 链划为后台 job，后续命令还在旧 cwd

### 坑 8：PyStand 版本无关性 & 边界
- exe 只加载 `runtime/python3.dll`（稳定 ABI 转发库）→ **换 Python 版本 = 换 runtime 目录**，exe 永不重编译
- `Py_Main()` 3.13 起 deprecated、**3.15 移除** → 官方预编译 exe 预计支持到 3.14；3.12 稳妥
- python.org 3.5+ 每个 Windows 版本都有 embed 包（3.12.5 实测 10.5MB），不存在"没有嵌入式包"的版本
- Win7 用户需 Python 3.8（官方也提供 py38 运行时包）

### 坑 9：杀软误报
rcedit 改图标 = 改 exe 字节，无签名小众 exe 误报风险升高（作者 README 也提醒）。skill 应在产出说明里提示用户。

### 坑 10：杂项
- `sys.argv[0]` 是 `.int` 文件路径，用户参数从 `argv[1:]` 开始（实测）
- `sys.executable` = PyStand exe 自身；`sys.PYSTAND_HOME/SCRIPT/SEP` 为注入属性
- pip `--target` 会生成 `bin/`（Unix 习惯），发布可删
- 发布瘦身（可选）：删 `runtime/Lib/site-packages`（pip 本体）、`runtime/Scripts`、`site-packages/bin`、`*.cat`、`python.exe`（只留 pythonw？不行——runtime\python.exe 是调试依赖，保留更稳）

## 四、验证矩阵（实测数据）

| 验证项 | 方法 | 结果 |
|---|---|---|
| 最小闭环（GUI） | `DemoApp.exe` 进程 + `MainWindowTitle="PyStand Demo"` | ✅ |
| 嵌入式解释器 | 窗口显示 `sys.version`=3.12.5、`sys.executable`=DemoApp.exe | ✅ |
| 第三方依赖 | requests 2.34.2 及 5 个传递依赖 import OK | ✅ |
| tkinter | TkVersion 8.6、`Tk()` init、tcl library 定位到 runtime\tcl\tcl8.6 | ✅ |
| 图标 | rcedit `--set-icon` 无报错，exe 增 ~5KB | ✅ |
| 改名机制 | DemoApp.exe + `_pystand_static.int` 正常启动 | ✅ |
| CLI 变体 | 前台管道：stdout、exit=0、参数转发（`argv[1:]`）| ✅ |
| 中文路径 | GUI 窗口标题 + CLI 全断言通过（路径含"中文应用"）| ✅ |
| standalone | 本机无系统级 Python，全程只用 runtime\python.exe | ✅ 天然验证 |

## 五、skill 化蓝图（下一步）

**输入**：用户项目目录（入口 py + requirements.txt）、可选（ico、Python 版本、GUI/CLI、32/64 位、是否 tkinter）
**输出**：`dist/<AppName>/`，结构如第一节
**步骤**：按第二节固化，参数化 `PY_VER`、`AppName`、变体选择
**内置知识**：第三节全部坑 + 下载 URL 模板 + tkinter 复制源探测顺序
**验证环节**：GUI 用 MainWindowTitle / 日志文件断言；CLI 用前台管道跑 `.int` 冒烟脚本
**发布建议**：自动瘦身 + 杀软误报提示 + 附 PyStand-CLI.exe 调试变体（可选）

## 六、遗留

- dist 下曾出现两个删不掉的中文目录（Git Bash 编码锁死），PowerShell 已清理；skill 操作中文路径统一走 PowerShell
- 未测：PyQt 场景（README 提及中文路径下 Qt 插件目录需手动指定，issue #6）；杀软实测；Win7/32 位
- PyStand.int（原名机制）已随实验清理，目录现只有 `_pystand_static.int` + CLI 调试对
