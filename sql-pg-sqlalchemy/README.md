# sql-pg-sqlalchemy

SQL → PostgreSQL → SQLAlchemy 三阶段学习仓。本机 Windows PostgreSQL 直连，不用 Docker。

配套速查：[CHEATSHEET.md](CHEATSHEET.md)（学完复习用）。

## 学习地图（建议顺序）

| 阶段 | 目录/文件 | 方式 | 说明 |
|---|---|---|---|
| 一、SQL 基础 | `01-sql-basics/` | psql | WHERE/JOIN/聚合/子查询 |
| 二、SQL 进阶 | `02-sql-advanced/` | psql | 窗口函数/UPSERT/NOT EXISTS |
| 三、PG 命令 | `03-pg-commands/` | psql | 元命令、类型选型、日常运维 |
| 四、索引与 EXPLAIN | `04-indexes-explain/` | psql | 7 个 lab：组合/覆盖/部分/函数/GIN 索引、慢查询案例 |
| 五、SQLAlchemy | 根目录 `01~07_*.py` | uv + Python | 见下方 |

## 阶段一~四：初始化（建 learn_pg 库 + 100 万行数据）

索引实验需要大表才看得出效果，所以先跑一次（约 1~2 分钟，可重复执行=完全重置）：

```powershell
cd setup
.\setup.ps1        # 会提示输入 postgres 密码
```

然后按各目录里的 README.md 学习。

## 阶段五：SQLAlchemy（uv + psycopg3，独立小库 sql_pg_lab）

启动三步：

```powershell
uv sync                      # 安装依赖
Copy-Item .env.example .env  # 然后编辑 .env 填 PG_PASSWORD
uv run python setup_db.py    # 建库建表 + 灌测试数据（可反复运行重置）
```

| 文件 | 主题 | Django 对照 |
|---|---|---|
| 01_engine_sql.py | Engine、原生 SQL、参数绑定、事务 | DATABASES / atomic |
| 02_models.py | 模型定义、索引、看 DDL（无需连库） | models.Model |
| 03_session_crud.py | Session 增删改查、identity map | objects.create / save / delete |
| 04_query.py | select() 过滤/聚合/分组/分页 | QuerySet API |
| 05_relations_n_plus_1.py | 关联加载策略、N+1 问题 | select_related / prefetch_related |
| 06_explain_index.py | EXPLAIN ANALYZE、索引失效实验 | — |
| 07_async_preview.py | 异步引擎（选学） | — |

运行方式：`uv run python 01_engine_sql.py`，控制台会打印每条 SQL（`ECHO_SQL=false` 可关）。

两个库各司其职：`learn_pg`（阶段一~四，大数据量）/ `sql_pg_lab`（阶段五，小数据随时重置）。

## psql 排查小抄

```text
psql -U postgres -d sql_pg_lab   # 进库
\d students                      # 看表结构和索引
\di                              # 列出索引
\x                               # 宽行显示
\timing on                       # 显示查询耗时
```
