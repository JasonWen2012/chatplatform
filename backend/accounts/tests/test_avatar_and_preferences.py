"""头像上传与用户偏好测试。"""
import io

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from PIL import Image

from backend.accounts.api.api import user_is_admin
from backend.accounts.models import UserPreference
from backend.tests_utils import api_patch, data_of, error_code, make_client, make_user


def image_bytes(size=(600, 400), fmt="PNG", mode="RGB", color=(7, 193, 96)):
    buffer = io.BytesIO()
    Image.new(mode, size, color).save(buffer, format=fmt)
    return buffer.getvalue()


class AvatarUploadTests(TestCase):
    def setUp(self):
        self.user = make_user("alice", "爱丽丝")
        self.client = make_client(self.user)

    def upload(self, content, name="photo.png", content_type="image/png"):
        return self.client.post(
            "/api/v1/users/me/avatar/",
            data={"file": SimpleUploadedFile(name, content, content_type=content_type)},
        )

    def test_avatar_is_cropped_to_square(self):
        """非正方形图片必须被裁成 256×256，否则不同头像会撑破界面。"""
        response = self.upload(image_bytes(size=(600, 400)))
        self.assertEqual(response.status_code, 200, response.content[:200])
        self.user.refresh_from_db()
        self.assertTrue(self.user.avatar)

        with Image.open(self.user.avatar.path) as saved:
            self.assertEqual(saved.size, (256, 256))
            self.assertEqual(saved.format, "JPEG")

    def test_avatar_saved_and_returned_in_profile(self):
        response = self.upload(image_bytes())
        payload = data_of(response)["user"]
        self.assertTrue(payload["avatar"])
        self.user.refresh_from_db()
        self.assertTrue(self.user.avatar.name.endswith(".jpg"))

    def test_portrait_image_also_squared(self):
        """竖图同样裁成正方形，不能只处理横图。"""
        response = self.upload(image_bytes(size=(300, 900)))
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        with Image.open(self.user.avatar.path) as saved:
            self.assertEqual(saved.size, (256, 256))

    def test_transparent_png_converted_to_jpeg(self):
        """带透明通道的 PNG 无法直接存 JPEG，必须转成 RGB。"""
        response = self.upload(image_bytes(fmt="PNG", mode="RGBA"))
        self.assertEqual(response.status_code, 200, response.content[:200])
        self.user.refresh_from_db()
        with Image.open(self.user.avatar.path) as saved:
            self.assertEqual(saved.mode, "RGB")

    def test_fake_image_rejected(self):
        response = self.upload(b"not an image at all", name="fake.png")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(error_code(response), "invalid_image")

    def test_missing_file_rejected(self):
        response = self.client.post("/api/v1/users/me/avatar/", data={})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(error_code(response), "missing_file")

    def test_oversized_avatar_rejected(self):
        from django.test import override_settings

        with override_settings(MAX_UPLOAD_SIZE=10):
            response = self.upload(image_bytes())
        self.assertEqual(response.status_code, 413)

    def test_replacing_avatar_removes_old_file(self):
        self.upload(image_bytes(color=(1, 2, 3)))
        self.user.refresh_from_db()
        first_name = self.user.avatar.name

        self.upload(image_bytes(color=(9, 8, 7)))
        self.user.refresh_from_db()
        self.assertNotEqual(self.user.avatar.name, first_name)

        storage = self.user.avatar.storage
        self.assertFalse(storage.exists(first_name), "旧头像文件应被删除，避免磁盘泄漏")

    def test_requires_authentication(self):
        from django.test import Client

        response = Client().post("/api/v1/users/me/avatar/", data={})
        self.assertEqual(response.status_code, 401)


class PreferenceTests(TestCase):
    def setUp(self):
        self.user = make_user("alice", "爱丽丝")
        self.client = make_client(self.user)

    def test_defaults_are_returned_lazily(self):
        response = self.client.get("/api/v1/users/me/preferences/")
        self.assertEqual(response.status_code, 200)
        prefs = data_of(response)["preferences"]
        self.assertEqual(prefs["theme"], "system")
        self.assertEqual(prefs["font_size"], "normal")
        # 惰性创建：首次读取才落库
        self.assertTrue(UserPreference.objects.filter(user=self.user).exists())

    def test_patch_updates_allowlisted_fields(self):
        response = api_patch(self.client, "/api/v1/users/me/preferences/", {
            "theme": "dark", "font_size": "large", "notify_desktop": True,
        })
        self.assertEqual(response.status_code, 200)
        prefs = data_of(response)["preferences"]
        self.assertEqual(prefs["theme"], "dark")
        self.assertEqual(prefs["font_size"], "large")
        self.assertTrue(prefs["notify_desktop"])

        stored = UserPreference.objects.get(user=self.user)
        self.assertEqual(stored.theme, "dark")

    def test_invalid_theme_rejected(self):
        response = api_patch(self.client, "/api/v1/users/me/preferences/", {"theme": "neon"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(error_code(response), "invalid_theme")

    def test_invalid_font_size_rejected(self):
        response = api_patch(self.client, "/api/v1/users/me/preferences/", {"font_size": "huge"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(error_code(response), "invalid_font_size")

    def test_unknown_fields_ignored(self):
        response = api_patch(self.client, "/api/v1/users/me/preferences/", {
            "theme": "dark", "totally_unknown": "x",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(data_of(response)["preferences"]["theme"], "dark")

    def test_preferences_are_per_user(self):
        other = make_user("bob", "小鲍")
        other_client = make_client(other)
        api_patch(self.client, "/api/v1/users/me/preferences/", {"theme": "dark"})
        response = other_client.get("/api/v1/users/me/preferences/")
        self.assertEqual(data_of(response)["preferences"]["theme"], "system")

    def test_requires_authentication(self):
        from django.test import Client

        self.assertEqual(Client().get("/api/v1/users/me/preferences/").status_code, 401)


class AdminFlagTests(TestCase):
    """``user_is_admin`` 是全部管理权限的唯一判定入口，必须稳定。"""

    def test_regular_user_is_not_admin(self):
        self.assertFalse(user_is_admin(make_user("alice")))

    def test_staff_user_is_admin(self):
        user = make_user("staffer")
        user.is_staff = True
        user.save()
        self.assertTrue(user_is_admin(user))

    def test_inactive_staff_is_not_admin(self):
        user = make_user("banned")
        user.is_staff = True
        user.is_active = False
        user.save()
        self.assertFalse(user_is_admin(user), "被禁用的管理员必须立即失去权限")

    def test_anonymous_is_not_admin(self):
        from django.contrib.auth.models import AnonymousUser

        self.assertFalse(user_is_admin(AnonymousUser()))
        self.assertFalse(user_is_admin(None))
