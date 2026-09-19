-- ============================================================
-- lab07：慢查询案例实战 —— 只给现象，自己排查
-- 五个案例都来自真实业务形态。每个案例的流程：
--   ① 复现慢查询（先 CREATE 索引/跑 EXPLAIN 看到问题）
--   ② 自己判断瓶颈在哪（套用慢查询五步法）
--   ③ 动手修复（改 SQL 或补索引）
--   ④ 验证提速
-- 先别看 solutions.md！每个案例末尾只有提示，排查思路和答案都在 solutions.md。
-- 本 lab 的索引最后会 DROP。
-- ============================================================

\set ON_ERROR_STOP on

-- ============================================================
-- 案例 1：「按天统计报表」越来越慢
-- 现象：运营后台的昨日订单统计 SQL，之前 10ms，最近涨到 80ms+
-- ============================================================

CREATE INDEX idx_orders_created ON orders (created_at);   -- 开发说：明明建了索引！

EXPLAIN (ANALYZE, BUFFERS)
SELECT count(*) FROM orders WHERE created_at::date = current_date - 1;

-- ❓排查：
--   a. 索引被用了吗？以什么方式用的？（注意看是 Index Cond 还是 Filter）
--   b. 每行都在计算什么？这个计算让索引失去了什么能力？
--   c. 修复它（改写 SQL，不动索引），目标 < 1ms。

-- ============================================================
-- 案例 2：「查用户订单」接口时快时慢
-- 现象：WHERE user_id = 42 时 0.1ms，但服务层拼出来的 SQL 是
--       WHERE user_id::text = '42'，慢 400 倍
-- ============================================================

CREATE INDEX idx_orders_user ON orders (user_id);

EXPLAIN (ANALYZE, BUFFERS)
SELECT count(*) FROM orders WHERE user_id::text = '42';

-- ❓排查：
--   a. ::text 转换让索引发生了什么？（对比：EXPLAIN ANALYZE SELECT count(*) FROM orders WHERE user_id = 42;）
--   b. 这种写法通常是怎么混进代码里的？（ORM 传参类型错 / 动态拼 SQL 时统一当字符串）
--   c. 修复它。

-- ============================================================
-- 案例 3：商品搜索框「包含关键词」查不出来索引
-- 现象：LIKE '%关键词%' 全表扫，商品表涨到几十万后搜索接口超时
-- ============================================================

EXPLAIN (ANALYZE)
SELECT id, name FROM products WHERE name LIKE '%降噪耳机%';

-- ❓排查：
--   a. 为什么 % 在前面就没法用普通索引？（B-tree 的有序性需要「确定的起点」）
--   b. 提示：lab05 里提过的扩展能救它。动手装并建索引，验证提速。
--   c. （延伸）这个方案什么情况下会失效？（关键词太短，如 1 个字符——trigram 凑不齐 3 元组）

-- ============================================================
-- 案例 4：订单管理后台「翻到第 4.5 万页」超时
-- 现象：ORDER BY created_at DESC LIMIT 20 OFFSET 900000 越翻越慢
-- ============================================================

-- （沿用案例 1 建的 idx_orders_created）
EXPLAIN (ANALYZE, BUFFERS)
SELECT id, user_id, total FROM orders ORDER BY created_at DESC LIMIT 20 OFFSET 900000;

-- ❓排查：
--   a. 索引用上了，为什么还要 400ms？（LIMIT 20 是在丢掉 90 万行**之后**才生效的）
--   b. Buffers 显示读了多少页？这些页读回来干了什么？
--   c. 改用 keyset 分页（游标 = 上一页最后一行的 created_at），先取第一页：
SELECT id, created_at FROM orders ORDER BY created_at DESC LIMIT 20;
--      再用最后一条的 created_at 写下一页查询（把时间替换成你查到的值）：
EXPLAIN (ANALYZE, BUFFERS)
SELECT id, user_id, total FROM orders
WHERE created_at < '2026-09-12 12:00:00+08'     -- ← 换成上一页最后一行的 created_at
ORDER BY created_at DESC LIMIT 20;
--   d. 对比两个计划的 Execution Time，思考为什么 keyset 与页码无关。

-- ============================================================
-- 案例 5：导出接口「按用户倒序+时间正序」出现巨大 Sort
-- 现象：ORDER BY user_id DESC, created_at ASC 全表排序，内存不够 spill 到磁盘
-- ============================================================

CREATE INDEX idx_orders_user_created ON orders (user_id, created_at);

EXPLAIN (ANALYZE, BUFFERS)
SELECT id, user_id, created_at FROM orders
ORDER BY user_id DESC, created_at ASC
LIMIT 50;

-- ❓排查：
--   a. 计划里出现了什么排序节点？（PG 13+ 的 Incremental Sort——先按 user_id 用索引，
--      组内再排 created_at，已经是优化过的形态；但没到 LIMIT 深处仍要逐组排序）
--   b. 建一个方向完全匹配的索引消掉排序节点，验证。
--   c. 思考：为什么 (user_id DESC, created_at ASC) 和 ORDER BY user_id DESC, created_at DESC
--      需要两个不同的索引？（B-tree 的有序 = 各列方向也要一致；单一索引只能整体正走或倒走）

-- ============================================================
-- 清理：全部实验索引归位
-- ============================================================
DROP INDEX idx_orders_created;
DROP INDEX idx_orders_user;
DROP INDEX idx_orders_user_created;
-- （如果你在案例 3/5 建了自己的索引，也一并 DROP，保持「无二级索引」基准）

-- ---------- 思考题（总） ----------
-- 用自己的话把慢查询五步法复述一遍，并给每个案例标注它在五步法的第几步被解决。
