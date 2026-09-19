-- ============================================================
-- 03_seed.sql —— 灌数据
-- 小表手写 + 大表 generate_series 集合式生成（无循环，秒级~分钟级）
--
-- 数据分布刻意设计（阶段 4 的 lab 依赖这些分布）：
--   * orders 100 万行：
--       - status 不均匀：pending 2% / cancelled 10% / paid 13% / shipped 17% / completed 58%
--         → 部分索引（partial index）收益肉眼可见
--       - created_at 均匀铺满最近 730 天，每天约 1370 单
--       - user_id 取模分布：10% 的订单集中在前 100 个 VIP 用户（人均约 1000 单），
--         其余用户人均约 18 单 → 直方图/统计信息教学点
--   * products 5 千行：tags 有 5% SQL NULL；1/200 带「停产」标签（约 25 行，GIN 索引教学用）
--   * users 5 万行：10% 邮箱全大写（表达式索引 lower(email) 教学用）
-- ============================================================

\set ON_ERROR_STOP on
\timing on

-- 批量灌数据时跳过每次提交的 fsync 等待，明显提速（仅数据初始化用，业务代码勿学）
SET synchronous_commit = off;

-- ---------- 1/5 分类（20 行：8 个顶级 + 12 个子级） ----------
\echo '== 1/5 categories =='
INSERT INTO categories (id, name, parent_id) VALUES
    (1,  '手机数码', NULL),
    (2,  '电脑办公', NULL),
    (3,  '家用电器', NULL),
    (4,  '数码影音', NULL),
    (5,  '服饰鞋包', NULL),
    (6,  '食品生鲜', NULL),
    (7,  '图书文娱', NULL),
    (8,  '运动户外', NULL),
    (9,  '手机',     1),
    (10, '智能穿戴', 1),
    (11, '笔记本',   2),
    (12, '台式机',   2),
    (13, '外设配件', 2),
    (14, '大家电',   3),
    (15, '厨房电器', 3),
    (16, '影音设备', 4),
    (17, '相机摄影', 4),
    (18, '男装',     5),
    (19, '女装',     5),
    (20, '休闲零食', 6);
SELECT setval('categories_id_seq', 20);

-- ---------- 2/5 用户（5 万行） ----------
\echo '== 2/5 users（5 万行） =='
INSERT INTO users (email, nickname, status, created_at)
SELECT
    CASE WHEN g % 10 = 0                                          -- 10% 大写邮箱：表达式索引教学用
         THEN 'USER' || g || '@EXAMPLE.COM'
         ELSE 'user' || g || '@example.com'
    END,
    'nick_' || g,
    CASE WHEN g % 20 = 0 THEN 'banned'                            -- 2500 封禁
         WHEN g % 7  = 0 THEN 'inactive'                          -- ~6400 停用
         ELSE 'active'
    END,
    now() - (random() * 1095) * interval '1 day'                  -- 注册时间铺满近 3 年
FROM generate_series(1, 50000) g;

-- ---------- 3/5 商品（5 千行） ----------
\echo '== 3/5 products（5 千行） =='
INSERT INTO products (name, category_id, price, stock, tags, created_at)
SELECT
    s.name,
    7 + s.g % 14,                                                 -- 只挂叶子分类（id 7..20）
    s.price,
    s.stock,
    CASE WHEN s.g % 20  = 0 THEN NULL                             -- 5% SQL NULL
         WHEN s.g % 200 = 7 THEN s.tags || '["停产"]'::jsonb      -- ~25 行带稀有标签「停产」（用 7 避开上面的 %20）
         ELSE s.tags
    END,
    now() - (random() * 730) * interval '1 day'
FROM (
    SELECT g,
           (ARRAY['华为','小米','苹果','联想','戴尔','海尔','美的','格力','索尼','飞利浦','安踏','优衣库'])[1 + g % 12]
           || ' '
           || (ARRAY['无线耳机','蓝牙耳机','降噪耳机','机械键盘','电竞显示器','移动电源','智能手表','平板电脑','滚轮鼠标','游戏本','台式机','单反相机','扫地机器人','空气炸锅','电饭煲'])[1 + (g * 7) % 15]
           || ' '
           || lpad(g::text, 4, '0')                               -- 例: '小米 无线耳机 0007'
           AS name,
           round((99 + (g * 37) % 9900 + (g % 5) * 0.5)::numeric, 2) AS price,   -- 99.00 ~ 9999.00
           CASE WHEN g % 30 = 0 THEN 0 ELSE (g * 11) % 500 END AS stock,         -- 1/30 缺货
           to_jsonb(ARRAY[
               (ARRAY['热销','新品','包邮','官方旗舰','自营'])[1 + g % 5],
               (ARRAY['限时折扣','人气','爆款','轻薄','大容量'])[1 + (g * 3) % 5],
               (ARRAY['无线','降噪','快充','防水','便携'])[1 + (g * 7) % 5]
           ]) AS tags
    FROM generate_series(1, 5000) g
) s;

-- ---------- 4/5 订单（100 万行，核心教学表） ----------
\echo '== 4/5 orders（100 万行，generate_series 集合式插入） =='
INSERT INTO orders (id, user_id, status, total, created_at, paid_at)
SELECT
    s.id,
    CASE WHEN s.id % 10 = 0 THEN 1 + (s.id / 10) % 100            -- VIP：前 100 用户各约 1000 单，吃掉 10% 订单
         ELSE 1 + ((s.id * 7919) % 50000)
    END,
    s.status,
    0,                                                            -- 占位，稍后按明细反算
    s.created_at,
    CASE WHEN s.status IN ('paid', 'shipped', 'completed')        -- 未支付/已取消 → paid_at 为 NULL
         THEN s.created_at + ((s.id * 3) % 72) * interval '1 hour'
    END
FROM (
    SELECT g AS id,
           CASE
               WHEN (g * 2654435761) % 1000 < 20  THEN 'pending'   --  2%
               WHEN (g * 2654435761) % 1000 < 120 THEN 'cancelled' -- 10%
               WHEN (g * 2654435761) % 1000 < 250 THEN 'paid'      -- 13%
               WHEN (g * 2654435761) % 1000 < 420 THEN 'shipped'   -- 17%
               ELSE 'completed'                                    -- 58%
           END AS status,
           now() - (g % 730) * interval '1 day'
                   - ((g * 17) % 86400) * interval '1 second'     -- 均匀铺满最近 730 天
           AS created_at
    FROM generate_series(1::bigint, 1000000) g                     -- 用 bigint 版 generate_series，int 乘 7919/2654435761 会溢出
) s;
-- 显式插入了 id，把序列拨到正确位置，后续手工 INSERT 不再冲突
SELECT setval('orders_id_seq', (SELECT max(id) FROM orders));

-- 订单明细：每单 2 件商品（200 万行）
\echo '== 4b/5 order_items（200 万行） =='
INSERT INTO order_items (order_id, product_id, quantity, unit_price)
SELECT g,
       p.id,
       1 + (g + n) % 3,                                           -- 每件买 1~3 个
       round(p.price * (0.8 + (g % 21)::numeric / 50), 2)         -- 成交价 = 标价 × 0.8~1.2
FROM generate_series(1, 1000000) g
CROSS JOIN generate_series(1, 2) n
JOIN products p ON p.id = 1 + ((g * 31 + n * 977) % 5000);

-- ---------- 5/5 反算订单金额（set-based UPDATE ... FROM，本身是个教学点） ----------
\echo '== 5/5 反算 orders.total =='
UPDATE orders o
SET total = t.amt
FROM (
    SELECT order_id, sum(quantity * unit_price) AS amt
    FROM order_items
    GROUP BY order_id
) t
WHERE o.id = t.order_id;

RESET synchronous_commit;

-- 更新统计信息 + 可见性映射（阶段 4 的 Index Only Scan 实验依赖它）
VACUUM (ANALYZE) categories;
VACUUM (ANALYZE) users;
VACUUM (ANALYZE) products;
VACUUM (ANALYZE) orders;
VACUUM (ANALYZE) order_items;

-- ---------- 数据分布抽查（应与文件头注释一致） ----------
\echo '== 数据分布抽查 =='
SELECT 'categories'  AS table_name, count(*) FROM categories
UNION ALL SELECT 'users',        count(*) FROM users
UNION ALL SELECT 'products',     count(*) FROM products
UNION ALL SELECT 'orders',       count(*) FROM orders
UNION ALL SELECT 'order_items',  count(*) FROM order_items;

SELECT status, count(*),
       round(100.0 * count(*) / sum(count(*)) OVER (), 1) AS pct
FROM orders
GROUP BY status
ORDER BY count(*) DESC;

SELECT count(*) FILTER (WHERE tags IS NULL)              AS null_tags,
       count(*) FILTER (WHERE tags @> '["停产"]'::jsonb) AS discontinued
FROM products;

-- VIP 用户订单量应约为普通用户的 50 倍以上
SELECT user_id, count(*) AS order_cnt
FROM orders
GROUP BY user_id
ORDER BY order_cnt DESC
LIMIT 5;
