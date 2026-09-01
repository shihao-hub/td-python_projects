from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from demo_api import models  # noqa: F401  确保模型注册进 metadata
from demo_api.config import settings
from demo_api.db import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 连接串从 .env 读，不落进 alembic.ini（避免密码入库）；%% 是 configparser 插值转义
# 迁移走同步驱动：应用用 psycopg_async，alembic 换回 psycopg
sync_url = settings.database_url.replace("+psycopg_async", "+psycopg")
config.set_main_option("sqlalchemy.url", sync_url.replace("%", "%%"))

target_metadata = Base.metadata


def include_name(name, type_, parent_names):
    """共享库：只比较 DB_SCHEMA 下的对象，autogenerate 绝不碰其他 schema"""
    if type_ == "schema":
        return name == settings.db_schema
    return True


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        include_name=include_name,
        version_table_schema=settings.db_schema,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=True,
            include_name=include_name,
            version_table_schema=settings.db_schema,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
