-- ============================================================
-- 阶段 3 参考答案（与 exercises.sql 题号一一对应，全部在 learn_pg 实跑通过）
-- ============================================================

-- ---------- 题 1 ----------
-- 元命令操作，无 SQL 答案。
-- 关键问题的答案：\d orders 显示的索引只有 1 个（orders_pkey 主键索引）。
-- PG 的外键【不会】自动创建索引（MySQL InnoDB 会），orders.user_id 上没有索引，
-- 这是刻意保留的——阶段 4 lab02 会亲手建它、对比前后执行计划。

-- ---------- 题 2 ----------
\echo '>> 题 2：时间类型'
SELECT now()                                          AS now_ts,
       date_trunc('day', now())                       AS today_start,
       now() - interval '7 days'                      AS week_ago,
       (SELECT created_at + interval '30 minutes'
        FROM orders WHERE id = 1)                     AS order1_plus_30min,
       current_setting('TimeZone')                    AS session_tz;

-- ---------- 题 3 ----------
\echo '>> 题 3：numeric vs float'
SELECT 0.1::float8 + 0.2::float8 = 0.3   AS float_eq,      -- false！
       0.1::numeric + 0.2::numeric = 0.3 AS numeric_eq,    -- true
       0.1::float8  * 3                  AS float_x3,      -- 0.30000000000000004
       0.1::numeric * 3                  AS numeric_x3;    -- 0.3

-- ---------- 题 4 ----------
\echo '>> 题 4：UPSERT（跑两遍，第二遍名字应变为 测试分类B）'
INSERT INTO categories (id, name, parent_id)
VALUES (21, '测试分类A', NULL)
ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name
RETURNING id, name;

-- ---------- 题 5 ----------
\echo '>> 题 5：UPDATE ... RETURNING + 清理'
UPDATE categories SET name = '测试分类C' WHERE id = 21
RETURNING id, name;

DELETE FROM categories WHERE id = 21
RETURNING id;                        -- 清理，返回删掉的行

-- ---------- 题 6 ----------
\echo '>> 题 6a：热销且 < 100 元'
SELECT name, price, tags
FROM products
WHERE tags @> '["热销"]'::jsonb
  AND price < 100;

\echo '>> 题 6b：每种标签的商品数 Top5'
SELECT tag, count(*) AS cnt
FROM products,
     jsonb_array_elements_text(tags) AS tag
WHERE tags IS NOT NULL
GROUP BY tag
ORDER BY cnt DESC
LIMIT 5;

-- ---------- 题 7 ----------
\echo '>> 题 7：array_agg / string_agg'
SELECT array_agg(DISTINCT status)                AS statuses_array,
       string_agg(DISTINCT status, ', ' ORDER BY status) AS statuses_str
FROM orders;

-- ---------- 题 8 ----------
\echo '>> 题 8：generate_series 补空档日期'
WITH days AS (
    SELECT generate_series(
               date_trunc('day', now()) - interval '6 days',
               date_trunc('day', now()),
               interval '1 day'
           )::date AS day
)
SELECT d.day,
       count(o.id) AS order_cnt
FROM days d
LEFT JOIN orders o ON date_trunc('day', o.created_at) = d.day
GROUP BY d.day
ORDER BY d.day;
