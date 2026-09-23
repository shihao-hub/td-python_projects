# zed-pi-stats

Zed 编辑器中 `pi-acp` 智能体（Agent）会话 Token 消耗、交互轮次与预估费用的轻量统计工具。

---

## ✨ 核心特性

- **三位一体契约对齐**：
  - **人读终端视图**：自适应弹性列排版（`expand=True`），垂直对齐，防文字截断。
  - **机器结构化 JSON**：`--json` 输出与查询参数（`--by-model` / `--by-project` / `--by-session`）精准对齐的精简 JSON。
  - **动态契约反射 Schema**：`--schema` 零 I/O 毫秒级输出当前视图专有的 JSON Schema，方便上游下游（如 Agent / CI 脚本）进行确定性校验与代码生成。
- **混合模型自动折叠与分解**：同一个会话中如果切过模型，按模型分别统计，并汇总全会话。
- **Zed 本地数据库安全读取**：自动快照读取 SQLite 的 WAL / SHM，防锁并发。
- **纯标准库零额外重依赖**：除 Rich 终端渲染库外无任何第三方依赖。

---

## 🚀 常用命令

### 1. 终端交互（人类可读）

```powershell
# 1. 默认完整看板 (总览 + 最近10条会话 + 模型汇总 + 工作区汇总)
zed-pi-stats

# 2. 仅看模型消耗汇总
zed-pi-stats --by-model

# 3. 仅看工程工作区汇总
zed-pi-stats --by-project

# 4. 仅看最近会话列表 (支持 -n 指定条数)
zed-pi-stats --by-session -n 20

# 5. 展开全部列 (宽终端) + 显示未缩写的原始大整数
zed-pi-stats --wide --raw
```

### 2. 机器自动化（JSON + Schema 对齐）

```powershell
# 获取全量报告数据
zed-pi-stats --json

# 仅获取模型消耗数据
zed-pi-stats --by-model --json

# 仅获取工作区工程数据
zed-pi-stats --by-project --json

# 仅获取最近 5 条会话数据
zed-pi-stats --by-session -n 5 --json
```

### 3. 契约自描述（零 I/O 极速反射）

```powershell
# 获取全量报告 Schema
zed-pi-stats --schema

# 获取模型视图专属 Schema
zed-pi-stats --by-model --schema

# 获取工作区视图专属 Schema
zed-pi-stats --by-project --schema

# 获取会话列表专属 Schema
zed-pi-stats --by-session --schema
```

---

## 📦 安装与运行

```powershell
# 在本项目目录下直接运行
uv run zed-pi-stats

# 或者全局安装到 uv 工具箱
uv tool install .
```
