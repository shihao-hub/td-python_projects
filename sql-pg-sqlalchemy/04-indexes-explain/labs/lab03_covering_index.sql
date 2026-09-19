-- ============================================================
-- lab03：覆盖索引 —— Index Only Scan 与「回表」的代价
-- 场景：报表页反复取「某用户的下单时间与金额」两列。
-- 本 lab 的索引最后会 DROP。
-- ============================================================

-- ---------- 第 1 步：普通索引的隐藏成本：回表 ----------
-- 先建一个「够用但不覆盖」的索引：
CREATE INDEX idx_orders_user ON orders (user_id);

EXPLAIN (ANALYZE, BUFFERS)
SELECT created_at, total FROM orders WHERE user_id = 42;   -- 1020 行

-- 📖 读计划：
--   1. Bitmap Heap Scan 的 Recheck Cond / Heap Blocks: exact=958 ——
--      1020 行散布在 958 个堆页上，每一页都要读 → Buffers 约 960+ 页。
--   2. Execution Time 约 2~3ms。「回表」就是：索引只存 user_id，
--      created_at 和 total 必须回堆里逐行取。

-- ---------- 第 2 步：升级为覆盖索引 ----------
DROP INDEX idx_orders_user;
CREATE INDEX idx_orders_user_cover ON orders (user_id) INCLUDE (created_at, total);

EXPLAIN (ANALYZE, BUFFERS)
SELECT created_at, total FROM orders WHERE user_id = 42;

-- 📖 读计划：
--   1. 节点变成了 Index Only Scan —— 查询需要的列（user_id, created_at, total）
--      全在索引里，一步到位，不再回表。
--   2. Heap Fetches: 0（一行都没回堆）；Buffers 从 ~960 页降到 ~10 页。
--   3. Execution Time 约 0.3ms。
--
-- 💡 为什么不直接建 (user_id, created_at, total) 三键列索引？
--   也可以，但 INCLUDE 语义更清晰：键列（user_id）负责定位排序，
--   载荷列（created_at, total）纯粹「搭车存储」，不参与排序、不膨胀树结构。

-- ---------- 第 3 步：破坏可见性，观察 Heap Fetches ----------
-- Index Only Scan 依赖 visibility map（VACUUM 维护）判断「这页的行对所有人可见，
-- 不用回表确认」。制造一些未 VACUUM 的写入：
BEGIN;
UPDATE orders SET total = total WHERE user_id = 42;   -- 空更新，制造新版本行
ROLLBACK;
-- 即使 ROLLBACK 了，也可能留下死元组痕迹。看看计划变化：
EXPLAIN (ANALYZE, BUFFERS)
SELECT created_at, total FROM orders WHERE user_id = 42;
-- 若 Heap Fetches 变成非 0：部分页的可见性信息失效，需要回表核对。
-- 跑 VACUUM 恢复：
VACUUM orders;
EXPLAIN (ANALYZE, BUFFERS)
SELECT created_at, total FROM orders WHERE user_id = 42;
-- Heap Fetches 回到 0。

-- ---------- 第 4 步：代价意识 ----------
-- 看看覆盖索引比普通索引大多少：
\di+ idx_orders_user_cover
-- 再对比一个「纯键」索引的大小（建了就删）：
CREATE INDEX idx_tmp ON orders (user_id);
\di+ idx_tmp
DROP INDEX idx_tmp;
-- ❓问题：多装了 created_at+total 两列，索引大了多少？
--   写入路径上每行要多维护多少字节？什么查询值得这个代价？（高频 + 固定列组合）

-- ---------- 第 5 步：清理 ----------
DROP INDEX idx_orders_user_cover;

-- ---------- 思考题 ----------
-- 1. Index Scan / Bitmap Scan / Index Only Scan 三者与「回表」的关系？
-- 2. Heap Fetches 什么时候 > 0？谁来治？（autovacuum / 手动 VACUUM）
-- 3. 为什么说覆盖索引是「用空间和写入换读取」？举一个你项目里的候选场景。
