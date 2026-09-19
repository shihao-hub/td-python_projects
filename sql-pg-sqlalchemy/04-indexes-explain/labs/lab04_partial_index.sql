-- ============================================================
-- lab04：部分索引（Partial Index）—— 只给关心的行建索引
-- 场景：定时任务每小时扫一遍「超过 3 天还没支付的 pending 订单」发提醒。
-- 记住数据分布：pending 只占 2%（20000/100 万），且只有它需要被这样查。
-- 本 lab 的索引最后会 DROP。
-- ============================================================

-- ---------- 第 1 步：慢查询现场 ----------
EXPLAIN (ANALYZE)
SELECT id, user_id, created_at
FROM orders
WHERE status = 'pending' AND created_at < now() - interval '3 days'
ORDER BY created_at
LIMIT 100;

-- 📖 读计划：Parallel Seq Scan + Gather Merge + Sort，
--   为找 ~19900 行 pending 扫了 100 万行，Execution Time 约 50ms。
--   每小时跑一次 = 每天白白全表扫 24 次。

-- ---------- 第 2 步：部分索引登场 ----------
-- 既然查询永远带 status='pending'，就只给这 2% 的行建索引：
CREATE INDEX idx_orders_pending_at ON orders (created_at) WHERE status = 'pending';

EXPLAIN (ANALYZE)
SELECT id, user_id, created_at
FROM orders
WHERE status = 'pending' AND created_at < now() - interval '3 days'
ORDER BY created_at
LIMIT 100;

-- 📖 读计划：
--   1. Index Scan using idx_orders_pending_at，Index Cond 只有 created_at 的条件——
--      status='pending' 已经写进索引定义了，不需要重复过滤。
--   2. created_at 有序 → 无 Sort 节点，LIMIT 提前终止。
--   3. Execution Time 约 0.2ms（约 250 倍提升）。

-- ---------- 第 3 步：和全列索引正面对比 ----------
-- 「不差钱」方案：全列联合索引 (status, created_at)：
CREATE INDEX idx_orders_status_created ON orders (status, created_at);

EXPLAIN (ANALYZE)
SELECT id, user_id, created_at
FROM orders
WHERE status = 'pending' AND created_at < now() - interval '3 days'
ORDER BY created_at
LIMIT 100;
-- 查询效果一样快（0.3ms 级）。那部分索引赢在哪？看体积：

\di+ idx_orders_pending_at
\di+ idx_orders_status_created

-- ❓问题：两者分别多大？（实测约 456KB vs 36MB，差 80 倍）
--   差异意味着：① 索引驻留内存少 80 倍；② 每次 INSERT/UPDATE 维护成本天差地别
--   （尤其 98% 的订单写入根本不用碰这个部分索引）。

-- ---------- 第 4 步：部分索引的边界 ----------
-- 前提：查询条件必须「蕴含」索引的 WHERE 条件，否则用不上。
-- 例如这个查询就永远用不上 idx_orders_pending_at（它查的是所有状态）：
EXPLAIN (ANALYZE)
SELECT count(*) FROM orders WHERE created_at < now() - interval '3 days';
-- ❓问题：计划用的什么索引/扫描？（答：不用部分索引，走全表或主键相关计划）

-- 部分索引的其他经典用途：
--   · 只给活跃数据建索引：... WHERE deleted_at IS NULL
--   · 只给未完成订单建索引：... WHERE status IN ('pending','paid')
--   · 唯一约束只对子集生效：CREATE UNIQUE INDEX ... ON orders (user_id) WHERE status <> 'cancelled'
--     （同一用户最多一张「未取消」订单——业务规则直接进数据库）

-- ---------- 第 5 步：清理 ----------
DROP INDEX idx_orders_pending_at;
DROP INDEX idx_orders_status_created;

-- ---------- 思考题 ----------
-- 1. 部分索引在什么数据分布下收益最大？（状态分布严重倾斜 + 查询只关心少数状态）
-- 2. 把 status='pending' 写进 WHERE 子句是索引生效的什么前提？
-- 3. 你的业务里有哪些「只查未完成/未删除/活跃」的查询适合部分索引？
