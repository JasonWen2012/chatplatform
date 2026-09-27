"""测试专用运行器。

做两件事：

1. 把默认的 PBKDF2 密码哈希（约 72 万次迭代）换成 MD5。
   它只是测试里的主要耗时来源（几十个用例累计数十秒），
   换成 MD5 不改变被测业务逻辑，校验路径完全一致。
2. 测试结束后清理 ``_test_media/``，避免反复运行留下上传产物。
"""
import shutil

from django.conf import settings
from django.test.runner import DiscoverRunner


class FastPasswordHasherRunner(DiscoverRunner):
    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

    def teardown_test_environment(self, **kwargs):
        super().teardown_test_environment(**kwargs)
        media_root = getattr(settings, "MEDIA_ROOT", None)
        # 仅清理测试专用目录，绝不碰正式 media/
        if media_root and str(media_root).rstrip("\\/").endswith("_test_media"):
            shutil.rmtree(media_root, ignore_errors=True)
