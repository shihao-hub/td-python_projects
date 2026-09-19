# 阶段 4：索引原理 + EXPLAIN + 慢查询排查实战（本项目重点）

> 目标：学完本阶段，你要能独立完成这条闭环——**"接口变慢了" → 定位到具体 SQL → EXPLAIN 读出瓶颈 → 加索引/改写 → 验证提速**。
> 方式：先通读本讲义建立框架，再逐个做 `labs/` 里的 7 个实验（每个实验都在 100 万真实订单上跑，前后对比肉眼可见），卡住了看 `solutions.md`。
> 环境：`psql -U postgres -d learn_pg`，进去先 `\timing on`。

## 0. 全局图景：一条 SELECT 是怎么被执行的

```
SQL 文本 → 解析（语法/语义） → 生成执行计划（planner，基于统计信息估算成本） → 执行器按计划跑
```

关键认知：**planner 是基于"估算"选路的，不是精确计算**。它靠统计信息（每张表大约多少行、每列的值的分布）估算每种计划的成本，选最便宜的。所以：

- 统计信息过期 → 估算离谱 → 选错计划（该走索引却全表扫）——第 12 节；
- 你以为会走索引但选择率太低（命中的行太多）→ planner 正确地放弃索引——第 4 节；
- 没有索引时，查一行也只能 **Seq Scan（全表顺序扫描）**——表越大越惨，这就是要建索引的原因。

## 1. B-tree 索引直观原理

**把"无序的堆表"想象成一堆乱放的档案，B-tree 索引就是一本按关键字排好序的目录。**

- 索引不存整行，只存 **(索引列的值 → 指向行的指针)**，且按值**有序**排列；
- 有序 → 二分查找 → 100 万行只需 3~4 次页跳转（树高 log 级），再回表取整行；
- 数据按"页"（8KB）组织，B-tree 是一棵矮胖的多叉树：根 → 少量中间层 → 叶子层存全部排序数据。**矮**意味着每查一行只碰 3~4 个页。

B-tree 能加速的查询形态（记住这个"形状"）：

| 查询形状 | 例子 | 能否用 B-tree |
|---|---|---|
| 等值 | `col = 42` | ✅ 直接定位 |
| 范围 | `col BETWEEN a AND b`、`col > a` | ✅ 定位到起点顺序扫 |
| 前缀 | `col LIKE 'abc%'` | ⚠️ 需 C 排序规则或 `text_pattern_ops`（lab05） |
| 排序 | `ORDER BY col` | ✅ 索引本身有序，免排序（lab02） |
| 中缀/后缀 | `col LIKE '%abc'` | ❌（lab07 案例3 用 trgm 救） |
| 列被函数包裹 | `lower(col) = 'x'`、`col::date = d` | ❌ 对普通索引（lab05/lab07） |
| 包含（jsonb/数组） | `tags @> '["x"]'` | ❌ B-tree 不支持，用 GIN（lab06） |

其他索引类型（知道存在、什么时候想起它即可）：

| 类型 | 适用 | 本项目实例 |
|---|---|---|
| **B-tree** | 默认，等值/范围/排序，90% 场景 | 大部分 lab |
| **GIN** | jsonb `@>`/`?`、数组、全文检索 | lab06 |
| **BRIN** | 超大表 + 物理顺序 correlated 的列（如追加写的时间戳） | 了解即可 |
| **Hash** | 只有等值（PG10+ 持久化），少见 | — |
| **GiST / SP-GiST** | 地理、范围类型、最近邻 | — |

## 2. EXPLAIN 逐项解读

```sql
EXPLAIN (ANALYZE, BUFFERS) SELECT id, total FROM orders WHERE total BETWEEN 31000 AND 31010;
```

- `EXPLAIN`：只显示**计划**（不执行）。看"打算怎么跑"。
- `EXPLAIN ANALYZE`：**真正执行**并显示每步实际耗时/行数。看"实际怎么跑了"。（注意：UPDATE/DELETE 会被真的执行！用 BEGIN...ROLLBACK 包住）
- `BUFFERS`：显示每步读多少内存页/磁盘页——**定位 I/O 的关键**，建议永远带上。

计划是棵树，**缩进越深越先执行**，从最深的节点往上读。每一行：

```
Bitmap Heap Scan on orders  (cost=6.60..785.46 rows=212 width=16) (actual time=0.126..0.357 rows=192.00 loops=1)
└─ cost=启动..总代价     planner 的估算成本（无量纲，只用于比较，无绝对意义）
└─ rows=212             planner 估算返回行数 ← 和 actual rows=192 差太多 = 统计信息有问题
└─ width=16             平均每行字节数
└─ actual time=0.126..0.357   该节点（含子节点）实际耗时：启动..完成，单位 ms
└─ loops=1              该节点执行次数（actual time/rows 是**单次循环**的平均值，总耗时 = time × loops）
```

`Planning Time` / `Execution Time` 在最底部。**先看 Execution Time 里的耗时大头在哪个节点**，逐层往下钻。

## 3. 四种扫描节点（EXPLAIN 里最常见的四兄弟）

以下数字基于本项目 100 万行 orders（实测，供建立量级感）：

### Seq Scan（顺序全表扫描）

```
Seq Scan on orders (actual time=17.648..96.376 rows=72408)
  Filter: (total > 35000)
  Rows Removed by Filter: 927592        ← 全表 100 万行逐行判断，92.7 万行被丢弃
```

一行行读完整张表。小表、或**命中比例高**（经验值 >5%~10%）时它反而是正确选择——扫全表比"走索引取回大半张表"便宜。**看到 Seq Scan 不等于有问题**，要看它扫了多少、过滤掉了多少。

### Index Scan（索引扫描）

```
Index Scan using idx_orders_user_status_created on orders (actual time=0.147..0.158 rows=10)
  Index Cond: ((user_id = 42) AND (status = 'completed'))   ← 索引真正"定位"用的条件
```

按索引找到行位置 → **回表**取整行。适合命中少量行。`Index Cond` 是索引条件，`Filter` 是拿到行后再过滤的条件——**出现在 Filter 里的条件没有被索引利用**。

### Bitmap (Index/Heap) Scan（位图扫描）

```
Bitmap Heap Scan on orders (actual rows=72408)
  -> Bitmap Index Scan on idx_orders_total (actual rows=72408)
```

先通过索引把所有匹配行的位置做成位图，再按**堆的物理顺序**批量取行。介于两者之间：命中几百~几万行时常用。`Recheck Cond`：位图太大放不进内存时退化为"取回后重新检查条件"（Lossy）。

### Index Only Scan（纯索引扫描，最快）

```
Index Only Scan using idx_orders_user_cover on orders (actual rows=1020)
  Heap Fetches: 0        ← 0 = 完全没回表
```

要查的列全在索引里 → 不回表（第 6 节覆盖索引）。`Heap Fetches` 不为 0 说明部分行还要回表确认可见性（与 VACUUM 状态有关）。

### 经验选择率分界（1M 行表量级）

| 命中行数占比 | 大概率的计划 |
|---|---|
| < 0.1% | Index Scan |
| 0.1% ~ 5% | Bitmap Scan |
| > 5~10% | Seq Scan（索引不划算） |

这不是规则是直觉——planner 用成本模型算，**数据分布变了选择就会变**。

## 4. 三种 Join 节点

| 节点 | 机理 | 适合 |
|---|---|---|
| Nested Loop | 外层每行去内层查一次（内层有索引就快） | 外层结果小 + 内层有索引 |
| Hash Join | 内层建哈希表，外层逐行探测 | 中大结果集、无可用索引、等值连接 |
| Merge Join | 两边都按连接键排好序，归并 | 大结果集 + 已有序（各走各的索引） |

阶段 1/2 的 JOIN 练习里 planner 自动选了合适的。日常排查记住一条：**Hash Join 需要 memory/disk 排序缓冲，写大了会在计划里看到 `Batches`，超过内存 spill 到磁盘是慢查询常见元凶**。

## 5. 联合索引与最左匹配（lab02 实战）

```sql
CREATE INDEX ON orders (user_id, status, created_at);
```

B-tree 按 **(user_id, status, created_at) 三元组**排序：先按 user_id 排，user_id 相同再按 status，再按 created_at。

**最左匹配**：查询条件必须从索引**最左列开始连续命中**才能用索引"定位"：

| WHERE 条件 | 能否用 (user_id, status, created_at) |
|---|---|
| `user_id = ?` | ✅ 前缀 |
| `user_id = ? AND status = ?` | ✅ 两列连续 |
| `user_id = ? AND created_at > ?` | ⚠️ 只有 user_id 用于定位；中间断了 status，created_at 不能继续定位（但仍可当过滤条件/避免不了排序，lab02-C 实测） |
| `status = ?` | ❌ 不从最左开始 → 全表扫 |
| `status = ? AND created_at > ?` | ❌ 同上 |

**列顺序三原则**（设计联合索引的默认思路）：

1. **等值条件列在前，范围/排序列在后**（范围一断，后面的列就用不上索引定位了）；
2. **选择性高的在前**（不同值多的列放前面收敛快）；
3. **让最常用的查询都能命中最左前缀**——一个 (A,B) 索引服务于 `A` 和 `A+B` 两类查询，等于买了 1.5 个索引。

**ORDER BY 也能吃索引**：`WHERE user_id=? AND status=? ORDER BY created_at DESC LIMIT 10` 配 (user_id, status, created_at) —— 索引里数据本来就是这个顺序，**Sort 节点消失**，LIMIT 提前终止，这是"翻页快"的本质（lab02 实测 135ms → 0.18ms）。

## 6. 覆盖索引与 Index Only Scan（lab03 实战）

```sql
-- 查询只要 created_at 和 total，但索引只有 user_id → 每行都要回表
SELECT created_at, total FROM orders WHERE user_id = 42;
-- 把需要的列 INCLUDE 进索引 → 不回表
CREATE INDEX ON orders (user_id) INCLUDE (created_at, total);
```

- `INCLUDE` 列**只存放、不参与排序**——键列保持精简（排序用），载荷列挂在叶子节点（取数据用）；
- 好处：`Heap Fetches: 0`，I/O 从 ~960 页降到 ~10 页（lab03 实测）；
- 代价：索引变大、写放大。适合"高频、固定列组合"的查询（比如列表页只展示某几列）。

## 7. 部分索引 / 表达式索引 / 唯一索引

### 部分索引（Partial Index）——只索引"关心的那部分行"

```sql
-- 只有 2% 的订单是 pending，而系统只对 pending 做"超时提醒"查询
CREATE INDEX ON orders (created_at) WHERE status = 'pending';
```

- 索引只含 2% 的行：**体积小（456KB vs 全列 36MB，实测 80 倍差）、写入维护少、查询一样快**；
- 前提：查询条件必须**蕴含**索引的 WHERE（`WHERE status='pending' AND ...` 才能用）。

### 表达式索引（Functional Index）——索引"计算结果"

```sql
CREATE INDEX ON users (lower(email));
-- 现在 WHERE lower(email) = 'x' 能走索引（lab05 实测 9.8ms → 0.065ms）
```

规则：**查询里对列做了什么运算，索引就得建在同样的运算上**。`WHERE col::date = d` 用不到 (col) 的索引，是这个规则最常咬人的形态（lab07 案例1/2）。

### LIKE 前缀与排序规则

非 C 排序规则下，`LIKE 'abc%'` 不能直接用普通 B-tree（排序顺序不保证与字符比较一致），两个出路：

```sql
CREATE INDEX ON users (email text_pattern_ops);  -- 出路1：专用于模式匹配的 opclass
CREATE EXTENSION pg_trgm;                        -- 出路2：trigram，连 '%abc%' 都能加速（lab07 案例3）
CREATE INDEX ON products USING gin (name gin_trgm_ops);
```

### 唯一索引

```sql
CREATE UNIQUE INDEX ON orders (user_id, created_at);  -- 防重：同一用户同一秒不能下两单
```

唯一约束（`UNIQUE`）底层就是唯一索引；UPSERT 的 `ON CONFLICT (列)` 依赖它。**用唯一索引表达业务不变量**（一人一坑），比应用层判重可靠。

## 8. jsonb 与 GIN（lab06 实战）

```sql
CREATE INDEX ON products USING gin (tags);            -- 默认 jsonb_ops：支持 @> ? ?| ?&
CREATE INDEX ON products USING gin (tags jsonb_path_ops);  -- 只支持 @>，更小更快
```

`@>`（包含）是**唯一能被 GIN 加速的 jsonb 包含操作符**；`->>` 取值后做比较（`tags->>0 = 'x'`）用不上索引。设计 jsonb 查询时优先写成 `@>` 形态。

## 9. 索引失效场景清单（速查）

| 场景 | 例子 | 解法 |
|---|---|---|
| 函数包裹列 | `lower(email)='x'`、`created_at::date=d` | 表达式索引 / 改写为范围 |
| 类型转换 | `user_id::text='42'` | 参数用对类型（框架层排查） |
| 前导通配符 | `LIKE '%x%'` | pg_trgm GIN / 改搜索设计 |
| 违反最左匹配 | (a,b) 索引只查 b | 调整列顺序或补索引 |
| 范围列断档 | (a,b,c) 查 a+c | 范围列放最后/拆索引 |
| OR 跨列 | `a=1 OR b=2` 各自无索引 | 每列建索引（Bitmap OR）或改 UNION |
| 隐式类型不匹配 | 参数传 varchar 给 int 列（部分驱动场景） | 统一类型 |
| 选择率太低 | 命中 30% 行 | 索引本来就不该用，考虑改业务 |
| 统计信息过期 | 刚灌完大量数据没 ANALYZE | `ANALYZE 表名;` |
| 排序方向不匹配 | `ORDER BY a DESC, b ASC` 配 (a,b) | 建同方向索引 (a DESC, b) |

## 10. 索引的代价（不是越多越好）

- **写入变慢**：每个索引 = 每次 INSERT/UPDATE 都要同步维护（本项目实测：100 万行 orders 建一个索引约 1~2 秒，意味着每次写入多一份额外维护）；
- **占空间**：36MB 的 (status, created_at) vs 456KB 的部分索引——设计差 80 倍（lab04 实测）；
- **拖累更新**：UPDATE 改到索引列就要动索引；HOT update 优化会因索引列被改而失效；
- **没人用的索引是纯负债**，定期清理：

```sql
-- 找出从未被使用的索引（idx_scan = 0）
SELECT relname AS table, indexrelname AS index, idx_scan, pg_size_pretty(pg_relation_size(indexrelid)) AS size
FROM pg_stat_user_indexes
WHERE idx_scan = 0 AND indexrelname NOT LIKE '%_pkey'   -- 排除主键
ORDER BY pg_relation_size(indexrelid) DESC;
```

## 11. 统计信息：planner 的决策依据

```sql
-- planner 眼中每张表有多少行（估算）
SELECT relname, reltuples::bigint AS est_rows FROM pg_class WHERE relname='orders';

-- 某列的统计细节：null 比例、不同值个数、最常见值及频率、直方图边界
SELECT * FROM pg_stats WHERE tablename='orders' AND attname='status';

-- 亲手制造一次"统计过期"：批量写入后不 ANALYZE，估算行数会严重偏离
ANALYZE orders;   -- 手动刷新（autovacuum 平时自动做，大批量写后建议手动）
```

症状对照：**估算 rows 和 actual rows 差一个数量级以上** → 统计信息问题 → `ANALYZE` 后再看。VIP 用户倾斜分布（前 100 人人均 1000 单）就是 pg_stats 里 `most_common_vals` 能看到的。

## 12. 慢查询排查五步法（本阶段的总纲）

```
① 找到慢 SQL     pg_stat_statements 按 total_exec_time 排序 / 慢日志
② 看计划         EXPLAIN (ANALYZE, BUFFERS) 拿到真实执行计划
③ 定位瓶颈       计划里耗时最大的节点，对号入座：
                  - Seq Scan 扫全表 + Rows Removed 巨大 → 缺索引
                  - Filter（而非 Index Cond）里出现本该走索引的条件 → 索引失效形态
                  - Sort/Batches 超大 → 排序 spill、缺排序列索引
                  - 估算行数 vs 实际行数差 10 倍+ → 统计信息过期
④ 动手           加索引（套第 5~8 节的模板）或改写 SQL（范围化、keyset、去函数包裹）
⑤ 验证           重跑 EXPLAIN (ANALYZE)：计划变了 + Execution Time 降了 + 业务结果一致
```

### ① pg_stat_statements：找到真凶

PG 的"SQL 体检报告"，记录每条 SQL 的调用次数、总耗时、平均耗时、返回行数。**默认没开启**，开启步骤（一次性）：

```sql
SHOW config_file;        -- 找到配置文件路径
-- 编辑 postgresql.conf，把该行改成（没有就加）：
--   shared_preload_libraries = 'pg_stat_statements'
-- 然后重启服务（Windows: 服务管理器重启 postgresql-x64-18，或管理员 net stop/start）
```

重启后：

```sql
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;   -- 在 learn_pg 里装一次

-- Top 10 最耗时的 SQL（按总耗时）
SELECT calls,
       round(total_exec_time::numeric, 1) AS total_ms,
       round(mean_exec_time::numeric, 2)  AS avg_ms,
       rows,
       left(query, 80) AS query
FROM pg_stat_statements
WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
ORDER BY total_exec_time DESC
LIMIT 10;

-- 也重点看：mean_exec_time 高 + calls 高 的（高频慢查询最致命）
-- 重置统计（做完一轮优化后重新收集）
SELECT pg_stat_statements_reset();
```

不想重启服务的替代方案：开慢日志 `SET log_min_duration_statement = 500;`（记录超过 500ms 的 SQL 到日志，`SHOW log_directory;` 找日志）。

### 排查时的好习惯

- 改前**先留存当前计划**（复制到笔记），改后对比才有说服力；
- 跑 `EXPLAIN ANALYZE` 前 TP 场景先热身跑 2 次取第二次（排除冷缓存影响）；
- 加索引用 `CREATE INDEX CONCURRENTLY`（不锁写，生产必用；学习库直接 CREATE 也行）。

## 13. 表设计原则（索引的内功）

1. **主键**：单机/常规业务用 `bigint GENERATED BY DEFAULT AS IDENTITY`（本项目 schema 就是这样）。分布式/离线生成场景才用 UUID（注意：随机 UUID 做 B-tree 主键写入时页分裂剧烈，PG14+ 可用 `uuidv7()` 或有序方案）。
2. **能 NOT NULL 就 NOT NULL**：NULL 让索引、统计、`NOT IN`、聚合都多一份心智负担。没有值就用默认值或空串/0 表达。
3. **外键要建，但它不带索引**：PG 的 FK 只保证一致性。**凡是"按外键查"的路径（order_items.order_id、orders.user_id）都要单独建索引**——本项目刻意没建，lab02/阶段 5 的 N+1 会让你亲身感受代价。
4. **钱的列用 numeric(p, s)**，永远别用 float（阶段 3 已验证 `0.1+0.2` 问题）。
5. **状态等有限枚举**：`text + CHECK`（加新值零成本）或原生 enum（改值麻烦），本项目选前者。
6. **范式与反范式的取舍**：
   - 范式（不冗余）：`orders.total` 本可实时 `SUM(order_items)` 算出来，但每次列表页都 Join 200 万明细太贵 → **冗余一个 total 字段，写入时维护**（seed 脚本第 5 步就是维护动作）。这是教科书级的反范式取舍：**读多写少的汇总值，冗余换性能**；
   - 反过来的坑：冗余字段一旦漏维护就是数据事故——冗余要有明确的单一写入点。
7. **索引跟着查询走，不是跟着表走**：先有高频查询，再有索引设计。alt: 每个 `WHERE/ORDER BY/JOIN ON` 组合都问一遍"值得配索引吗"。
8. **时间用 timestamptz**（阶段 3 讲过）。
9. 建表时把 `created_at/updated_at` 默认值写上（`DEFAULT now()`），别依赖应用层记得传。

## 14. 动手实验（labs/）

| Lab | 主题 | 一句话目标 |
|---|---|---|
| lab01 | EXPLAIN 基础 | 会读 cost/rows/buffers，亲眼见 Seq→Bitmap 的切换 |
| lab02 | 联合索引 | 最左匹配、列顺序、ORDER BY 消 Sort（135ms → 0.2ms） |
| lab03 | 覆盖索引 | Index Only Scan、Heap Fetches 归零 |
| lab04 | 部分索引 | 2% 的 pending：456KB 干掉 36MB |
| lab05 | 表达式索引 | lower(email)、LIKE 前缀与 text_pattern_ops |
| lab06 | jsonb + GIN | @> 走 GIN，->> 不走 |
| lab07 | 慢查询案例 | 5 个真实病例，只给现象自己排查（答案在 solutions.md） |

每个 lab 结构相同：**慢查询现场 → EXPLAIN ANALYZE 读计划 → 自己动手（建索引/改写）→ 再跑验证 → 思考题**。lab 结束会把实验索引 DROP 掉，保持"无二级索引"的基准状态（想保留就注释掉 DROP 再跑一遍）。

## 15. 自测清单

- [ ] planner 依据什么选计划？统计信息过期会导致什么？
- [ ] B-tree 加速哪四类查询形态？哪三类不行？
- [ ] Seq/Index/Bitmap/Index Only 各自机理一句话 + 大致的选择率分界？
- [ ] EXPLAIN 输出里 rows（估算）和 actual rows（实际）差 10 倍说明什么？
- [ ] Index Cond 和 Filter 的区别？哪个说明条件没吃上索引？
- [ ] 联合索引列顺序三原则？`WHERE a=? AND c>?` 配 (a,b,c) 索引会发生什么？
- [ ] 覆盖索引为什么快？Heap Fetches 是什么？INCLUDE 列和键列的区别？
- [ ] 部分索引适合什么场景？什么前提下查询用不上它？
- [ ] `WHERE created_at::date = ...` 为什么不走索引？两种修法？
- [ ] `LIKE '%x%'` 怎么救？
- [ ] 加索引的三条代价？怎么找出没人用的索引？
- [ ] 慢查询五步法背出来。pg_stat_statements 怎么开、怎么查 Top SQL？
- [ ] orders.total 为什么可以冗余？反范式的风险和纪律是什么？
