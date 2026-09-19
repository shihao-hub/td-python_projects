-- ============================================================
-- 阶段 3 练习：psql 元命令 / 类型系统 / UPSERT / jsonb（共 8 题）
-- 第 1 题是元命令操作，在 psql 交互模式里做；其余写 SQL。
-- 第 4、5 题会写入数据，做完记得清理（答案里有清理语句）。
-- ============================================================

-- ---------- 题 1（psql 元命令，交互模式下操作） ----------
-- 依次执行并观察输出：
--   \l              —— 找到 learn_pg
--   \dt             —— 应看到 5 张表
--   \d orders       —— 看列、索引（现在只有主键）、外键、CHECK 约束
--   \di             —— 现在只有 6 个索引（5 主键 + users.email 唯一）
--   \timing on      —— 打开计时
--   SELECT count(*) FROM orders;   —— 感受 100 万行 count 的耗时
--   \x              —— 切换旋转显示，再跑一遍上面的 count，观察格式变化
--   \pset null '∅'  —— SELECT paid_at FROM orders LIMIT 5; 把 NULL 变成可见符号
-- 问题：\d orders 里显示的索引有几个？为什么 orders.user_id 上没有索引（提示：PG 外键不自动建索引）？

-- ---------- 题 2（时间类型） ----------
-- 一条 SELECT 返回：当前时刻、今天 0 点（date_trunc）、7 天前的此刻、
-- 订单 id=1 的 created_at 加 30 分钟后的值、当前会话时区。

-- ---------- 题 3（numeric vs float） ----------
-- 在一个 SELECT 里同时验证：
--   0.1::float8 + 0.2::float8 = 0.3 的结果
--   0.1::numeric + 0.2::numeric = 0.3 的结果
-- 再算一下：把 0.1 元钱乘 3（三种数量），float 和 numeric 各来一遍，直接看到差异。

-- ---------- 题 4（UPSERT） ----------
-- 执行两次下面这条（一行 SQL 跑两遍），观察行为：
--   INSERT INTO categories (id, name, parent_id) VALUES (21, '测试分类A', NULL)
--   第一次正常插入；请补全 ON CONFLICT 子句，使第二次执行时把名字改成 '测试分类B'，
--   并用 RETURNING id, name 观察。

-- ---------- 题 5（UPDATE ... RETURNING） ----------
-- 把 id=21 的分类改名为 '测试分类C' 并 RETURNING 旧值效果：
-- UPDATE ... SET name = '测试分类C' WHERE id=21 RETURNING id, name;
-- 然后清理：DELETE FROM categories WHERE id = 21;

-- ---------- 题 6（jsonb 查询） ----------
-- 6a. tags 含「热销」且价格低于 100 元的商品：name、price、tags（预期 0~几行）。
-- 6b. 统计每种 tag 出现的商品数，按数量降序，取前 5。
--     （提示：jsonb_array_elements_text 展开，记得排除 tags IS NULL）

-- ---------- 题 7（数组/聚合函数） ----------
-- 一条 SQL 返回 orders 表出现过的所有状态（去重）：
--   分别用 array_agg(DISTINCT ...) 和 string_agg(DISTINCT ..., ',') 两种聚合各出一列。

-- ---------- 题 8（generate_series 补空档） ----------
-- 最近 7 天（含今天）每天的订单数，**没有订单的日期也要显示一行（计数为 0）**。
-- 提示：generate_series 生成日期骨架，LEFT JOIN 订单数据。
-- 输出列：day、order_cnt，按 day 升序。
