-- ============================================================
-- lab02：联合索引 —— 最左匹配、列顺序、ORDER BY 消 Sort
-- 场景：「用户订单列表页」：某用户某状态的订单，按时间倒序取 10 条。
-- 本 lab 的索引最后会 DROP。
-- ============================================================

-- ---------- 第 1 步：慢查询现场 ----------
-- 用户 42 的已完成订单，最新 10 笔（典型列表页查询）：
EXPLAIN (ANALYZE)
SELECT id, status, total, created_at
FROM orders
WHERE user_id = 42 AND status = 'completed'
ORDER BY created_at DESC
LIMIT 10;

-- 📖 读计划：
--   1. 出现了哪三个节点？（提示：Limit → Sort → Parallel Seq Scan）
--   2. Sort 节点在排多少行？Execution Time 总共多少毫秒？（约 130ms）
--   3. 为什么必须 Sort？（拿到的 20 行是无序的，排完才能取前 10）

-- ---------- 第 2 步：设计并创建联合索引 ----------
-- 思考：等值列（user_id、status）放前面，排序列（created_at）放最后。
CREATE INDEX idx_orders_user_status_created ON orders (user_id, status, created_at);

-- 再跑第 1 步的查询：
EXPLAIN (ANALYZE)
SELECT id, status, total, created_at
FROM orders
WHERE user_id = 42 AND status = 'completed'
ORDER BY created_at DESC
LIMIT 10;

-- 📖 读计划：
--   1. Sort 节点消失了吗？为什么？（索引 (user_id, status, created_at) 的有序性
--      + Index Scan Backward 倒着走 = 天然的 created_at DESC 顺序）
--   2. Execution Time 降到了多少？（约 0.2ms，700 倍提升）
--   3. Index Cond 里包含哪些条件？说明索引「定位」用上了几列？

-- ---------- 第 3 步：最左匹配实验 ----------
-- 换一个查询：不按用户，直接查「全部超时未支付的 pending 订单」（没有 user_id！）：
EXPLAIN (ANALYZE)
SELECT count(*) FROM orders
WHERE status = 'pending' AND created_at < now() - interval '3 days';

-- ❓问题：刚建的 (user_id, status, created_at) 索引用上了吗？为什么？
--   （status 不在最左前缀上——索引按 user_id 排序，同一 status 的行散落各处，
--    无法用 B-tree 定位。这个查询的正确索引留给 lab04。）

-- ---------- 第 4 步：中间断档 ----------
-- 只按 user_id 查 + 按 created_at 排序（状态不限定）：
EXPLAIN (ANALYZE)
SELECT id, created_at FROM orders
WHERE user_id = 7
ORDER BY created_at DESC
LIMIT 5;

-- 📖 读计划（关键现象）：
--   1. 索引用上了（user_id 前缀可用），但中间的 status 列断档 →
--      索引内部顺序是 (status, created_at) 分段的，不是全局 created_at 顺序 →
--      Sort 节点又出现了！
--   2. 好在这个用户只有 ~1000 行，Sort 很便宜（top-N heapsort）。
--      但如果这是高频接口，正确做法是再建一个 (user_id, created_at) 索引。
--   动手验证：
CREATE INDEX idx_orders_user_created ON orders (user_id, created_at);
EXPLAIN (ANALYZE)
SELECT id, created_at FROM orders
WHERE user_id = 7
ORDER BY created_at DESC
LIMIT 5;
--    Sort 消失，直接 Index Scan Backward。两个索引各司其职。

-- ---------- 第 5 步：验证一个反直觉结论 ----------
-- 只按 user_id 单列查（前缀匹配，索引可用）：
EXPLAIN (ANALYZE)
SELECT count(*) FROM orders WHERE user_id = 42;

-- ❓问题：联合索引 (user_id, status, created_at) 能服务单列 user_id 查询，
--   那还需要单独建 (user_id) 索引吗？（答：不需要——除非要给它做覆盖/唯一约束。这就是
--   「(A,B) 一个索引顶 A、A+B 两类查询用」。）

-- ---------- 第 6 步：清理 ----------
DROP INDEX idx_orders_user_status_created;
DROP INDEX idx_orders_user_created;

-- ---------- 思考题 ----------
-- 1. 联合索引 (a, b, c)，以下查询哪些能用上索引定位？
--    a) WHERE a=1 AND b=2 AND c>3     b) WHERE b=2 AND a=1      c) WHERE b=2
--    d) WHERE a=1 AND c>3             e) WHERE a>1 AND b=2      f) ORDER BY a, b（无 WHERE）
--    （答案见 solutions.md）
-- 2. 为什么「等值列在前、范围列在后」？（范围一断，后面的列全部退化为过滤）
-- 3. 什么情况下该容忍 Sort 节点存在，不值得为消 Sort 再建索引？
