-- ============================================================
-- 阶段 1 参考答案（与 exercises.sql 题号一一对应，全部在 learn_pg 实跑通过）
-- ============================================================

-- ---------- 题 1 ----------
\echo '>> 题 1：banned 用户前 10'
SELECT id, email, nickname, created_at
FROM users
WHERE status = 'banned'
ORDER BY created_at ASC
LIMIT 10;

-- ---------- 题 2 ----------
\echo '>> 题 2：分类 9 的 1000~3000 元有货商品'
SELECT name, price, stock
FROM products
WHERE category_id = 9
  AND price BETWEEN 1000 AND 3000
  AND stock > 0
ORDER BY price DESC;

-- ---------- 题 3 ----------
\echo '>> 题 3a：pending + cancelled 订单数'
SELECT count(*) AS cnt
FROM orders
WHERE status IN ('pending', 'cancelled');

\echo '>> 题 3b：小米开头的商品数'
SELECT count(*) AS cnt
FROM products
WHERE name LIKE '小米%';

\echo '>> 题 3c：tags 为 NULL 的商品数'
SELECT count(*) AS cnt
FROM products
WHERE tags IS NULL;

-- ---------- 题 4 ----------
\echo '>> 题 4：订单总览'
SELECT count(*)                    AS order_cnt,
       sum(total)                  AS total_amount,
       round(avg(total), 2)        AS avg_amount,
       min(created_at)             AS first_at,
       max(created_at)             AS last_at
FROM orders;

-- ---------- 题 5 ----------
\echo '>> 题 5：按状态统计'
SELECT status,
       count(*)             AS order_cnt,
       round(sum(total), 2) AS amount
FROM orders
GROUP BY status
ORDER BY order_cnt DESC;

-- ---------- 题 6 ----------
\echo '>> 题 6：下单超过 500 单的用户 Top5'
SELECT user_id, count(*) AS order_cnt
FROM orders
GROUP BY user_id
HAVING count(*) > 500
ORDER BY order_cnt DESC
LIMIT 5;

-- ---------- 题 7 ----------
\echo '>> 题 7：叶子分类的商品数与均价'
SELECT c.id, c.name,
       count(*)             AS product_cnt,
       round(avg(p.price), 2) AS avg_price
FROM products p
JOIN categories c ON c.id = p.category_id
WHERE c.parent_id IS NOT NULL
GROUP BY c.id, c.name
ORDER BY product_cnt DESC;

-- ---------- 题 8 ----------
\echo '>> 题 8：最近 10 笔 pending 订单 + 用户信息'
SELECT o.id, o.total, o.created_at, u.email, u.nickname
FROM orders o
JOIN users u ON u.id = o.user_id
WHERE o.status = 'pending'
ORDER BY o.created_at DESC
LIMIT 10;

-- ---------- 题 9 ----------
\echo '>> 题 9：从来没有 pending 订单的用户数'
SELECT count(*) AS no_pending_users
FROM users u
LEFT JOIN orders o ON o.user_id = u.id AND o.status = 'pending'
WHERE o.id IS NULL;

-- ---------- 题 10 ----------
\echo '>> 题 10：订单 10086 的商品明细'
SELECT p.name,
       oi.quantity,
       oi.unit_price,
       round(oi.quantity * oi.unit_price, 2) AS line_amount
FROM order_items oi
JOIN products p ON p.id = oi.product_id
WHERE oi.order_id = 10086;

-- ---------- 题 11 ----------
\echo '>> 题 11：叶子分类 + 父分类名'
SELECT c.id, c.name AS child_name, p.name AS parent_name
FROM categories c
JOIN categories p ON p.id = c.parent_id
ORDER BY c.id;

-- ---------- 题 12 ----------
\echo '>> 题 12：消费 Top5 用户'
SELECT u.id, u.nickname,
       round(sum(o.total), 2) AS total_spent,
       count(*)               AS order_cnt
FROM orders o
JOIN users u ON u.id = o.user_id
GROUP BY u.id, u.nickname
ORDER BY sum(o.total) DESC
LIMIT 5;

-- ---------- 题 13 ----------
\echo '>> 题 13：插入测试用户'
INSERT INTO users (email, nickname)        -- status/created_at 走默认值
VALUES ('test_delete_me@example.com', 'test_delete_me')
RETURNING id, email, status, created_at;

-- ---------- 题 14 ----------
\echo '>> 题 14：更新测试用户'
UPDATE orders
SET status = 'cancelled'
WHERE user_id = (SELECT id FROM users WHERE nickname = 'test_delete_me')
  AND status = 'pending';                  -- 预期 UPDATE 0：该用户没有订单

UPDATE users
SET nickname = 'test_delete_me_2'
WHERE nickname = 'test_delete_me'
RETURNING id, nickname;

-- ---------- 题 15 ----------
\echo '>> 题 15：删除测试用户'
-- 直接删通常能成功（这个测试用户没有订单）；若用户有订单，会报外键冲突：
--   ERROR: update or delete on table "users" violates foreign key constraint ...
-- 因为 orders/order_items 引用 users 的行。此时要先删其订单（order_items 有 ON DELETE CASCADE 会连带删除）：
DELETE FROM orders
WHERE user_id = (SELECT id FROM users WHERE nickname = 'test_delete_me_2');
DELETE FROM users
WHERE nickname = 'test_delete_me_2'
RETURNING id;
