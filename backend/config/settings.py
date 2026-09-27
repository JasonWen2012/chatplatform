"""Django 配置：类微信即时通信服务器（第一期）。"""
import os
from pathlib import Path

# 项目根目录（manage.py 所在目录）
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# ---------------------------------------------------------------- 安全
# 开发密钥；生产部署请通过环境变量注入
SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "dev-only-insecure-key-change-me-in-production-0123456789abcdef",
)
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"


def _split_env(name):
    """读取逗号分隔的环境变量，返回去空白后的列表。"""
    raw = os.environ.get(name, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


# 公网访问（ngrok / cloudflared / 反向代理）时配置：
#   DSH_PUBLIC_ORIGIN=https://xxx.ngrok-free.dev
# 也支持 DSH_PUBLIC_HOSTS 传入逗号分隔的多个来源。
PUBLIC_ORIGINS = _split_env("DSH_PUBLIC_ORIGIN") + _split_env("DSH_PUBLIC_HOSTS")

# 通过 HTTPS 对外访问时设为 1，让 Cookie 带上 Secure 标记
PUBLIC_HTTPS = os.environ.get("DSH_PUBLIC_HTTPS", "").strip().lower() in {"1", "true", "yes", "on"}

ALLOWED_HOSTS = [
    "127.0.0.1",
    "localhost",
    "0.0.0.0",
    "[::1]",
]
# 把公网域名（去掉协议部分）并入允许的主机名
for _origin in PUBLIC_ORIGINS:
    _host = _origin.split("://", 1)[-1].split("/", 1)[0]
    if _host and _host not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(_host)
# 允许局域网内手机访问；开发环境放开
if DEBUG:
    ALLOWED_HOSTS.append("*")

# Django 4.2 起 POST 会校验 Origin 头，经 ngrok/反向代理访问时必须显式信任，
# 否则登录、注册等表单提交会报「CSRF verification failed / Origin checking failed」。
CSRF_TRUSTED_ORIGINS = [
    "http://127.0.0.1:8000",
    "http://localhost:8000",
]
CSRF_TRUSTED_ORIGINS += [origin for origin in PUBLIC_ORIGINS if origin not in CSRF_TRUSTED_ORIGINS]

# ---------------------------------------------------------------- 应用
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # 业务应用
    "backend.accounts",
    "backend.contacts",
    "backend.chat",
    "backend.realtime",
    "backend.apiapp",
    "backend.ops",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "backend.config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "backend" / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "backend.config.wsgi.application"
ASGI_APPLICATION = "backend.config.asgi.application"

# ---------------------------------------------------------------- 数据库
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
        # 聊天场景写入频繁：放宽锁等待超时，降低 "database is locked" 概率。
        # WAL 与 synchronous 通过 backend/config/db_pragmas.py 的连接信号设置
        # （SQLite 后端不支持 MySQL 那种 init_command）。
        "OPTIONS": {
            "timeout": 20,
        },
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------- 认证
AUTH_USER_MODEL = "accounts.User"

# 登录页地址。必须显式指定：Django 的 redirect_to_login() / login_required 在
# 未设置 LOGIN_URL 时会默认跳到 /accounts/login/，而本项目没有该路径，
# 结果是「未登录访问首页」被重定向到一个 404 页面。
LOGIN_URL = "web-login"
LOGIN_REDIRECT_URL = "web-app"
LOGOUT_REDIRECT_URL = "web-login"

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
        # 默认按 username/email 等字段比对；显式列出以保证行为稳定
        "OPTIONS": {"user_attributes": ("username", "nickname", "email")},
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 6},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ---------------------------------------------------------------- 国际化
LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------- 静态与媒体
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
# 开发期直接由 Django 提供前端静态资源，无需 collectstatic
STATICFILES_DIRS = [BASE_DIR / "backend" / "static"]

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# ---------------------------------------------------------------- 上传限制
# 单个附件上限 20MB；超过则走临时文件而非内存
MAX_UPLOAD_SIZE = 20 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = MAX_UPLOAD_SIZE
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024

# 允许的附件类型（Pillow 会做真实解码校验；此处仅做前置白名单）
ALLOWED_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "webp", "bmp"}
MAX_IMAGE_PIXELS = 40_000_000  # 约 40MP，防解压炸弹

# ---------------------------------------------------------------- 会话与 Cookie
SESSION_COOKIE_AGE = 60 * 60 * 24 * 14  # 14 天
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"

# ngrok 等反向代理会在 X-Forwarded-Proto 里告知真实协议。
# 不声明此项时，Django 认为请求是 HTTP，开启 Secure 后 Cookie 无法下发。
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# 本地 HTTP 开发不能开 Secure（否则 Cookie 不下发）；
# 经 ngrok 以 HTTPS 对外访问时设置 DSH_PUBLIC_HTTPS=1 打开。
SESSION_COOKIE_SECURE = PUBLIC_HTTPS
CSRF_COOKIE_SECURE = PUBLIC_HTTPS
# 关闭 Referer 严格校验，避免代理改写 Referer 导致 CSRF 误判
CSRF_USE_SESSIONS = False
CSRF_COOKIE_HTTPONLY = False

# ---------------------------------------------------------------- 实时层参数
# 事件存储后端：一期为进程内内存；二期接 WebSocket/多 worker 时改为 "redis"
# （切换点见 backend/realtime/registry.py，业务代码无需改动）
EVENT_STORE_BACKEND = os.environ.get("EVENT_STORE_BACKEND", "memory")
# 长轮询：单次请求最长挂起秒数、轮询检查间隔
LONGPOLL_TIMEOUT_SECONDS = 25
LONGPOLL_POLL_INTERVAL = 0.5
# 每用户事件缓冲窗口（秒），超出即裁剪，防止内存无限增长
EVENT_BUFFER_TTL_SECONDS = 600
# 「正在输入」状态有效期（秒）
TYPING_TTL_SECONDS = 5

# ---------------------------------------------------------------- 日志
# 落盘到 logs/app.log 并轮转，避免长期运行把磁盘写满；
# 只记录「谁在何时做了什么」，绝不记录密码、令牌明文与消息正文。
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{asctime} [{levelname}] {name}: {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "verbose"},
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(LOG_DIR / "app.log"),
            "maxBytes": 5 * 1024 * 1024,
            "backupCount": 3,
            "encoding": "utf-8",
            "formatter": "verbose",
        },
    },
    "root": {"handlers": ["console", "file"], "level": "INFO"},
    "loggers": {
        # 未处理异常
        "chat.api": {"handlers": ["console", "file"], "level": "INFO", "propagate": False},
        # 登录失败：无限流时用于事后发现暴力破解
        "chat.auth": {"handlers": ["console", "file"], "level": "INFO", "propagate": False},
        "django.request": {"handlers": ["console", "file"], "level": "WARNING", "propagate": False},
    },
}
