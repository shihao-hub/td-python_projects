# 阶段 1：SQL 基础查漏补缺

> 目标：把日常后端开发要用的 SQL 全部过一遍——条件查询、聚合分组、多表 JOIN、增删改。
> 你已经会用 Django ORM 做这些事，本阶段用「ORM ↔ SQL」的映射直接搬运心智模型，重点是**补上 ORM 帮你隐藏掉的细节**。
> 环境：`psql -U postgres -d learn_pg` 进入后，把本文示例一段段贴进去跑。

## 0. 一分钟热身：这张表怎么读

```sql
SELECT id, email, status, created_at
FROM users
WHERE status = 'banned'
ORDER BY created_at DESC
LIMIT 5;
```

SQL 的**逻辑执行顺序**（和书写顺序不同，Django 链式调用没有这个问题）：

```
FROM / JOIN   →  WHERE  →  GROUP BY  →  HAVING  →  SELECT  →  ORDER BY  →  LIMIT
 ①               ②          ③           ④          ⑤           ⑥           ⑦
```

推论：`WHERE` 里不能用 `SELECT` 起的别名（⑤ 在 ② 之后才执行）；对聚合结果的筛选必须用 `HAVING` 不能用 `WHERE`。

## 1. 条件查询：WHERE / ORDER BY / LIMIT

```sql
-- AND / OR / NOT（Django: filter(a=1, b=2) / filter(Q(a=1) | Q(b=2))）
SELECT * FROM orders
WHERE status = 'paid' AND total > 1000;

-- IN / BETWEEN / LIKE / IS NULL
SELECT * FROM products WHERE category_id IN (9, 10, 11);
SELECT * FROM products WHERE price BETWEEN 100 AND 500;
SELECT * FROM products WHERE name LIKE '华为%';     -- 前缀匹配（能走索引，阶段 4 讲）
SELECT * FROM products WHERE name LIKE '%耳机%';    -- 包含匹配（前后都有 %，不走索引）
SELECT * FROM orders WHERE paid_at IS NULL;         -- NULL 判断只有 IS NULL / IS NOT NULL

-- 排序 + 分页：LIMIT 条数 OFFSET 偏移（Django 切片 [20:30] 即 LIMIT 10 OFFSET 20）
SELECT id, user_id, total FROM orders
ORDER BY total DESC, id ASC        -- 多列排序，DESC 只作用于紧邻的那一列
LIMIT 10 OFFSET 20;
```

**NULL 三值逻辑**（ORM 里被藏起来的最大坑）：

```sql
SELECT NULL = NULL;    -- NULL！不是 true。跟"未知"比较结果还是"未知"
SELECT NULL <> 1;      -- NULL。所以 WHERE paid_at <> NULL 一行都查不出来
SELECT NULL AND false; -- false    SELECT NULL AND true;  -- NULL
-- 结论：判断 NULL 只能 IS [NOT] NULL；NOT IN 遇到子查询含 NULL 会全军覆没（阶段 2 演示）
```

## 2. 聚合与分组：GROUP BY / HAVING / FILTER

```sql
-- 聚合函数把多行压成一行（Django: aggregate()）
SELECT count(*), sum(total), round(avg(total), 2) AS avg_total,
       min(created_at), max(created_at)
FROM orders;

-- GROUP BY：分组维度（Django: values('status').annotate(...)）
SELECT status,
       count(*)                    AS order_cnt,
       round(sum(total), 2)        AS amount,
       round(avg(total), 2)        AS avg_amount
FROM orders
GROUP BY status
ORDER BY order_cnt DESC;

-- HAVING：对【分组后】的结果过滤（Django: annotate 后 .filter(n__gt=…)）
SELECT user_id, count(*) AS order_cnt
FROM orders
GROUP BY user_id
HAVING count(*) > 500          -- 写成 WHERE count(*) > 500 会直接报错
ORDER BY order_cnt DESC;

-- FILTER：比 CASE WHEN 更干净的条件聚合（PG 特性，比 Django 的 Count(filter=…) 直观）
SELECT
    count(*)                                               AS all_cnt,
    count(*) FILTER (WHERE status = 'completed')           AS completed_cnt,
    count(*) FILTER (WHERE paid_at IS NOT NULL)            AS paid_cnt,
    count(DISTINCT user_id)                                AS user_cnt   -- 去重计数
FROM orders;
```

规则：`SELECT` 里出现的非聚合列**必须**出现在 `GROUP BY` 里（Django 的 values() 自动帮你对齐了这一点，裸 SQL 不会）。

## 3. 多表连接：JOIN

### 3.1 INNER JOIN（最常用）

```sql
-- 订单 + 下单用户（Django: select_related 的效果就是拼成一条 JOIN SQL）
SELECT o.id, o.total, u.email, u.nickname
FROM orders o
JOIN users u ON u.id = o.user_id
WHERE o.status = 'pending'
ORDER BY o.created_at DESC
LIMIT 10;
```

### 3.2 LEFT JOIN + 反连接（"没有 XXX 的 XXX"）

```sql
-- 左表全保留，右表没匹配上的填 NULL
SELECT u.id, u.nickname, o.id AS order_id
FROM users u
LEFT JOIN orders o ON o.user_id = u.id AND o.status = 'pending'   -- 条件写 ON
LIMIT 10;

-- 反连接：右表 IS NULL = "没有匹配"（Django 没有直接等价物，通常用两步查）
-- 例：从来没有 pending 订单的用户
SELECT u.id, u.nickname
FROM users u
LEFT JOIN orders o ON o.user_id = u.id AND o.status = 'pending'
WHERE o.id IS NULL;
```

**经典坑**：`LEFT JOIN` 后把右表条件写进 `WHERE`（而不是 `ON`），NULL 行被 WHERE 过滤掉，LEFT JOIN 悄悄退化成 INNER JOIN。右表的匹配条件永远写 `ON`，对"右表为 NULL"的筛选才写 `WHERE`。

### 3.3 多表 JOIN 链

```sql
-- 一笔订单买了什么：orders → order_items → products
SELECT o.id AS order_id, p.name AS product_name, oi.quantity, oi.unit_price,
       round(oi.quantity * oi.unit_price, 2) AS line_amount
FROM orders o
JOIN order_items oi ON oi.order_id = o.id
JOIN products p     ON p.id = oi.product_id
WHERE o.id = 42;
```

### 3.4 自连接（同一张表 Join 自己）

```sql
-- 分类树：子分类名 + 父分类名
SELECT c.id, c.name AS child_name, p.name AS parent_name
FROM categories c
LEFT JOIN categories p ON p.id = c.parent_id
ORDER BY c.id;
```

### JOIN 类型速记

| 类型 | 语义 |
|---|---|
| `JOIN` / `INNER JOIN` | 只留两边都匹配的行 |
| `LEFT JOIN` | 左表全留，右表缺匹配填 NULL |
| `RIGHT JOIN` | 同上镜像（少见，一般改写成 LEFT） |
| `FULL JOIN` | 两边全留（报表对账用） |
| `CROSS JOIN` | 笛卡尔积，行数 = 两表行数相乘（构造数据/生成序列用，阶段 4 的 seed 就是） |

## 4. 增删改：INSERT / UPDATE / DELETE

```sql
-- INSERT 单条：RETURNING 返回刚插入的行（Django 的 create() 返回带 id 的对象，SQL 靠 RETURNING）
INSERT INTO categories (name, parent_id) VALUES ('测试分类', NULL)
RETURNING id;

-- INSERT 批量：一条语句多组值 ≈ bulk_create
INSERT INTO products (name, category_id, price, stock) VALUES
    ('测试商品A', 9, 99.00, 10),
    ('测试商品B', 9, 199.00, 0);

-- INSERT ... SELECT：从查询结果灌表（阶段 0 的 seed 就是这个套路）
INSERT INTO categories (name, parent_id)
SELECT '临时-' || id, id FROM categories WHERE parent_id IS NULL;

-- UPDATE：忘写 WHERE = 全表更新！危险操作先 BEGIN 包住，确认无误再 COMMIT
BEGIN;
UPDATE products SET stock = stock - 1 WHERE id = 1;   -- stock = stock - 1 等价 F() 表达式
-- SELECT 确认结果没问题再 COMMIT；不对就 ROLLBACK
COMMIT;

-- UPDATE ... FROM：用另一张表的数据批量更新（set-based，逐行循环的天敌）
UPDATE orders o
SET total = t.amt
FROM (SELECT order_id, sum(quantity * unit_price) AS amt
      FROM order_items GROUP BY order_id) t
WHERE o.id = t.order_id;      -- seed 脚本里反算订单金额就是它

-- DELETE：被外键引用的行删不掉（约束保护），先删子表或用 ON DELETE CASCADE
DELETE FROM categories WHERE name LIKE '临时-%';
```

`psql` 默认**每条语句自动提交**。改动危险数据的习惯：先 `BEGIN;` → 执行 → `SELECT` 检查 → `COMMIT` / `ROLLBACK`。

## 5. Django ORM ↔ SQL 对照表（本阶段核心速查）

| Django ORM | 对应 SQL | 备注 |
|---|---|---|
| `User.objects.all()` | `SELECT * FROM users` | |
| `.filter(status='banned')` | `WHERE status = 'banned'` | 多个 filter/关键字参数 = AND |
| `.exclude(status='active')` | `WHERE status <> 'active'` | |
| `.filter(Q(a=1) \| Q(b=2))` | `WHERE a = 1 OR b = 2` | |
| `.filter(~Q(a=1))` | `WHERE NOT (a = 1)` | NOT 对 NULL 行不成立 |
| `.filter(id__in=[1,2,3])` | `WHERE id IN (1,2,3)` | |
| `.filter(price__range=(100,500))` | `WHERE price BETWEEN 100 AND 500` | |
| `.filter(name__contains='耳机')` | `WHERE name LIKE '%耳机%'` | 前后 %，索引失效 |
| `.filter(name__startswith('华为'))` | `WHERE name LIKE '华为%'` | 可走索引 |
| `.filter(paid_at__isnull=True)` | `WHERE paid_at IS NULL` | |
| `.order_by('-created_at')` | `ORDER BY created_at DESC` | |
| `.order_by('a', '-b')[20:30]` | `ORDER BY a, b DESC LIMIT 10 OFFSET 20` | QuerySet 切片 |
| `.get(pk=1)` | `WHERE id = 1`（期望恰好 1 行） | get 取不到抛异常；SQL 返回 0 行不报错 |
| `.first()` / `.last()` | `ORDER BY … LIMIT 1` | |
| `.exists()` | `SELECT EXISTS (SELECT 1 FROM …)` | 比 count 更省 |
| `.count()` | `SELECT count(*)` | |
| `.aggregate(Sum('total'), Avg('total'))` | `SELECT sum(total), avg(total)` | 不分组，返回单行 |
| `.values('status').annotate(n=Count('id'))` | `GROUP BY status` + `count(id)` | annotate 的聚合进 SELECT |
| `.values(...).annotate(n=…).filter(n__gt=10)` | `HAVING count(*) > 10` | Django 自动判断放 HAVING |
| `.distinct()` | `SELECT DISTINCT` | |
| `.only('id','email')` / `.defer(...)` | `SELECT id, email` | |
| `.select_related('user')` | `JOIN`（外键/一对一，单条 SQL） | |
| `.prefetch_related('items')` | 两条 SQL + 应用层组装 | 多对多/反向外键 |
| `User.objects.create(...)` | `INSERT ... RETURNING id` | PG 下 Django 用 RETURNING 拿回主键 |
| `.update(stock=F('stock')-1)` | `UPDATE ... SET stock = stock - 1` | F() = 引用列本身 |
| `.delete()` | `DELETE`（级联由 FK 的 on_delete 决定） | |
| `bulk_create(objs)` | 多值 `INSERT` | |

## 6. 练习

打开 `exercises.sql`（题目在注释里），先自己写，再对照 `solutions.sql`。全部做完进入阶段 2。

## 7. 自测清单

- [ ] 能默写 SQL 逻辑执行顺序，并解释为什么 WHERE 里不能用 SELECT 的别名
- [ ] `NULL = NULL`、`NULL <> 1` 的结果是什么？判断 NULL 只能用什么？
- [ ] WHERE 和 HAVING 的区别一句话说清
- [ ] LEFT JOIN 的右表条件写 ON 还是 WHERE？写错会发生什么？
- [ ] "没有 XX 记录的用户"用 LEFT JOIN 怎么写？（反连接）
- [ ] Django 的 `select_related` 和 `prefetch_related` 分别对应 SQL 里什么？
- [ ] `UPDATE ... FROM` 解决什么问题？为什么比"查出循环再逐条 UPDATE"好？
- [ ] psql 里执行危险 UPDATE 前的正确姿势是什么？
