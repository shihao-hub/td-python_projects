# django-lab — 用最新 Django 写的「图书馆」，一个项目读懂 Django 设计思想

> 从 FastAPI 切换过来的练习场：Django **6.1.1**（2026-08 发布）+ DRF **3.18**，SQLite 开箱即用，所有页面 SSR + 一套 DRF API，19 个测试全绿。

## 快速开始

```powershell
uv sync                      # 装依赖（默认含 dev 组）
uv run manage.py migrate     # 建表（内置迁移系统）
uv run manage.py seed_demo   # 演示数据 + 账号（幂等）
uv run manage.py runserver   # http://127.0.0.1:8000
```

| 入口 | 地址 | 说明 |
|---|---|---|
| 图书列表 | `/books/` | 泛型类视图 + 分页 + 搜索 |
| 运营看板 | `/dashboard/` | **async 视图 + async ORM** |
| 我的借阅 | `/my/loans/` | 未登录会被 `LoginRequiredMiddleware` 拦到登录页 |
| Admin 后台 | `/admin/` | admin / admin1234 |
| 浏览式 API | `/api/` | reader / reader1234（普通读者） |

登录页也印了这两个演示账号。改模型后跑 `uv run manage.py makemigrations` 生成迁移 —— schema 即版本历史。

## Django 设计思想 → 代码对照

| 设计思想 | 在这个项目里 |
|---|---|
| **batteries included**（自带电池） | auth / admin / forms / messages / signals / migrations / test client / `django.tasks`（6.0 新电池）没有一个来自第三方 |
| **模型即唯一事实** | `catalog/models.py`：一份定义同时驱动数据库 schema、表单字段（`BorrowForm`）、admin 列、API 序列化 |
| **fat models** | `Book.is_borrowable`、`BorrowRecord.mark_returned()` —— 业务逻辑长在模型上，页面 / API / admin 三端复用 |
| **MTV 而非 MVC** | Model(models.py) + Template(templates/) + View(views.py)；URL 层只是显式路由表 |
| **少写代码（泛型视图）** | `ListView`/`DetailView` 声明几行属性得到完整页面；`{{ form.as_div }}` 一行渲染整个表单 |
| **DRY** | `get_absolute_url()` 定义一次，`redirect(book)` / admin「查看站点」/ API `page_url` 全复用；卡片 partial 四处 include |
| **默认安全** | CSRF / XSS 转义 / Clickjacking / 密码校验器默认开启；5.1+ `LoginRequiredMiddleware` 全站默认拒绝，公开页用 `@login_not_required` 显式豁免 |
| **松耦合 apps** | `catalog`（业务）与 `api`（接口）互不 import 对方代码，只共享模型与表单；想拆掉 api 一个目录就够 |
| **并发兜底下沉到数据库** | 「同一本书同时只能有一条在借」是**部分唯一约束**，不依赖应用层校验 |
| **信号解耦** | `catalog/signals.py`：任何入口创建/归还借阅记录，图书状态自动同步 |
| **约定优于配置** | `registration/login.html` 不配置就被 `LoginView` 找到；`apps.py` 的 `ready()` 自动注册信号 |

## 用到的 Django 新特性

| 版本 | 特性 | 位置 |
|---|---|---|
| 6.1 | `on_delete=DB_CASCADE` 数据库级联（含引用链统一性约束、M2M 显式 through） | `catalog/models.py` |
| 6.1 | `fetch_mode(FETCH_PEERS)` 按需补字段，两条 SQL 解决 N+1 | `catalog/views.py` AuthorDetailView |
| 6.0 | 内置后台任务 `django.tasks`（`@task` + `.enqueue()`，可插拔后端） | `catalog/tasks.py`、归还图书时触发 |
| 6.0 | 表单默认 `<div>` 渲染（`{{ form.as_div }}`） | `login.html`、`book_detail.html` |
| 5.1 | `LoginRequiredMiddleware` + `login_not_required` 默认拒绝 | `config/settings.py`、`catalog/mixins.py` |
| 5.0 | `GeneratedField` 数据库生成列（含税价 = 定价 × 1.09） | `catalog/models.py` |
| 5.0 | `db_default=Now()` 数据库计算默认值（入库/借出时间） | `catalog/models.py` |
| 5.0 | `TextChoices` 函数式枚举（本项目用类式以便类型检查，两者皆可） | `catalog/models.py` |
| 4.1+ | async 视图 + async ORM（`acount`、`async for`） | `catalog/views.py` dashboard |

## 第三方库（全部最新）

| 库 | 用在哪 | 为什么 |
|---|---|---|
| djangorestframework 3.18 | `api/` | Serializer≈ModelForm、ViewSet≈泛型视图、Router 自动注册 —— Django 思想的 API 延伸；浏览式 API 相当于 API 版 admin |
| pydantic-settings 2.15 | `config/settings.py` | 类型化读取 `DJANGO_` 前缀环境变量 / `.env`，从 FastAPI 平滑过渡 |
| whitenoise 6.12 | settings.MIDDLEWARE | 生产静态文件零配置托管（DEBUG=False 时自动切 Manifest 存储） |
| django-extensions 4.1 | INSTALLED_APPS | `shell_plus`（自动 import 全部模型）、`show_urls` 等增强命令 |
| dj-database-url 3.1 | settings.DATABASES | 一条 URL 切换数据库（配 `psycopg` 3 可选组） |
| django-debug-toolbar 8.0 | dev 组 | 开发时 SQL/签名面板（DEBUG=True 自动挂载） |
| django-stubs 6.1 + mypy 2.3 | dev 组 | `mypy catalog config api` 全绿 |
| ruff 0.16 | dev 组 | lint + format，规则见 pyproject.toml |
| psycopg 3.3（可选组） | `uv sync --group postgres` | `DJANGO_DATABASE_URL=postgres://...` 即切 PostgreSQL |

## 目录结构

```
django-lab/
├─ config/                 # 项目配置（settings / urls / asgi / wsgi）
├─ catalog/                # 业务应用：一个目录装下 M+T+V
│  ├─ models.py            #   模型 + 查询集 + 约束 + 生成列（核心）
│  ├─ forms.py             #   ModelForm：一处校验，页面与 API 共用
│  ├─ views.py             #   泛型类视图 / 函数视图 / async 视图三种风格
│  ├─ admin.py             #   声明式后台配置
│  ├─ signals.py / tasks.py / mixins.py / context_processors.py
│  ├─ management/commands/seed_demo.py
│  └─ migrations/          #   schema 版本历史（由模型自动生成）
├─ api/                    # DRF 应用：serializers / viewsets / urls(router)
├─ templates/              # base.html 继承体系 + registration/ 约定目录
├─ static/css/style.css
├─ catalog/tests.py + api/tests.py   # 19 个测试
└─ pyproject.toml          # uv 依赖组 + ruff/mypy/django-stubs 配置
```

## 写给 FastAPI 老用户的直觉对照

| FastAPI | Django | 备注 |
|---|---|---|
| 装饰器逐个注册路由 | `urls.py` 显式路由表 + DRF Router 批量生成 | 集中可读，反查 `reverse()` 让 URL 单点定义 |
| Pydantic 声明 + 校验 | Model 定义 + ModelForm/Serializer | 校验靠近数据源，模板/admin 直连 |
| SQLAlchemy + Alembic | ORM + 迁移一体 | `makemigrations` 对比模型自动生成 |
| 自己拼 admin / docs | Admin / 浏览式 API 白送 | 站在电池上干活 |
| 依赖注入做横切 | 中间件 + Mixin 组合 | CBV 用 Mixin 拼装能力 |
| async 天下第一 | 同步为主，async 按需 | dashboard 就是 async；生态多数仍是同步 |

## 常用命令

```powershell
uv run manage.py test                          # 测试（自带 test client，不起服务）
uv run manage.py shell_plus                    # django-extensions 增强交互
uv run manage.py show_urls                     # 查看全部路由
uv run ruff check . ; uv run mypy catalog config api
uv run manage.py createsuperuser               # 自建管理员（seed_demo 已建 admin）
```
