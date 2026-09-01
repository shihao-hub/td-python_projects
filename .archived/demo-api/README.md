# demo-api

超基础 FastAPI 后端：uv + structlog + SQLAlchemy（async）+ PostgreSQL（psycopg_async 驱动）。

## 数据库

连接串在 `.env` 配置（参考 `.env.example`）；表建在 `DB_SCHEMA` 指定的 schema（默认 `test`），表名统一带 `demo_` 前缀。密码含特殊字符时需 URL 编码（`@`→`%40` 等）。

## 迁移（Alembic）

```bash
uv run alembic revision --autogenerate -m "描述"   # 改了 models.py 后生成迁移
uv run alembic upgrade head                          # 应用
uv run alembic downgrade base                        # 回滚全部
uv run alembic current                               # 查看当前版本
```

连接串从 `.env` 读取，autogenerate 只比较 `DB_SCHEMA` 下的对象，不会碰共享库其他 schema。

## 启动服务

```bash
cp .env.example .env   # 按需修改
uv run uvicorn demo_api.main:app --reload
```

接口文档：http://127.0.0.1:8000/docs

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | /health | 健康检查（含 DB 连通性） |
| POST | /items | 创建 |
| GET | /items | 列表 |
| GET | /items/{id} | 详情 |
| PATCH | /items/{id} | 部分更新 |
| DELETE | /items/{id} | 删除 |
