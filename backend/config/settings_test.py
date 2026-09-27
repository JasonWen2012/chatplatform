"""测试专用配置。

仅覆盖会让测试变慢或产生外部副作用的设置，
其余完全继承 backend.config.settings，保证测试与生产行为一致。
"""
from backend.config.settings import *  # noqa: F401,F403

# 长轮询在测试中必须立即返回，否则每个等待用例都会阻塞数十秒
LONGPOLL_TIMEOUT_SECONDS = 0
LONGPOLL_POLL_INTERVAL = 0.01

# 事件缓冲区在测试中保留更久，避免用例间互相影响
EVENT_BUFFER_TTL_SECONDS = 0

# 测试上传目录放在项目内，避免依赖系统临时目录权限
MEDIA_ROOT = BASE_DIR / "_test_media"  # noqa: F405

# 测试运行器：把 PBKDF2 换成 MD5，让 90+ 用例从分钟级降到秒级
TEST_RUNNER = "backend.config.test_runner.FastPasswordHasherRunner"
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# 关闭真实日志，保持测试输出干净
LOGGING = {
    "version": 1,
    "disable_existing_loggers": True,
    "handlers": {"null": {"class": "logging.NullHandler"}},
    "root": {"handlers": ["null"], "level": "CRITICAL"},
}
