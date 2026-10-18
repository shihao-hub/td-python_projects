# SKILL_SPEC: docsearch Agent 调用规范与契约

> **定位**：本文件是 `docsearch` 作为 Agent 外部工具（Tool / Skill）的单一真理调用契约。  
> 任何 AI Agent（Claude Code, Codex, Antigravity, OpenCode 等）在需要自然语言检索本地文档库、技术手记或技能库时，请严格按照本文档的指令与输入输出规范进行调用。

---

## 一、何时使用 / 何时不用

- **何时使用**：
  - 用户想要通过自然语言/模糊描述查找本地技术方案、规范设计、踩坑记录或项目文档；
  - 关键词不确定、仅知道大致语义概念（例如“找找之前写的关于跨项目提交的规范”、“搜一下豆包设计原则”、“怎么做会话治理”）；
  - 需要跨目录、跨知识库检索 Markdown 切块并定位文件和具体行号区间。
- **何时不用**：
  - 精确文件名、扩展名查找或大文件扫描：使用 `sh-everything-search`；
  - 精确代码符号匹配、特定字符串仓内搜索：直接使用 `rg` (ripgrep) 或 grep；
  - 查看已有确切路径的文件全文：直接使用 `view_file` 或相关文件读取工具。

---

## 二、架构特性：即查即退（零常驻内存）

- **无常驻依赖**：日常无需开启 `docsearch serve` / `docsearch ui` 或后台监听进程；
- **冷启动即退**：通过单次 CLI 命令执行，从载入 LanceDB 向量索引、单句 Query 编码到排序输出仅需 ~300ms；
- **内存安全**：执行完毕 Python 进程立即销毁，占用的 1GB 模型内存瞬间 100% 归还操作系统，**零后台内存驻留**；
- **后台服务直通**：若宿主碰巧在后台运行了 WebUI 服务（默认 8765 端口），CLI 会自动优先走 HTTP 接口秒级直通响应（~10ms），绝不重复拉起第二个进程。

---

## 三、标准调用命令（原子化）

无论当前工作目录在何处，统一使用 `--directory` 显式定位到本项目根目录：

### 1. 结构化 JSON 输出（机器调用推荐）
```powershell
uv run --directory "D:\Users\language_projects\python_projects\docsearch" docsearch search "<检索关键词或自然语言句子>" --json
```

### 2. 指定返回条数上限（默认 10）
```powershell
uv run --directory "D:\Users\language_projects\python_projects\docsearch" docsearch search "<检索词>" --top-k 5 --json
```

### 3. 查看自描述 JSON Schema 契约（零 I/O 极速反射）
```powershell
uv run --directory "D:\Users\language_projects\python_projects\docsearch" docsearch search --schema
```

---

## 四、响应结构与字段定义

`--json` 输出格式遵循 Pydantic `SearchResponse` 契约：

```json
{
  "query": "检索词",
  "total": 1,
  "results": [
    {
      "filepath": "D:/Users/language_projects/docs/repo/示例.md",
      "filename": "示例.md",
      "heading": "二级标题名称",
      "start_line": 11,
      "end_line": 20,
      "content": "切块正文 Markdown 内容...",
      "score": 0.9412
    }
  ],
  "took_ms": 365.65
}
```

### 核心字段解析
| 字段 | 类型 | 说明 |
|---|---|---|
| `query` | string | 输入的原检索词 |
| `total` | int | 命中并返回的切块总数 |
| `took_ms` | float | 检索执行耗时（毫秒） |
| `results[].filepath` | string | 文档绝对路径（POSIX 风格斜杠） |
| `results[].filename` | string | 文档文件名 |
| `results[].heading` | string | 该切块所在或最近的上级 Markdown 标题 |
| `results[].start_line` | int | 切块在源文件中的起始行号（1-indexed） |
| `results[].end_line` | int | 切块在源文件中的结束行号（1-indexed） |
| `results[].content` | string | 切块正文文本片段 |
| `results[].score` | float | 综合相似度得分（0.0 ~ 1.0，越接近 1.0 越匹配） |

---

## 五、索引维护与增量更新

如果近期新建或大幅修改了文档库，需要使新内容可被检索：

```powershell
# 增量扫描并构建新向量切块（执行完自动退出释放内存）
uv run --directory "D:\Users\language_projects\python_projects\docsearch" docsearch index

# 查看当前已索引文件与切块数量
uv run --directory "D:\Users\language_projects\python_projects\docsearch" docsearch status
```
