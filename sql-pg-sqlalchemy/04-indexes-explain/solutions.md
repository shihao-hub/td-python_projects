# 阶段 4 labs 排查思路与答案

> 所有数字均为 100 万行 orders 上的实测值（PostgreSQL 18，本机）。你跑出来的绝对耗时会因机器而异，但**计划形态和倍数关系**应该一致。

---

## lab01 EXPLAIN 基础

**第 1 步（Seq Scan 现场参考）**：Seq Scan on orders，cost=0.00..30614.00，actual ≈ 96ms，Rows Removed by Filter ≈ 92.7 万，Buffers shared hit≈15737 read≈2377。

**第 2 步**：BETWEEN 31000~31010 无索引时是 Parallel Seq Scan（约 52ms）——**命中 192 行也要扫全表**，因为 Seq Scan 的代价由表大小决定，与命中多少无关。

**第 3 步**：建 `idx_orders_total` 后：

- `total > 35000`（7.2%）→ **Bitmap Heap Scan + Bitmap Index Scan**，~34ms。Bitmap 先做「行位置位图」，再按堆物理顺序批量取，比逐行回表少了随机 I/O。
- `BETWEEN 31000 AND 31010`（192 行）→ Bitmap Index Scan，**~0.39ms**，提升 100 倍级。

**第 4 步（索引不是万能）**：`total > 18000` 命中约 53%，planner **放弃索引选 Seq Scan**——走索引要回表取回一半的表，比顺序扫更贵。「看到 Seq Scan」不等于「有病」，要看命中比例。

**思考题**：
1. 命中行占比高（经验 >5~10%）、或表本身很小（几页）时，Seq Scan 更快。
2. EXPLAIN 只显示计划不执行；EXPLAIN ANALYZe 真执行。测 UPDATE/DELETE 必须包 `BEGIN ... ROLLBACK`。
3. shared hit = 从 shared_buffers（内存）读的页；read = 触发磁盘/OS 缓存读的页。

---

## lab02 联合索引

**第 1 步**：Limit → Sort → Parallel Seq Scan，~135ms。Sort 是因为拿到的行无序。
**第 2 步**：`(user_id, status, created_at)` 后 → **Index Scan Backward**，Index Cond 含两个等值条件，Sort 消失，**~0.18ms（约 770 倍）**。索引顺序 + 倒序遍历 = 现成的 `ORDER BY created_at DESC`，LIMIT 提前终止。

**第 3 步（最左匹配）**：`status='pending'` 不含 user_id → 索引按 user_id 排序，pending 的行散落在各处，无法定位 → Parallel Seq Scan。正确解法在 lab04（部分索引）。

**第 4 步（中间断档）**：`user_id=7 ORDER BY created_at` → 索引可用 user_id 前缀，但 status 断档后索引内 created_at 是「按 status 分段有序」，全局无序 → **Sort 节点回来了**（Bitmap + top-N heapsort ~1.3ms）。补 `(user_id, created_at)` 后 Sort 消失，直接 Index Scan Backward。

**第 5 步**：`(user_id, status, created_at)` 可服务单列 user_id 查询（前缀），无需重复建 `(user_id)`。

**思考题 1（(a,b,c) 索引）**：

| 查询 | 结论 |
|---|---|
| a) `a=1 AND b=2 AND c>3` | ✅ 三列全用（c 范围定位） |
| b) `b=2 AND a=1` | ✅ 书写顺序无关，planner 按列名对齐，等价 a+b |
| c) `b=2` | ❌ 缺最左列 a |
| d) `a=1 AND c>3` | ⚠️ 只 a 定位；b 断档，c 只能过滤（lab04 断档实验同型） |
| e) `a>1 AND b=2` | ⚠️ 只 a 范围可用；范围列之后的 b 无法定位 |
| f) `ORDER BY a, b` | ✅ 索引天然有序，免 Sort（但无 WHERE 时全索引扫描不便宜，planner 会权衡） |

**思考题 2**：范围条件会把索引「后面的列」全部变成不可定位——断档之后只能逐行过滤，所以等值在前、范围在后。
**思考题 3**：待排序行数少（如几千行）且查询频率低时，内存 Sort 只需零点几毫秒，为它多维护一个索引（写放大 + 空间）不值。

---

## lab03 覆盖索引

- 普通 `(user_id)`：Bitmap Heap Scan，1020 行散在 **958 个堆页** → ~960 次 buffer 访问，~2.8ms。
- `(user_id) INCLUDE (created_at, total)`：**Index Only Scan，Heap Fetches: 0**，Buffers ~10 页，~0.3ms。要查的列全在索引里，不回表。
- 第 3 步：制造脏数据后 Heap Fetches 可能 >0（visibility map 失效，需回表确认可见性），`VACUUM orders;` 后归零——这就是 autovacuum 影响读性能的隐秘方式。
- 第 4 步：覆盖索引比纯键索引大（多背了两列的载荷），每次写入多维护相应字节。值得为「高频 + 固定列组合」的查询建。

**思考题**：1. Index/Bitmap 都要回表取整行，Index Only 免回表；2. 页面可见性映射失效时，autovacuum/VACUUM 治；3. 开放题，例：消息列表页只取 (id, title, created_at)。

---

## lab04 部分索引

- 现场Parallel Seq + Sort ~52ms。
- `ON orders (created_at) WHERE status='pending'`：Index Scan，status 条件已内化进索引，**~0.23ms（约 250 倍）**。
- 全列 `(status, created_at)` 同样快（~0.37ms），但体积 **36MB vs 456KB（80 倍）**：内存驻留、写入维护全面占优；98% 的非 pending 写入根本不碰部分索引。
- 边界：查询条件必须蕴含 `status='pending'`，查所有状态的 created_at 条件用不上它。

**思考题**：1. 状态列严重倾斜 + 查询只关心少数状态（未支付、未删除、活跃）；2. 蕴含前提；3. 软删除表（deleted_at IS NULL）、任务表（未完成态）是两大高频场景。

---

## lab05 表达式索引

- `lower(email)='x'`：email 的唯一索引帮不上（索引建在原值上，查询在函数空间里）→ Seq ~9.8ms。
- `ON users (lower(email))` → Bitmap Index Scan **~0.07ms**。查询表达式必须与索引表达式一字不差。
- `LIKE 'user1234%'` + 普通 B-tree → Seq（非 C 排序规则下 B-tree 顺序 ≠ 字符序）。
- `text_pattern_ops` → Index Scan（`~>=~`/`~<~` 边界扫描），**~0.05ms**；pg_trgm GIN 也可（且能救 `%x%`）。

**思考题**：1. `created_at::date`、`date_trunc('day', ts)`、`upper(code)`、`(a+b)`；2. C collation 或 binary 排序；3. ORM 封装必须生成稳定一致的表达式，两处写法不一致就一边索引失效。

---

## lab06 jsonb + GIN

- 无索引 / B-tree：`@>` 走不了（PG18 能建出 jsonb B-tree，但 @> 不是 B-tree 操作符；PG17- 直接报无 opclass）。
- `USING gin (tags)` → Bitmap Index Scan，25 行 **~0.09ms**（Seq ~1.4ms）。`? '新品'`（1000 行）同样走 GIN。
- `tags->>0 = '热销'` → Seq + Filter：`->>` 是取值函数，GIN 不认识。设计启示：高频过滤字段提升为真实列。
- `jsonb_path_ops`：只支持 `@>`，索引更小更快；需要 `?`/`?|` 时用默认 `jsonb_ops`。

**思考题**：1. @> 是包含语义无序可言，GIN 倒排 = 元素 → 行号集合；2. 只用 @> 选 path_ops；3. 建普通列 + B-tree；4. `CREATE INDEX ... USING gin (arr)`（array_ops 默认）。

---

## lab07 慢查询案例（重点）

### 案例 1：`created_at::date = current_date - 1`

- **排查**：计划是（Parallel）Index Only Scan on idx_orders_created，但条件在 **Filter** 不在 Index Cond——索引被「全量扫了一遍」，每行计算 `::date` 再过滤，Rows Removed ≈ 99.9 万，~81ms。函数包裹列 = 索引失去定位能力。
- **修复（改写为半开区间，经典手法）**：

```sql
SELECT count(*) FROM orders
WHERE created_at >= current_date - 1   -- 昨天零点（含）
  AND created_at < current_date;       -- 今天零点（不含）
```

- **验证**：Index Only Scan + Index Cond 范围定位，~**0.16ms（500 倍）**。原则：**对索引用范围，不对索引用函数**。
- 另一条路：表达式索引 `ON orders ((created_at::date))` 也能快，但改 SQL 更好（索引不用多维护一份）。

### 案例 2：`user_id::text = '42'`

- **排查**：有 `(user_id)` 索引，但 `::text` 把每行的 int 转成文本再比较 → Filter，~47ms；写 `user_id = 42` 时 Index Only Scan ~0.086ms（500 倍）。
- **修复**：参数类型用对。排查方向：ORM 的字段类型声明、动态拼 SQL 时把一切当字符串、跨语言序列化。

### 案例 3：`LIKE '%降噪耳机%'`

- **排查**：前导 % → 无确定起点，B-tree 无能为力 → Seq。
- **修复**：

```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX idx_products_name_trgm ON products USING gin (name gin_trgm_ops);
```

- **验证**：Bitmap Index Scan，0.46ms → 0.17ms（表只有 5 千行差距温和；表越大差距越大，因为 Seq 线性涨、GIN 几乎不涨）。
- **边界**：关键词 <3 个字符时 trigram 匹配不到完整三元组，退化；中英文混合按字符处理。极短词考虑前缀搜索或专用分词方案。

### 案例 4：深分页 `OFFSET 900000`

- **排查**：计划 Index Scan Backward + Limit——索引用上了，但 **必须先产出 900020 行再丢掉前 90 万行**，Buffers ≈ 90 万页，~397ms。OFFSET 的语义就是「跳过」，页码越深越慢。
- **修复（keyset / 游标分页）**：

```sql
-- 第一页
SELECT id, user_id, total, created_at FROM orders ORDER BY created_at DESC LIMIT 20;
-- 下一页：游标 = 上一页最后一行的 created_at（应用层记住它，不要子查询重算）
SELECT id, user_id, total FROM orders
WHERE created_at < '2026-09-12 12:00:00+08'   -- 上一页最后一行的值
ORDER BY created_at DESC LIMIT 20;
```

- **验证**：Index Cond 直接定位游标，**~0.1ms，且与页码深度无关**。（lab 里用子查询取锚点是为了演示，真实系统游标来自上一页结果。）
- **取舍**：keyset 不能跳页（只能上一页/下一页），后台翻页场景一般可接受；要跳页就限定最大页深或用搜索条件替代。

### 案例 5：`ORDER BY user_id DESC, created_at ASC`

- **排查**：`(user_id, created_at)` 索引正走 = (ASC, ASC)，倒走 = (DESC, DESC)——**整体正/倒只能同时翻**，与目标 (DESC, ASC) 方向不匹配 → 出现 **Incremental Sort**（PG 13+ 的补救：先按已有序的 user_id 分组，组内再排 created_at）。
- **修复（方向精确匹配的索引）**：

```sql
CREATE INDEX idx_orders_user_desc_created ON orders (user_id DESC, created_at);
```

- **验证**：纯 Index Scan，排序节点彻底消失。反之 `ORDER BY user_id DESC, created_at DESC` 直接用原索引倒走即可——**两种排序方向需求需要两个索引**。

---

## 五步法对号入座

| 案例 | ① 找到 | ② 看计划 | ③ 定位瓶颈 | ④ 手段 | ⑤ 验证 |
|---|---|---|---|---|---|
| 1 报表 | pg_stat_statements | Index Only + Filter | 函数包裹致 Filter 全扫 | 改写为范围 | 81ms→0.16ms |
| 2 接口 | 慢日志 | Filter `::text` | 类型转换致索引失效 | 参数类型修正 | 47ms→0.09ms |
| 3 搜索 | pg_stat_statements | Seq Scan | 前导 % | pg_trgm GIN | Seq→Bitmap |
| 4 深翻页 | 用户反馈 | Limit+Index 但 buffers 巨大 | OFFSET 丢行 | keyset 改写 | 397ms→0.1ms |
| 5 导出 | 慢日志 | Incremental Sort | 排序方向不匹配 | 方向匹配索引 | 排序消失 |
