# 阶段 3：PG 命令与特性

> 目标：① psql 元命令速查（"pg 命令"的核心诉求）；② PG 类型系统的选型判断力；③ UPSERT / RETURNING / jsonb 这些 PG 特色武器。
> 环境：`psql -U postgres -d learn_pg`。

## 1. psql 元命令速查（\ 开头，不是 SQL，分号可省）

### 每天都用的

| 命令 | 作用 |
|---|---|
| `\l` | 列出所有数据库 |
| `\c learn_pg` | 切换数据库 |
| `\dt` / `\dt+` | 列出当前库的表（+ 带行数估计和大小） |
| `\d orders` | 看表结构：列、类型、索引、约束——**用得最多** |
| `\d+ orders` | 更详细（压缩、统计信息等） |
| `\di` / `\di+` | 索引列表 |
| `\x` / `\x on` | 行转列显示（宽结果可读性质变），`\x auto` 自动判断 |
| `\timing on` | 显示每条 SQL 实际耗时——**学习期间建议常开**（本课程后面全靠它） |
| `\i d:/path/file.sql` | 执行脚本文件（注意 Windows 下用正斜杠） |
| `\q` | 退出 |

### 导入导出（\copy，客户端侧，不用服务器权限）

```sql
-- 导出 CSV
\copy (SELECT * FROM orders WHERE status='pending') TO 'pending.csv' WITH (FORMAT csv, HEADER true)
-- 导入 CSV
\copy products FROM 'products.csv' WITH (FORMAT csv, HEADER true)
```

### 调试与效率

| 命令 | 作用 |
|---|---|
| `\e` | 用编辑器编辑上一条 SQL（写长 SQL 不用憋在命令行） |
| `\ef 函数名` | 编辑函数定义 |
| `\watch 5` | 每 5 秒重复执行上一条 SQL（观察数值变化） |
| `\pset null '∅'` | 把 NULL 显示成可见符号，避免和空字符串混淆 |
| `\?` | 元命令帮助 |
| `\h UPDATE` | SQL 语法帮助（\h 后跟 SQL 关键字） |
| `\du` / `\dn` / `\df` | 列出用户 / schema / 函数 |
| `Ctrl+R` | 搜索命令历史；`Tab` 补全表名列名 |

## 2. 类型系统：选型判断力

### 数字

| 类型 | 用途 | 说明 |
|---|---|---|
| `smallint` / `integer` / `bigint` | 2B / 4B / 8B 整数 | id、计数、状态值 |
| `numeric(p,s)` / `decimal` | **精确**十进制 | **存钱必用**（本项目 price/total/unit_price 全是它） |
| `real` / `double precision` | 二进制浮点 | 科学计算、近似值才用 |
| `serial` / `GENERATED ... AS IDENTITY` | 自增主键 | 新项目用 IDENTITY（SQL 标准，schema 见 setup/02） |

浮点为什么不能存钱（亲手跑）：

```sql
SELECT 0.1::float8 + 0.2::float8 = 0.3;   -- false！二进制表示 0.1 是无限循环
SELECT 0.1::numeric + 0.2::numeric = 0.3; -- true
```

### 时间（后端最容易踩的坑）

| 类型 | 说明 |
|---|---|
| `timestamptz`（timestamp with time zone） | **推荐默认**。存的是 UTC 绝对时刻，显示时按会话时区转换 |
| `timestamp`（without time zone） | 无时区的"墙上时钟"，跨时区就乱 |
| `date` / `time` / `interval` | 日期 / 时间 / 时间段 |

```sql
SHOW timezone;                              -- 当前会话时区
SELECT now(), current_date;                 -- timestamptz 用 now()；current_date 是 date
SELECT now() AT TIME ZONE 'UTC';            -- 换成 UTC 视角的墙上时间
SELECT now() - interval '7 days';           -- interval 运算：7 天前
SELECT created_at + interval '30 minutes' FROM orders LIMIT 1;
```

规则：**业务表一律 timestamptz**（对应 Django `USE_TZ=True` + `DateTimeField`），只有"生日""纪念日"这种无时刻语义的才用 date。

### 文本 / 其他

- `text` 和 `varchar(n)` 在 PG 里**性能完全一样**；不加长度限制直接 `text`，要限制长度用 CHECK 约束（业务层）。`varchar(n)` 超长直接报错，改起来要锁表。
- `jsonb`：二进制 json，可索引、操作符丰富 → **永远用 jsonb 不用 json**（json 只是原样存文本）。
- `boolean`、`uuid`（`gen_random_uuid()`，分布式主键）、`text[]` 数组（一对多的轻量替代，如角色列表）。
- 类型转换：`'123'::int`、`CAST(x AS int)`、`created_at::date`。

## 3. UPSERT：INSERT ... ON CONFLICT

```sql
-- 冲突时更新：ON CONFLICT (唯一键列) 引用"想插入但冲突的那行"用 EXCLUDED
INSERT INTO categories (id, name, parent_id) VALUES (21, '测试分类', NULL)
ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name
RETURNING *;

-- 冲突时忽略（幂等导入常用）
INSERT INTO categories (name) VALUES ('测试分类')
ON CONFLICT DO NOTHING;          -- 不指定列 = 任何唯一约束冲突都忽略
```

要点：

- `ON CONFLICT (列)` 依赖该列上的**唯一约束/唯一索引**，没有就报错。
- 这是**原子操作**。Django 的 `update_or_create()` 是 SELECT + INSERT/UPDATE 两条语句，并发下会竞态（IntegrityError 或互相覆盖）；`ON CONFLICT` 是标准解法（Django 4.1+ 的 `Model.objects.bulk_create(..., update_conflicts=True)` 底层就是它）。
- `RETURNING` 让 INSERT/UPDATE/DELETE 也能"返回结果"：`.update(..., returning=...)`、删除时 `DELETE ... RETURNING id` 拿到删了谁。

## 4. jsonb 查询（products.tags 实战）

```sql
-- 取值：-> 返回 jsonb，->> 返回 text（显示用 ->>）
SELECT tags->0        AS first_tag_jsonb,   -- 第一个元素（jsonb 类型）
       tags->>0       AS first_tag_text,    -- 第一个元素（text 类型）
       tags           AS all_tags
FROM products WHERE tags IS NOT NULL LIMIT 3;

-- 包含查询（走 GIN 索引的操作符，阶段 4 lab06 详解）
SELECT count(*) FROM products WHERE tags @> '["热销"]'::jsonb;   -- 包含元素"热销"
SELECT count(*) FROM products WHERE tags ? '新品';               -- 顶层存在键/元素

-- jsonb 之间的包含关系：{"a":1,"b":2} @> {"a":1} → true
SELECT '{"a":1,"b":2}'::jsonb @> '{"a":1}';

-- 修改：|| 合并，- 删键
SELECT '{"a":1}'::jsonb || '{"b":2}'::jsonb;   -- {"a":1,"b":2}
SELECT '{"a":1,"b":2}'::jsonb - 'a';           -- {"b":2}
UPDATE products SET tags = tags || '["清仓"]'::jsonb WHERE id = 1;

-- 展开：数组变行（统计每种标签出现次数）
SELECT tag, count(*)
FROM products, jsonb_array_elements_text(tags) AS tag
WHERE tags IS NOT NULL
GROUP BY tag
ORDER BY count(*) DESC;
```

Django 对照：`django.contrib.postgres` 的 `JSONField` 查询 `.filter(tags__contains=["热销"])` 底层就是 `@>`。

## 5. 常用函数速查

```sql
-- 时间
SELECT date_trunc('month', now());              -- 截断到月初（阶段 2 题 5 用过）
SELECT extract(year FROM created_at), extract(epoch FROM now());  -- 取部分 / 转 epoch 秒
SELECT to_char(now(), 'YYYY-MM-DD HH24:MI');    -- 格式化

-- NULL 处理
SELECT coalesce(NULL, 0), coalesce(paid_at, created_at) FROM orders LIMIT 1;  -- 取第一个非 NULL
SELECT nullif('', NULL);                        -- 两值相等返回 NULL（常配合 coalesce 把空串变默认值）

-- 其他
SELECT greatest(1, 5, 3), least(1, 5, 3);
SELECT generate_series(1, 5);                   -- 生成序列（setup 灌 100 万行的核心）
SELECT array_agg(DISTINCT status) FROM orders;  -- 聚合成数组（Django 的 ArrayAgg）
SELECT string_agg(DISTINCT status, ',') FROM orders;  -- 聚合成字符串（Django 的 StringAgg）
```

## 6. 练习

`exercises.sql`（8 题，第 4、5 题会写入数据，做完清理）。

## 7. 自测清单

- [ ] `\d 表名` 能看到哪些信息？`\dt` 和 `\di` 分别看什么？
- [ ] 学习期间建议常开的两个元命令是什么？分别解决什么问题？
- [ ] 为什么钱不能用 float 存？`0.1::float8 + 0.2::float8 = 0.3` 结果？
- [ ] timestamptz 和 timestamp 的区别？业务表默认用哪个？对应 Django 哪个设置？
- [ ] text 和 varchar(n) 在 PG 里的关系？
- [ ] ON CONFLICT 依赖什么？EXCLUDED 指什么？为什么比 Django 的 update_or_create 更稳？
- [ ] `tags->>0` 和 `tags->0` 的区别？包含查询用哪个操作符？
- [ ] jsonb 数组统计每个元素出现次数怎么写？
