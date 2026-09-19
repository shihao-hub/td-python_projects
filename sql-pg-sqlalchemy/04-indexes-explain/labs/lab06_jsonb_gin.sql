-- ============================================================
-- lab06：jsonb + GIN 索引
-- 场景：商品打了 jsonb 标签（products.tags），运营要按标签筛商品。
-- 记住数据分布：products 5 千行；「热销」等常用标签约 1000 件/个；
-- 稀有标签「停产」只有 25 件。
-- 本 lab 的索引最后会 DROP。
-- ============================================================

-- ---------- 第 1 步：没有索引 ----------
EXPLAIN (ANALYZE)
SELECT id, name FROM products WHERE tags @> '["停产"]'::jsonb;

-- 📖 读计划：Seq Scan + Filter，扫 5000 行 ~1.4ms。
--   products 只有 5 千行，全表扫不算疼——但先建立基线。
--
-- ❓思考：B-tree 索引能救这个查询吗？
--   jsonb 的 @> 是「包含」语义，不是等值/范围语义，B-tree 帮不上。
--   验证（PG 18 新增了 jsonb 的 B-tree 支持，建得出来，但看看查询用不用它；
--   PG 17 及以下则直接报错 "data type jsonb has no default operator class
--   for access method btree"——两种结果都说明了同一件事）：
CREATE INDEX idx_products_tags_btree ON products (tags);
EXPLAIN (ANALYZE) SELECT id, name FROM products WHERE tags @> '["停产"]'::jsonb;
-- 📖 依然是 Seq Scan——索引在，但 @> 不是 B-tree 认识的操作符，用不上。
DROP INDEX idx_products_tags_btree;

-- ---------- 第 2 步：GIN 索引 ----------
CREATE INDEX idx_products_tags ON products USING gin (tags);

EXPLAIN (ANALYZE)
SELECT id, name FROM products WHERE tags @> '["停产"]'::jsonb;

-- 📖 读计划：Bitmap Index Scan on idx_products_tags，
--   Index Cond: tags @> '["停产"]' ——包含查询进了索引，~0.09ms。
--   GIN = 倒排索引：把 jsonb 里出现的每个键/元素建一张「元素 → 行列表」的映射，
--   @> 包含查询 = 查倒排表取交集，天然为 jsonb/数组/全文检索而生。
--   换常用标签也试试（命中 1000 行，计划仍走 GIN）：
EXPLAIN (ANALYZE) SELECT count(*) FROM products WHERE tags ? '新品';

-- ---------- 第 3 步：哪些 jsonb 查询能吃到 GIN ----------
-- ✅ @> 包含、? 键存在、?| ?& —— 默认 jsonb_ops 全支持
-- ❌ ->> 取值后的普通比较，用不上：
EXPLAIN (ANALYZE)
SELECT id, name FROM products WHERE tags->>0 = '热销';
-- ❓问题：计划用了什么？（Seq Scan + Filter——tags->>0 每行算函数再比较，
--   GIN 帮不上。想按「第一个标签」查，就该把它设计成普通列，而不是 jsonb 里的位置。）
--
-- 💡 设计启示：jsonb 适合「结构灵活、查询浅」（包含/存在/按路径取）；
--   高频过滤/排序的字段提升为真实列 + B-tree。「NoSQL 的一切都塞 jsonb」是反模式。

-- ---------- 第 4 步：jsonb_path_ops 变体 ----------
-- 默认 jsonb_ops 为每个键值建索引条目，支持 ? 等；jsonb_path_ops 只为「路径+值」
-- 组合建条目：只支持 @>，但索引更小更快：
DROP INDEX idx_products_tags;
CREATE INDEX idx_products_tags_path ON products USING gin (tags jsonb_path_ops);

EXPLAIN (ANALYZE)
SELECT id, name FROM products WHERE tags @> '["停产"]'::jsonb;

\di+ idx_products_tags_path
-- ❓对比第 2 步的索引大小（如果忘了，可以重建默认版对比）：
CREATE INDEX idx_products_tags_default ON products USING gin (tags);
\di+ idx_products_tags_default
DROP INDEX idx_products_tags_default;

-- ---------- 第 5 步：小表之上，索引值不值 ----------
-- products 只有 5000 行，Seq 1.4ms vs GIN 0.09ms——差 15 倍但绝对值都小。
-- ❓诚实的问题：这张表值得建 GIN 吗？
--   讨论：① 若查询 QPS 很高，15 倍也值得；② 表会长大（5 千 → 50 万），Seq 的 1.4ms
--   会线性涨成 140ms，GIN 几乎不变——**索引是为表的未来规模买的保险**；
--   ③ 小表上 planner 可能本来就不选索引（它按成本算，不迁就你的期待）。

-- ---------- 第 6 步：清理 ----------
DROP INDEX idx_products_tags_path;

-- ---------- 思考题 ----------
-- 1. 为什么 @> 用不了 B-tree？GIN 的「倒排」思想一句话？
-- 2. jsonb_ops 和 jsonb_path_ops 的取舍？
-- 3. tags->>0 = 'x' 为什么不走索引？这个需求更好的表设计是什么？
-- 4. 数组列 text[] 的 @> 查询配什么索引？（也是 GIN，array_ops——同理）
