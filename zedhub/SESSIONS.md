# SESSIONS.md — 会话模型与常见操作手册

本文档回答两个问题：**zedhub 的会话数据是怎么设计的**（理解了它，各类"会话不见了/
重复了/没标题"问题都能自己推断），以及**常见需求该怎么操作**。

---

## 1. 双锚点模型（一切问题的钥匙）

一个 agent 会话存在于两个地方，靠 `session_id` 关联：

```
┌─ Zed 索引（%LOCALAPPDATA%\Zed\db\0-stable\db.sqlite 的 sidebar_threads 表）
│    thread_id   ← 索引行主键（BLOB，可随意新建）
│    session_id  ← 外部锚点（指向数据本体，永不修改）
│    agent_id    ← claude-acp | codex-acp | antigravity-acp | opencode | pi-acp …
│    folder_paths← 挂载目录（\n 分隔多根；Zed 侧栏按当前工作区过滤显示）
│    title       ← Zed 建 thread 时自动生成（通常是首条 prompt 前缀）
│
└─ 各 agent 会话数据本体（zedhub 只读，不修改其格式）
     opencode     ~/.local/share/opencode/opencode.db（SQLite：session/message/part）
     claude-code  ~/.claude/projects/<目录slug>/<session_id>.jsonl
     codex        ~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<session_id>.jsonl
     antigravity  ~/.gemini/antigravity-acp/conversations/<session_id>.db
                  + 同名 .meta（JSON，含 cwd/mode）
```

由此推出几条不变式：

- **`session_id` 永远不能改**——它是数据文件名的锚点，改了 Zed 就找不到会话内容；
- **`thread_id` 可以随意新建**——"把会话弄到另一个目录"就是插一行新 thread
  （新 thread_id + 原 session_id + 新 folder_paths），不是修改 session_id；
- **同一 session_id 可以在多个目录各挂一行**——Zed 按当前工作区过滤显示，
  两个工作区各看到一份入口，互不干扰，指向同一个会话数据；
- **补登的行 title 写空会显示 "New Agent Thread"**——title 是 Zed 建 thread 时
  生成的，外部补登拿不到就只能回填（见 §3.4）。

### antigravity 的特殊性

对话本体 `conversations/<sid>.db` 里 `steps.step_payload` 是 **protobuf 二进制
（无公开 schema）**——zedhub 无法解析出标题和内容，只能整库字节搬运。因此：

- 目录锚点靠旁挂的 `<sid>.meta`（JSON 的 `cwd` 字段）；无 .meta 的会话无法定位；
- 补登/迁移时标题拿不到，靠 Zed 原索引行回填；原行也没有就只能是空。

---

## 2. 查重语义（为什么"已经 link 过"还会跳过）

`sessions link` 的跳过规则是 **`(agent_id, session_id, 目标目录)` 三元组查重**：

- 同会话挂到**新目录** → 允许新挂（跨目录挂载的实现基础）；
- 同会话挂到**已挂过的目录** → `already_linked` 跳过（幂等，重跑安全）。

同理 `archive import` 的文件写入是 **"已存在即跳过"**：把本机导出的归档再导回
本机，结果就是 `written=0 skipped=N`——这不是失败，是"确认存在"。真正的写入
只发生在缺数据的新机器上。

---

## 3. 常见需求速查

以下命令均在 `python_projects/zedhub` 下执行；**写操作（--apply）前必须完全
退出 Zed 与 opencode**（进程检查会拦截），且默认自动备份到
`%APPDATA%\language_projects\zedhub\backups\`。

### 3.1 终端直跑的 claude/codex 会话不在 Zed 侧栏

Zed 索引里没有它（Zed 外跑的会话不会登记）。补登：

```powershell
uv run zedhub sessions link <目录子串或完整路径> --source claude-code   # 或 codex / antigravity
# 确认 dry-run 计划后：
uv run zedhub sessions link <目录> --source claude-code --apply
```

### 3.2 把会话弄到另一个目录（保留原目录入口）

```powershell
# --exact：归一化后目录全等才命中，不带出子目录（推荐传完整路径）
uv run zedhub sessions link "D:\path\from" --target "D:\path\to" --exact
uv run zedhub sessions link "D:\path\from" --target "D:\path\to" --exact --apply
```

效果：`D:\path\to` 工作区出现新入口（新 thread_id、原 session_id），原目录入口
保留，两边打开的是同一个会话。

### 3.3 跨机器迁移

```powershell
# 源机导出（schema v2，整文件字节搬运，支持 --source claude-code/codex/antigravity）
uv run zedhub archive export <项目路径> -o 归档.db --source antigravity --exact
# 目标机检查 + 导入（默认 dry-run；--apply 才写；先完全退出 Zed 与 opencode）
uv run zedhub archive inspect 归档.db
uv run zedhub archive import 归档.db --target "D:\目标目录" --exact --apply
```

v2 归档按各源数据根（`~/.claude` / `~/.codex` / `~/.gemini`）整文件落盘，
`--target` 只决定新 Zed thread 挂到哪个目录。opencode 源走 v1 路径（表级迁移）。

### 3.4 修复补登后的空标题（"New Agent Thread"）

link 对空 title 会话会自动从 Zed 原索引行回填；**历史已写入的空标题行**不会被
再碰（幂等跳过），手动修（先退出 Zed；`WHERE` 里那半句 EXISTS 守卫必须保留，
否则子查询返回 NULL 会撞 `title NOT NULL` 约束）：

```python
# uv run python -c "<以下主体>"
import sqlite3, os
con = sqlite3.connect(os.path.expandvars(r"%LOCALAPPDATA%\Zed\db\0-stable\db.sqlite"))
con.execute("""
UPDATE sidebar_threads SET title = (
  SELECT s.title FROM sidebar_threads AS s
   WHERE s.session_id = sidebar_threads.session_id AND s.title IS NOT NULL AND s.title != ''
   LIMIT 1)
 WHERE (title IS NULL OR title = '')
   AND EXISTS (SELECT 1 FROM sidebar_threads AS s2
                WHERE s2.session_id = sidebar_threads.session_id
                  AND s2.title IS NOT NULL AND s2.title != '')
""")
con.commit(); con.close()
```

### 3.5 纠正挂错目录（把入口挪走，不是新增）

当前没有内置命令。手动 SQL（先退出 Zed，folder_paths 多根以 `\n` 分隔）：

```python
import sqlite3, os
con = sqlite3.connect(os.path.expandvars(r"%LOCALAPPDATA%\Zed\db\0-stable\db.sqlite"))
con.execute(
    "UPDATE sidebar_threads SET folder_paths = REPLACE(folder_paths, ?, ?),"
    " main_worktree_paths = REPLACE(main_worktree_paths, ?, ?)"
    " WHERE agent_id = ? AND folder_paths = ?",
    (r"D:\旧目录", r"D:\新目录", r"D:\旧目录", r"D:\新目录", "antigravity-acp", r"D:\旧目录"),
)
con.commit(); con.close()
```

### 3.6 查会话去哪了

```powershell
uv run zedhub threads list --search <关键词>          # Zed 索引侧
uv run zedhub search <关键词>                          # 跨源元数据检索
uv run zedhub sessions list --source <源> --project <目录>
```

---

## 4. 写库三铁律（本项目所有写操作的共同约束）

1. **进程检查**：写 Zed db 前完全退出 Zed 与 opencode（长连接 + 内存缓存，
   外部并发写有竞态）；一次性 SQL 修复同理；
2. **备份**：zedhub 的写命令自动备份；手动 SQL 前先复制 db 文件
   （`db.sqlite` 连同 `-wal`/`-shm`）到备份目录；
3. **dry-run 优先**：所有 zedhub 写命令默认 dry-run，确认计划再加 `--apply`；
   手写 SQL 先用 SELECT 验证 WHERE 范围，确认恰好命中目标行再改成 UPDATE。

> 踩坑记录：UPDATE 的 WHERE 范围必须与 SELECT 预览完全一致（漏掉 EXISTS 守卫
> 导致过 `NOT NULL constraint failed`）；`PRAGMA wal_checkpoint(TRUNCATE)` 需要
> 独占锁，不能放在 commit 之前（残留读句柄会卡 `database table is locked`），
> 一次性脚本先 `commit` 再 `PASSIVE` checkpoint 即可。

---

## 5. 相关文档

- 总体设计与命令全集：`README.md`
- 对接协议（daemon/HTTP/MCP/rpc）：父仓库
  `docs/projects/python_projects/zedhub/zedhub 对接协议.md`
- 需求/设计/任务：父仓库
  `docs/projects/python_projects/zedhub/specs/01-zed-session-hub/`
