-- ============================================================
-- lab01：EXPLAIN 基础 —— 第一次读懂执行计划
-- 前置：psql -U postgres -d learn_pg，先跑 \timing on
-- 用法：整段复制进 psql，边跑边对照每步的问题。
-- 本 lab 的索引最后会 DROP，不影响后续 lab。
-- ============================================================

-- ---------- 第 1 步：没有索引的世界 ----------
-- orders 现在只有主键索引。查「金额很高的大额订单」：
EXPLAIN (ANALYZE, BUFFERS)
SELECT id, total FROM orders WHERE total > 35000;

-- 📖 读计划（对照 04 README 第 2、3 节）：
--   1. 用的什么扫描节点？__________
--   2. cost 里的两个数字（0.00..30614.00）分别是什么？__________
--   3. actual time 一栏显示总共花了多少毫秒？__________
--   4. Rows Removed by Filter 是多少？说明全表 100 万行里丢了多少行才得到结果？__________
--   5. Buffers: shared hit / read 各多少页？（read = 从磁盘读的页数）__________

-- ---------- 第 2 步：换个高选择率的查询 ----------
-- 只查一个很窄的金额区间（约 200 行）：
EXPLAIN (ANALYZE, BUFFERS)
SELECT id, total FROM orders WHERE total BETWEEN 31000 AND 31010;

-- ❓问题：没有索引时，命中 192 行也要扫全表——actual time 是多少？
--   和第 1 步比，为什么耗时差不多？（答案：Seq Scan 的成本 ≈ 全表行数，和命中多少无关）

-- ---------- 第 3 步：动手建第一个索引 ----------
CREATE INDEX idx_orders_total ON orders (total);

-- 再跑第 1 步的查询（total > 35000，命中约 7.2%）：
EXPLAIN (ANALYZE, BUFFERS)
SELECT id, total FROM orders WHERE total > 35000;

-- ❓问题：计划变成什么了？ Bitmap Heap Scan + Bitmap Index Scan
--   两个节点谁先执行？（提示：缩进深的先执行）
--   Execution Time 从 ~100ms 降到多少？

-- 再跑第 2 步的查询（命中 192 行）：
EXPLAIN (ANALYZE, BUFFERS)
SELECT id, total FROM orders WHERE total BETWEEN 31000 AND 31010;

-- ❓问题：Execution Time 降到了多少？（约 0.4ms，200 倍级提升）

-- ---------- 第 4 步：索引不是万能的 ----------
-- 查一个「命中一半行」的条件：
EXPLAIN (ANALYZE, BUFFERS)
SELECT id, total FROM orders WHERE total > 18000;   -- p50≈18908，命中约 53%

-- ❓问题：明明有索引，planner 为什么不用？（提示：命中行太多时，
--   走索引取回一半的表比直接顺序扫全表更贵。这就是"选择率"直觉。）

-- 等值查询（最能体现 B-tree 的场景，这个金额已知存在，共 10 行）：
EXPLAIN (ANALYZE, BUFFERS)
SELECT id, total FROM orders WHERE total = 56761.70;

-- ---------- 第 5 步：清理 ----------
DROP INDEX idx_orders_total;

-- ---------- 思考题 ----------
-- 1. 一条查询在什么情况下 Seq Scan 反而比 Index Scan 快？
-- 2. EXPLAIN 和 EXPLAIN ANALYZE 的区别？什么时候不能用 EXPLAIN ANALYZE 直接测？
--    （提示：UPDATE/DELETE 语句——要用 BEGIN + ROLLBACK 包住）
-- 3. Buffers 里 shared hit 和 read 的区别？哪个代表命中了缓存？
