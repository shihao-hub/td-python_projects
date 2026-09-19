# 阶段 2：SQL 进阶（子查询 / CTE / 窗口函数）

> 目标：掌握慢查询优化的两块前置拼图——
> ① 会读/会写嵌套子查询和 CTE（阶段 4 的慢查询很多要靠改写救命）；
> ② 窗口函数（"每组 Top-N""累计""环比"这类 ORM 里很难写的需求，SQL 一个 OVER 搞定）。
> 环境：`psql -U postgres -d learn_pg`。

## 1. 子查询的三种位置

```sql
-- ① 标量子查询（返回单个值）：只能用在期望"一个值"的地方
SELECT nickname,
       (SELECT count(*) FROM orders o WHERE o.user_id = u.id) AS order_cnt
FROM users u
WHERE u.id IN (6, 23, 34);

-- ② 列子查询（返回一列）：用在 IN / EXISTS / ANY / ALL
SELECT count(*) FROM users
WHERE id IN (SELECT user_id FROM orders WHERE status = 'pending');

-- ③ 表子查询（返回一张表）：用在 FROM / JOIN，必须起别名
SELECT t.status, t.cnt
FROM (SELECT status, count(*) AS cnt FROM orders GROUP BY status) t
WHERE t.cnt > 100000;
```

## 2. EXISTS vs IN（重点）

```sql
-- IN：先算出子查询的完整列表，再逐个比对
SELECT count(*) FROM orders
WHERE user_id IN (SELECT id FROM users WHERE status = 'banned');

-- EXISTS：相关子查询，外层每行进去问一次"存在吗"，命中即短路返回
SELECT count(*) FROM orders o
WHERE EXISTS (SELECT 1 FROM users u WHERE u.id = o.user_id AND u.status = 'banned');
```

怎么选（经验法则）：

| 场景 | 用什么 | 原因 |
|---|---|---|
| 子查询结果很小（几十几百个 id） | IN | 一次算完列表，比对快 |
| 子查询表很大 / 外层表很小 | EXISTS | 外层每行驱动一次索引探测，不用物化大列表 |
| 「不存在」语义（NOT IN / NOT EXISTS） | **NOT EXISTS** | NOT IN 遇到 NULL 直接翻车（见下） |

**NOT IN 的 NULL 陷阱**（必亲手跑一遍）：

```sql
SELECT 1 WHERE 1 NOT IN (1, 2);        -- 0 行，正常
SELECT 1 WHERE 3 NOT IN (1, 2);        -- 1 行，正常
SELECT 1 WHERE 3 NOT IN (1, NULL);     -- 0 行！3 <> NULL 结果是 NULL，WHERE 只放行 true
-- 子查询返回的列里只要有 NULL，NOT IN 整个查询一行都查不出来 —— 永远用 NOT EXISTS
```

## 3. 相关子查询 与 JOIN 的互相改写

```sql
-- 需求：每个分类最贵的商品
-- 写法 A：相关子查询（好读，但外层每行执行一次内层）
SELECT c.name, p.name AS product_name, p.price
FROM products p
JOIN categories c ON c.id = p.category_id
WHERE p.price = (SELECT max(p2.price) FROM products p2 WHERE p2.category_id = p.category_id);

-- 写法 B：窗口函数（一次扫描，阶段 4 讲性能时回头看）
SELECT name, product_name, price FROM (
    SELECT c.name, p.name AS product_name, p.price,
           rank() OVER (PARTITION BY p.category_id ORDER BY p.price DESC) AS rk
    FROM products p JOIN categories c ON c.id = p.category_id
) t WHERE rk = 1;
```

经验：相关子查询表达"每行关联算一下"最直观；数据量大时改写成 JOIN / 窗口（EXPLAIN 对比放阶段 4）。

## 4. CTE（WITH 子句）

```sql
-- 基础：给中间结果起名字，代替层层嵌套的派生表
WITH monthly AS (
    SELECT date_trunc('month', created_at) AS month,
           count(*) AS cnt, sum(total) AS amount
    FROM orders
    WHERE created_at >= '2026-01-01'
    GROUP BY 1
)
SELECT to_char(month, 'YYYY-MM') AS month, cnt, round(amount, 2)
FROM monthly
ORDER BY month;

-- 一个 CTE 可以被引用多次（派生表做不到）
WITH banned AS (SELECT id FROM users WHERE status = 'banned')
SELECT
    (SELECT count(*) FROM orders WHERE user_id IN (SELECT id FROM banned)) AS banned_orders,
    (SELECT round(avg(total), 2) FROM orders WHERE user_id IN (SELECT id FROM banned)) AS banned_avg;
```

### 递归 CTE（树形结构就靠它）

```sql
-- 从「手机数码」(id=1) 向下展开整棵子树
WITH RECURSIVE tree AS (
    SELECT id, name, parent_id, 0 AS depth        -- 锚点：起点行
    FROM categories WHERE id = 1
    UNION ALL
    SELECT c.id, c.name, c.parent_id, t.depth + 1 -- 递归部分：引用自己
    FROM categories c
    JOIN tree t ON c.parent_id = t.id
)
SELECT id, repeat('  ', depth) || name AS name, depth
FROM tree
ORDER BY depth, id;
```

理解要点：锚点查询 + 递归查询 `UNION ALL` 起来；数据库反复执行递归部分直到没有新行。Django ORM 做不了这件事，得 `raw()`。

## 5. 窗口函数（本阶段最重要的增量）

聚合函数把 N 行压成 1 行；**窗口函数给每一行都附上一个"在其所属窗口里算出来的值"**，行数不变。

```sql
SELECT id, user_id, status, total,
       row_number() OVER (PARTITION BY user_id ORDER BY created_at DESC) AS rn_user,
       rank()       OVER (ORDER BY total DESC)                           AS r_total,
       sum(total)   OVER (PARTITION BY user_id)                          AS user_total,     -- 分组小计
       sum(total)   OVER (ORDER BY created_at)                           AS running_total,  -- 累计！
       round(avg(total) OVER (PARTITION BY user_id), 2)                  AS user_avg
FROM orders
WHERE user_id IN (6, 23);
```

解剖 `OVER (PARTITION BY … ORDER BY …)`：

- `PARTITION BY`：窗口按什么**分组**（不写 = 全表一个窗口）
- `ORDER BY`：窗口内按什么**排序**——排序后聚合函数（sum/avg/count）会变成"累计到当前行"；序号函数必须有它
- 窗口函数**只能出现在 SELECT 和 ORDER BY**，不能进 WHERE → 所以"过滤窗口结果"要套一层子查询/CTE

### 常用窗口函数

| 函数 | 语义 | 经典用途 |
|---|---|---|
| `row_number()` | 1,2,3,4（并列也强行区分） | 每组取 N 条、去重 |
| `rank()` | 1,2,2,4（并列同名次，跳号） | 排行榜 |
| `dense_rank()` | 1,2,2,3（并列不跳号） | 排行榜（并列算同一名） |
| `sum/avg/count() OVER` | 分组小计 / 累计 / 移动平均 | 报表 |
| `lag(col, n)` / `lead(col, n)` | 取前/后第 n 行的值 | 环比、间隔、断档检测 |
| `first_value() / nth_value()` | 窗口内第 1 / 第 n 个值 | 每组最优值 |
| `ntile(4)` | 均分成 4 桶编号 1~4 | 四分位分析 |

### 三个实战 pattern（背下来）

```sql
-- pattern 1：每组 Top-N（"每个用户最近 3 笔订单"）
SELECT * FROM (
    SELECT o.*,
           row_number() OVER (PARTITION BY user_id ORDER BY created_at DESC) AS rn
    FROM orders o
) t
WHERE rn <= 3;

-- pattern 2：用 row_number 去重（保留每组最老/最新一条）
--   假设 dup_orders 里有重复数据，按 (email, created_at) 分组各留一条：
--   DELETE FROM dup_orders WHERE id IN (
--       SELECT id FROM (
--           SELECT id, row_number() OVER (PARTITION BY email, created_at
--                                        ORDER BY id) AS rn
--           FROM dup_orders) t
--       WHERE rn > 1);

-- pattern 3：环比 / 与上一单的间隔
SELECT id, user_id, created_at,
       lag(created_at) OVER (PARTITION BY user_id ORDER BY created_at) AS prev_at,
       round(extract(epoch FROM created_at
                 - lag(created_at) OVER (PARTITION BY user_id ORDER BY created_at)) / 3600, 1) AS hours_since_prev
FROM orders
WHERE user_id = 6;
```

Django 对照：Django 2.0+ 有 `Window` 表达式（`F('total').asc()` 之类 + `from django.db.models.functions import Rank, RowNumber`），能用但写起来费劲且组合受限；复杂分析直接写 SQL（阶段 5 的 `text()` 就是干这个的）。

## 6. 练习

打开 `exercises.sql`（10 题），先写再对 `solutions.sql`。

## 7. 自测清单

- [ ] IN、EXISTS 各适合什么场景？「不存在」语义为什么必须 NOT EXISTS？
- [ ] `3 NOT IN (1, NULL)` 返回几行？为什么？
- [ ] 相关子查询和 JOIN 在表达"每组最 X"时怎么互换？
- [ ] 递归 CTE 的结构分哪两部分？Django ORM 能做递归查询吗？
- [ ] 窗口函数的 PARTITION BY 和 GROUP BY 区别一句话说清
- [ ] 为什么窗口函数不能写在 WHERE 里？"过滤窗口结果"的正确姿势？
- [ ] `row_number` / `rank` / `dense_rank` 对 [100, 90, 90, 80] 分别输出什么？
- [ ] 累计求和怎么写？（OVER 里加什么就变累计）
