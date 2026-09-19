-- ============================================================
-- 阶段 2 参考答案（与 exercises.sql 题号一一对应，全部在 learn_pg 实跑通过）
-- ============================================================

-- ---------- 题 1 ----------
\echo '>> 题 1：标量子查询'
SELECT u.id,
       u.nickname,
       (SELECT count(*) FROM orders o WHERE o.user_id = u.id)          AS order_cnt,
       (SELECT min(created_at) FROM orders o WHERE o.user_id = u.id)   AS first_order_at
FROM users u
WHERE u.id IN (6, 23, 34);

-- ---------- 题 2 ----------
\echo '>> 题 2a：IN 写法'
SELECT count(DISTINCT user_id) AS user_cnt
FROM orders
WHERE user_id IN (SELECT id FROM users)   -- 用户都有订单，这个口径演示写法
  AND created_at >= '2026-09-01' AND created_at < '2026-10-01';

-- 更能体现 IN 价值的口径：2026-09 下过单的【banned】用户
\echo '>> 题 2a：IN 写法（banned 用户口径）'
SELECT count(DISTINCT user_id) AS banned_user_cnt
FROM orders
WHERE created_at >= '2026-09-01' AND created_at < '2026-10-01'
  AND user_id IN (SELECT id FROM users WHERE status = 'banned');

\echo '>> 题 2b：EXISTS 写法（同口径）'
SELECT count(DISTINCT u.id) AS banned_user_cnt
FROM users u
WHERE u.status = 'banned'
  AND EXISTS (SELECT 1 FROM orders o
              WHERE o.user_id = u.id
                AND o.created_at >= '2026-09-01' AND o.created_at < '2026-10-01');

-- ---------- 题 3 ----------
\echo '>> 题 3a：NULL 陷阱（第 2 条返回 0 行就是坑所在）'
SELECT 1 WHERE 3 NOT IN (1, 2);
SELECT 1 WHERE 3 NOT IN (1, NULL);

\echo '>> 题 3b：NOT IN 写法（order_items.product_id 全非 NULL，所以安全）'
SELECT count(*) AS never_sold_cnt
FROM products p
WHERE p.id NOT IN (SELECT product_id FROM order_items);

\echo '>> 题 3b：NOT EXISTS 写法（推荐，天然免疫 NULL）'
SELECT count(*) AS never_sold_cnt
FROM products p
WHERE NOT EXISTS (SELECT 1 FROM order_items oi WHERE oi.product_id = p.id);

-- ---------- 题 4 ----------
\echo '>> 题 4a：相关子查询'
SELECT c.name AS category_name, p.name AS product_name, p.price
FROM products p
JOIN categories c ON c.id = p.category_id
WHERE p.price = (SELECT max(p2.price) FROM products p2
                 WHERE p2.category_id = p.category_id)
ORDER BY c.id;

\echo '>> 题 4b：窗口函数（rank 并列时会输出多条；要严格一条就换 row_number）'
SELECT category_name, product_name, price
FROM (
    SELECT c.name AS category_name, p.name AS product_name, p.price,
           rank() OVER (PARTITION BY p.category_id ORDER BY p.price DESC) AS rk
    FROM products p
    JOIN categories c ON c.id = p.category_id
) t
WHERE rk = 1
ORDER BY category_name;

-- ---------- 题 5 ----------
\echo '>> 题 5：2026 年月度报表'
WITH monthly AS (
    SELECT date_trunc('month', created_at) AS month,
           count(*)        AS order_cnt,
           sum(total)     AS amount
    FROM orders
    WHERE created_at >= '2026-01-01' AND created_at < '2027-01-01'
    GROUP BY 1
)
SELECT to_char(month, 'YYYY-MM') AS month,
       order_cnt,
       round(amount, 2) AS amount
FROM monthly
ORDER BY month;

-- ---------- 题 6 ----------
\echo '>> 题 6：递归展开分类子树'
WITH RECURSIVE tree AS (
    SELECT id, name, parent_id, 0 AS depth
    FROM categories
    WHERE id = 1
    UNION ALL
    SELECT c.id, c.name, c.parent_id, t.depth + 1
    FROM categories c
    JOIN tree t ON c.parent_id = t.id
)
SELECT id, repeat('  ', depth) || name AS name, depth
FROM tree
ORDER BY depth, id;

-- ---------- 题 7 ----------
\echo '>> 题 7：每种状态金额最大的 3 笔订单'
SELECT status, id, total
FROM (
    SELECT status, id, total,
           row_number() OVER (PARTITION BY status ORDER BY total DESC) AS rn
    FROM orders
) t
WHERE rn <= 3
ORDER BY status, total DESC;

-- ---------- 题 8 ----------
\echo '>> 题 8：近 14 天每日订单数与累计'
WITH daily AS (
    SELECT date_trunc('day', created_at) AS day, count(*) AS day_cnt
    FROM orders
    WHERE created_at >= date_trunc('day', now()) - interval '13 days'
      AND created_at <  date_trunc('day', now()) + interval '1 day'
    GROUP BY 1
)
SELECT day::date,
       day_cnt,
       sum(day_cnt) OVER (ORDER BY day) AS running_cnt
FROM daily
ORDER BY day;

-- ---------- 题 9 ----------
\echo '>> 题 9：用户 42 相邻订单间隔'
SELECT id,
       created_at,
       round(extract(epoch FROM created_at
                 - lag(created_at) OVER (PARTITION BY user_id ORDER BY created_at)) / 3600, 1
            ) AS hours_since_prev
FROM orders
WHERE user_id = 42
ORDER BY created_at;

-- ---------- 题 10 ----------
\echo '>> 题 10：Top10 消费用户及占比'
SELECT user_id,
       nickname,
       round(spent, 2)            AS spent,
       round(100.0 * spent / sum(spent) OVER (), 2) AS pct
FROM (
    SELECT o.user_id, u.nickname, sum(o.total) AS spent,
           row_number() OVER (ORDER BY sum(o.total) DESC) AS rn
    FROM orders o
    JOIN users u ON u.id = o.user_id
    GROUP BY o.user_id, u.nickname
) t
WHERE rn <= 10
ORDER BY rn;
