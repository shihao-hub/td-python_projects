# SQL / PostgreSQL / SQLAlchemy 速成学习项目

面向有 2-3 年 Django 经验、想**快速系统补齐 SQL + PG + 索引/慢查询**的 Python 后端。
全部材料基于同一个本地样例库 `learn_pg`（电商风格：用户/商品/订单/明细），**每个结论都在真实数据上验证过**。

## 学习路线总览

| 阶段 | 目录 | 内容 | 预计耗时 |
|---|---|---|---|
| 0 | `setup/` | 环境准备：建库 + 建表 + 灌 100 万订单 | 0.5h |
| 1 | `01-sql-basics/` | SQL 基础查漏补缺（JOIN / GROUP BY / 增删改）+ Django ORM 对照 | 3-5h |
| 2 | `02-sql-advanced/` | 子查询、CTE、窗口函数（慢查询优化的前置） | 3-5h |
| 3 | `03-pg-commands/` | psql 元命令、类型系统、UPSERT、jsonb | 2-3h |
| 4 | `04-indexes-explain/` | **重点**：索引原理 + EXPLAIN + 慢查询排查实战 + 表设计原则 | 8-10h |
| 5 | `05-sqlalchemy/` | SQLAlchemy 2.0：Core/ORM、text()、Session、N+1 | 6-8h |

合计约 25-30h：每天 1.5h 约 3 周；集中学 3-4 天可过完。学完全部内容后用 `CHEATSHEET.md` 复习。

## 快速开始（阶段 0）

```powershell
# 在本目录打开 PowerShell
cd setup
.\setup.ps1                 # 建库 + 建表 + 灌数据，约 1 分钟，可反复执行（= 完全重置）
```

连接数据库（日常学习就用这一条）：

```powershell
psql -U postgres -d learn_pg
```

- 密码：优先读环境变量 `PGPASSWORD` 或 `%APPDATA%\postgresql\pgpass.conf`（格式 `localhost:5432:*:postgres:你的密码`），都没有则 `setup.ps1` 会提示输入一次。
- 中文乱码时先执行 `chcp 65001`。
- 前置条件：本机已装 PostgreSQL 13+（本项目在 PostgreSQL 18 上验证），Python 3.10+（仅阶段 5 需要）。

## 如何使用（每个阶段通用）

1. **读讲义**：各阶段 `README.md`，只放结论和示例。
2. **做练习**：`exercises.sql` / `exercises.py`，题目写在注释里，直接在 psql 里一段段跑。
3. **对答案**：`solutions.sql`（先自己写完再看，答案都在 `learn_pg` 上实跑通过）。
4. **过自测清单**：每篇讲义末尾的「自测清单」，能不假思索地答出来才算过关。

## 如何重置数据库

- 数据被练习改乱了、或想从干净状态重新开始：重跑 `setup\setup.ps1`（删库重建，约 1 分钟）。
- 注意：阶段 4 会动手创建索引，重跑 setup 会把索引也一并清掉——**这正是重新练习「加索引前后对比」的机会**。

## 样例库说明（所有练习的数据基础）

| 表 | 行数 | 说明 |
|---|---|---|
| `categories` | 20 | 商品分类，带父子层级（自连接 / 递归 CTE 练习用） |
| `users` | 5 万 | 10% 邮箱全大写（表达式索引教学）；少量 banned/inactive |
| `products` | 5 千 | 价格/库存/`tags` jsonb；5% tags 为 SQL NULL；25 件带稀有标签「停产」（GIN 教学用） |
| `orders` | **100 万** | 状态不均匀：pending 2% / cancelled 10% / paid 13% / shipped 17% / completed 58%；时间铺满最近 730 天；前 100 个 VIP 用户人均约 1000 单，其余人均约 18 单 |
| `order_items` | 200 万 | 每单 2 件商品，`orders.total` 由明细反算（讲反范式冗余时举例） |

**schema 刻意只建了主键和唯一约束，没有任何二级索引**——所有索引留给阶段 4 的 lab 里自己动手建、对比加索引前后的执行计划。另外 PG 的外键**不会**自动建索引（MySQL InnoDB 会），这一点也会在阶段 4 亲眼验证。

## 给 Django 背景的说明

- 阶段 1/2/5 的讲义里都有 **Django ORM ↔ SQL ↔ SQLAlchemy 对照表**，用你已有的 ORM 心智模型直接映射到新概念。
- 最需要警惕的两个心智迁移：
  1. **QuerySet 是惰性且可重复执行的，SQLAlchemy Session 不是**（阶段 5 专门讲）；
  2. **Django 帮你建好索引≠业务正确**，阶段 4 教你自己判断该建什么索引。

## 目录结构

```
sql-pg-sqlalchemy/
├── setup/                  # 阶段 0：建库/建表/灌数据 + 一键脚本
├── 01-sql-basics/          # 阶段 1：SQL 基础 + 练习 + 答案
├── 02-sql-advanced/        # 阶段 2：子查询/CTE/窗口函数
├── 03-pg-commands/         # 阶段 3：psql 元命令 / 类型 / UPSERT / jsonb
├── 04-indexes-explain/     # 阶段 4（重点）：索引 + EXPLAIN + 7 个动手 lab
├── 05-sqlalchemy/          # 阶段 5：SQLAlchemy 2.0，5 课 + 练习
├── CHEATSHEET.md           # 一页速查（学完复习用）
└── README.md               # 本文件
```
