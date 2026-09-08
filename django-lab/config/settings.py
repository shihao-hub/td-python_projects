"""Django settings：用 pydantic-settings 做类型化配置（DJANGO_ 前缀读取环境变量 / .env）。"""

import io
import sys
from pathlib import Path

import dj_database_url
import structlog
from pydantic_settings import BaseSettings, SettingsConfigDict
from structlog.typing import Processor

BASE_DIR = Path(__file__).resolve().parent.parent

# Windows 控制台默认 GBK：含中文的日志会乱码，统一把标准流切成 UTF-8
for _stream in (sys.stdout, sys.stderr):
    if isinstance(_stream, io.TextIOWrapper):
        _stream.reconfigure(encoding="utf-8")


class Env(BaseSettings):
    """12-factor：配置来自环境变量，本地开发有安全默认值。"""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", env_prefix="DJANGO_", extra="ignore"
    )

    secret_key: str = "django-insecure-lab-key-change-me"
    debug: bool = True
    allowed_hosts: list[str] = ["localhost", "127.0.0.1"]
    database_url: str = "sqlite:///" + (BASE_DIR / "db.sqlite3").as_posix()
    log_level: str = "INFO"


env = Env()

SECRET_KEY = env.secret_key
DEBUG = env.debug
ALLOWED_HOSTS = env.allowed_hosts

# Django 设计思想「batteries included」：admin/auth/sessions/messages/staticfiles 全部内置。
# 应用间松耦合：catalog 是业务应用，api 只复用它的模型与表单，不 import 对方的视图。
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "django_extensions",
    "catalog",
    "api",
    "sqla_lab",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # whitenoise：生产环境零配置托管静态文件
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Django 5.1+：默认拒绝 —— 所有视图都需要登录，公开页面用 @login_not_required 显式豁免
    "django.contrib.auth.middleware.LoginRequiredMiddleware",
    # structlog：放在认证之后，__call__ 时 request.user 已就绪可绑定
    "config.logging_middleware.RequestLogContextMiddleware",
]

if DEBUG:
    INSTALLED_APPS += ["debug_toolbar"]
    MIDDLEWARE += ["debug_toolbar.middleware.DebugToolbarMiddleware"]
    INTERNAL_IPS = ["127.0.0.1"]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "catalog.context_processors.globals",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# 默认 SQLite（开箱即用）；切换 PostgreSQL：DJANGO_DATABASE_URL=postgres://user:pass@host/dbname
# psycopg 3 已放在可选依赖组：uv sync --group postgres
if env.database_url.startswith("sqlite"):
    DATABASES = {
        "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}
    }
else:
    parsed = dict(dj_database_url.parse(env.database_url, conn_max_age=60))
    DATABASES = {"default": parsed}

# Django 6.0+：内置后台任务框架（本 demo 用 immediate 后端，同步执行便于观察日志）
TASKS = {
    "default": {"BACKEND": "django.tasks.backends.immediate.ImmediateBackend"},
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]

if not DEBUG:
    STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "catalog:books"
LOGOUT_REDIRECT_URL = "catalog:books"

# DRF：类的哲学 —— Serializer/ViewSet/Router 组合出 API；浏览式 API 自带管理界面
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticatedOrReadOnly",
    ],
    "DEFAULT_PAGINATION_CLASS": "api.pagination.StandardPagination",
    "PAGE_SIZE": 10,
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {"anon": "120/min", "user": "240/min"},
}

# ---------------------------------------------------------------------------
# 日志：structlog 全面接管 —— 结构化键值日志。
# 设计思想「适配器 + 单一渲染管线」：业务代码用 structlog.get_logger() 写
# 键值对，输出格式由 renderer 决定（开发=彩色控制台，生产=JSON）；
# 标准库日志（Django/DRF/sqlalchemy）经 ProcessorFormatter 走同一条管线，
# 全站日志只有一种格式，而不是两套混着看。
# ---------------------------------------------------------------------------

# 共享前置链：structlog 自身日志与标准库「外来日志」都过一遍，字段口径一致
_SHARED_PRE_CHAIN: list[Processor] = [
    structlog.contextvars.merge_contextvars,  # 合并中间件绑定的请求上下文
    structlog.stdlib.add_logger_name,
    structlog.stdlib.add_log_level,
    structlog.processors.TimeStamper(fmt="iso", utc=False),
]

structlog.configure(
    processors=[
        *_SHARED_PRE_CHAIN,
        structlog.stdlib.PositionalArgumentsFormatter(),  # 兼容 %-style 占位参数
        structlog.processors.StackInfoRenderer(),
        # 不在此处 format_exc_info：异常渲染交给 LOGGING 里的 formatter
        structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
    ],
    logger_factory=structlog.stdlib.LoggerFactory(),  # 输出交给 logging 体系（级别/handler 复用）
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)

if DEBUG:
    _TAIL: list[Processor] = [structlog.dev.ConsoleRenderer(colors=True)]
else:
    # dict_tracebacks：把 traceback 转成 JSON 友好的结构化字段
    _TAIL = [
        structlog.processors.dict_tracebacks,
        structlog.processors.JSONRenderer(ensure_ascii=False),
    ]

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "structlog": {
            "()": structlog.stdlib.ProcessorFormatter,
            "processors": [
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                *_TAIL,
            ],
            "foreign_pre_chain": _SHARED_PRE_CHAIN,
        },
    },
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "structlog"}},
    "root": {"handlers": ["console"], "level": env.log_level},
    # Django 的 DEFAULT_LOGGING 先于本配置应用，且 dictConfig 不会动「未在
    # 此处提及的 logger」—— 不接管的话 django.server 会保留专属的
    # ServerFormatter（runserver 访问日志老格式），django 保留旧 console，
    # DEBUG 时 4xx/5xx 还会双份输出。对「提及的 logger」dictConfig 会先摘
    # 旧 handler 再挂新的，因此这里显式接管：
    "loggers": {
        # 摘掉默认 handler，日志统一上溯 root 走 structlog 渲染（单份）
        "django": {"handlers": []},
        # runserver 访问日志接入同一管线；status>=4xx 自动升 warning/error
        "django.server": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}
