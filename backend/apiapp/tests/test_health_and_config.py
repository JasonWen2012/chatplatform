"""健康检查与生产配置校验测试。"""
import os
from unittest import mock

from django.core.checks import run_checks
from django.test import Client, TestCase, override_settings

from backend.apiapp import checks as prod_checks
from backend.apiapp.health import VERSION


class HealthCheckTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_healthz_ok(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["db"])
        self.assertEqual(payload["version"], VERSION)

    def test_healthz_returns_503_when_db_fails(self):
        with mock.patch("backend.apiapp.health.connection") as fake_connection:
            fake_connection.cursor.side_effect = Exception("db down")
            response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json()["ok"])

    def test_healthz_does_not_leak_configuration(self):
        body = self.client.get("/healthz").content.decode("utf-8")
        for secret in ["SECRET", "MEDIA_ROOT", "DATABASES", "password", "C:\\", "/home"]:
            self.assertNotIn(secret, body, f"健康检查不应暴露 {secret}")

    def test_healthz_needs_no_authentication(self):
        self.assertEqual(Client().get("/healthz").status_code, 200)


class ProductionConfigCheckTests(TestCase):
    """生产模式下的配置校验必须真的能拦住危险配置。"""

    def _check_ids(self):
        return {issue.id for issue in run_checks()}

    def test_dev_mode_allows_missing_secret(self):
        with mock.patch.dict(os.environ, {"DJANGO_DEBUG": "1"}, clear=False):
            self.assertNotIn("chat.E001", self._check_ids())

    def test_production_without_secret_key_is_blocked(self):
        env = {"DJANGO_DEBUG": "0"}
        with mock.patch.dict(os.environ, env, clear=False):
            os.environ.pop("DJANGO_SECRET_KEY", None)
            self.assertIn(
                "chat.E001", self._check_ids(),
                "生产模式缺少密钥必须报错，否则会用开发密钥上线",
            )

    def test_production_with_dev_fallback_secret_is_blocked(self):
        with mock.patch.dict(os.environ, {
            "DJANGO_DEBUG": "0",
            "DJANGO_SECRET_KEY": prod_checks.DEV_FALLBACK_SECRET,
        }, clear=False):
            self.assertIn("chat.E001", self._check_ids())

    def test_production_with_strong_secret_passes_that_check(self):
        with mock.patch.dict(os.environ, {
            "DJANGO_DEBUG": "0",
            "DJANGO_SECRET_KEY": "x" * 64,
        }, clear=False):
            self.assertNotIn("chat.E001", self._check_ids())

    def test_wildcard_host_blocked_in_production(self):
        with mock.patch.dict(os.environ, {"DJANGO_DEBUG": "0"}, clear=False):
            with override_settings(ALLOWED_HOSTS=["*"]):
                self.assertIn("chat.E002", self._check_ids())

    def test_wildcard_host_allowed_in_development(self):
        with mock.patch.dict(os.environ, {"DJANGO_DEBUG": "1"}, clear=False):
            with override_settings(ALLOWED_HOSTS=["*"]):
                self.assertNotIn("chat.E002", self._check_ids())

    def test_insecure_cookies_warned_in_production(self):
        with mock.patch.dict(os.environ, {"DJANGO_DEBUG": "0"}, clear=False):
            with override_settings(SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False):
                ids = self._check_ids()
                self.assertIn("chat.W002", ids)
                self.assertIn("chat.W003", ids)

    def test_debug_true_warns(self):
        with override_settings(DEBUG=True):
            self.assertIn("chat.W004", self._check_ids())
