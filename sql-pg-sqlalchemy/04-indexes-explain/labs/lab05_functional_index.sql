-- ============================================================
-- lab05：表达式索引 —— lower(email) 与 LIKE 前缀
-- 场景：登录按邮箱查找（大小写不敏感）；搜索框按邮箱前缀提示。
-- 记住数据分布：users 5 万行，10% 的邮箱是大写（USER123@EXAMPLE.COM）。
-- 本 lab 的索引最后会 DROP。
-- ============================================================

-- ---------- 第 1 步：大小写不敏感查找，慢 ----------
-- 登录时通常把输入 lower 后比对（Django 的 normalize_email 同理）：
EXPLAIN (ANALYZE)
SELECT id, email FROM users WHERE lower(email) = 'user12345@example.com';

-- 📖 读计划：Seq Scan，Filter: lower(email) = ...，扫了 5 万行，~10ms。
--
-- ❓问题：users.email 上有唯一索引（建表时 UNIQUE 自带），为什么不用？
--   答：索引建在「email 的原始值」上，有序；查询条件是「lower(email) 的值」，
--       这是另一个函数空间，B-tree 的有序性用不上。
--       记住总规则：查询对列做了什么运算，索引就得建在同样的运算上。

-- ---------- 第 2 步：表达式索引 ----------
CREATE INDEX idx_users_email_lower ON users (lower(email));

EXPLAIN (ANALYZE)
SELECT id, email FROM users WHERE lower(email) = 'user12345@example.com';

-- 📖 读计划：Bitmap Index Scan on idx_users_email_lower，
--   Index Cond: lower(email) = ...  ← 函数计算进了索引，~0.07ms（百倍提升）。
--   注意：查询必须写一模一样的表达式 lower(email)，写 upper(email) 或 lower(EMAIL)
--   的变体都不行（planner 按表达式精确匹配）。
--
-- 进阶用法：唯一 + 表达式，实现大小写不敏感的唯一邮箱：
--   CREATE UNIQUE INDEX ON users (lower(email));   （本项目没建，避免破坏种子数据）

-- ---------- 第 3 步：LIKE 前缀查询的坑 ----------
-- 需求：输入 user1234，前缀提示匹配的邮箱：
DROP INDEX idx_users_email_lower;      -- 先清场
CREATE INDEX idx_users_email ON users (email);   -- 普通 B-tree

EXPLAIN (ANALYZE)
SELECT id, email FROM users WHERE email LIKE 'user1234%';

-- 📖 读计划：有索引却 Seq Scan！Filter: (email ~~ 'user1234%')。
--   为什么？非 C 排序规则（SHOW lc_collate; 本机是中文 Windows 排序）下，
--   B-tree 的排序顺序 ≠ 逐字符比较顺序，前缀范围无法换算成索引区间。
--   （数据库理论里只有 C collation 保证两者一致。）

-- ---------- 第 4 步：两种解法 ----------
-- 解法 A：text_pattern_ops —— 专用于模式匹配的索引 opclass，按「逐字节」排序：
CREATE INDEX idx_users_email_pat ON users (email text_pattern_ops);
EXPLAIN (ANALYZE)
SELECT id, email FROM users WHERE email LIKE 'user1234%';
-- 📖 Index Scan，Index Cond 变成了 (email ~>=~ 'user1234') AND (email ~<~ 'user1235')
--   ——planner 把前缀匹配翻译成 range 扫描，~0.05ms。
--   代价：这个索引只服务 LIKE / 比较类查询，普通 = 查询仍用原 B-tree（两个索引并存）。

DROP INDEX idx_users_email_pat;

-- 解法 B：pg_trgm —— 连 '%xxx%' 都能救（lab07 案例3 详测）：
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX idx_users_email_trgm ON users USING gin (email gin_trgm_ops);
EXPLAIN (ANALYZE)
SELECT id, email FROM users WHERE email LIKE 'user1234%';
DROP INDEX idx_users_email_trgm;

-- ---------- 第 5 步：清理 ----------
DROP INDEX idx_users_email;
-- （pg_trgm 扩展留在库里，lab07 还要用。）

-- ---------- 思考题 ----------
-- 1. 「查询对列做了运算 → 索引建在运算结果上」还能举出哪些例子？（last_login::date、
--    date_trunc('day', created_at)、upper(code)…）
-- 2. LIKE 'abc%' 在什么条件下普通 B-tree 就够用？（C collation / 纯 ASCII 业务列）
-- 3. 表达式索引为什么要求查询表达式「一字不差」？这对 ORM 封装有什么提示？
--    （封装的查询构造器必须生成稳定的表达式，别一处 lower 一处 upper）
