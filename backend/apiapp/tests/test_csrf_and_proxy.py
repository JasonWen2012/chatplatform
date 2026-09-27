"""网页表单 CSRF 与公网访问（ngrok / 反向代理）配置测试。

背景：经 ngrok 等 HTTPS 隧道访问时，Django 4.2 会校验 POST 的 Origin 头，
若域名未列入 CSRF_TRUSTED_ORIGINS，登录与注册会直接 403。
这里既验证「放行公网域名」的配置逻辑，也验证 CSRF 防护本身没有被削弱。
"""
from django.test import Client, TestCase
from django.urls import resolve, reverse

from backend.config import settings as project_settings
from backend.tests_utils import DEFAULT_PASSWORD as PWD
from backend.tests_utils import make_user


class LoginRedirectTests(TestCase):
    """未登录跳转必须落在真实存在的登录页上。

    背景（真实故障）：Django 的 ``redirect_to_login()`` 与 ``login_required``
    在未设置 ``LOGIN_URL`` 时会默认跳到 ``/accounts/login/``。
    本项目没有该路径，于是「未登录访问首页」被送到一个 404 页面 ——
    浏览器上表现为「能打开 /login/，但一进站点就 404」，极易被误判为 ngrok 或网络问题。
    这里的断言刻意检查 **Location 目标**，而不只是最终状态码：
    前者才能发现中间那次错误跳转。
    """

    def test_login_url_points_to_existing_route(self):
        self.assertEqual(project_settings.LOGIN_URL, "web-login")
        # 命名路由必须能反解，且该路径确实有视图承接
        path = reverse("web-login")
        self.assertEqual(path, "/login/")
        self.assertIsNotNone(resolve(path))

    def test_default_django_login_path_does_not_exist(self):
        """确认 /accounts/login/ 确实没有对应页面（用来解释故障现象）。"""
        response = self.client.get("/accounts/login/")
        self.assertEqual(response.status_code, 404)

    def test_root_redirects_to_real_login_page(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            response["Location"].startswith("/login/"),
            f"未登录应跳转到 /login/，实际为 {response['Location']}",
        )
        self.assertNotIn("/accounts/login/", response["Location"])

    def test_redirect_target_is_reachable(self):
        """跳转目标本身不能是 404 —— 这是本次故障的核心。"""
        location = self.client.get("/").headers["Location"]
        followed = self.client.get(location)
        self.assertEqual(followed.status_code, 200)
        self.assertContains(followed, "csrfmiddlewaretoken")

    def test_manage_page_redirects_to_real_login_page(self):
        """管理界面同样依赖 redirect_to_login，必须一并正确。"""
        response = self.client.get("/manage/")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith("/login/"))
        self.assertNotIn("/accounts/login/", response["Location"])

    def test_next_parameter_preserved(self):
        response = self.client.get("/manage/users/")
        self.assertIn("next=/manage/users/", response["Location"])


class SplitEnvTests(TestCase):
    """环境变量解析：公网域名的来源与主机名提取。"""

    def test_split_env_ignores_blank_items(self):
        import os

        os.environ["DSH_TEST_SPLIT"] = " https://a.example , ,https://b.example,"
        try:
            self.assertEqual(
                project_settings._split_env("DSH_TEST_SPLIT"),
                ["https://a.example", "https://b.example"],
            )
        finally:
            del os.environ["DSH_TEST_SPLIT"]

    def test_split_env_missing_variable_returns_empty(self):
        self.assertEqual(project_settings._split_env("DSH_NOT_DEFINED_AT_ALL"), [])

    def test_public_origins_host_extraction(self):
        """CSRF_TRUSTED_ORIGINS 需带协议，而 ALLOWED_HOSTS 只认主机名。"""
        origins = ["https://lapped-entourage-headphone.ngrok-free.dev", "http://192.168.1.9:8000"]
        hosts = [origin.split("://", 1)[-1].split("/", 1)[0] for origin in origins]
        self.assertEqual(
            hosts,
            ["lapped-entourage-headphone.ngrok-free.dev", "192.168.1.9:8000"],
        )

    def test_local_origins_are_trusted_by_default(self):
        self.assertIn("http://127.0.0.1:8000", project_settings.CSRF_TRUSTED_ORIGINS)
        self.assertIn("http://localhost:8000", project_settings.CSRF_TRUSTED_ORIGINS)

    def test_allowed_hosts_always_contains_loopback(self):
        for host in ("127.0.0.1", "localhost"):
            self.assertIn(host, project_settings.ALLOWED_HOSTS)

    def test_proxy_ssl_header_declared(self):
        """声明 X-Forwarded-Proto 后，Django 才能识别代理转发的 HTTPS。"""
        self.assertEqual(
            project_settings.SECURE_PROXY_SSL_HEADER, ("HTTP_X_FORWARDED_PROTO", "https")
        )


class CsrfProtectionTests(TestCase):
    """CSRF 防护必须保持有效：放行公网域名不等于关闭校验。"""

    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.user = make_user("alice", "爱丽丝")

    def test_login_post_without_csrf_token_rejected(self):
        response = self.client.post(reverse("web-login"), {
            "username": "alice", "password": PWD,
        })
        self.assertEqual(response.status_code, 403)

    def test_login_post_with_csrf_token_succeeds(self):
        page = self.client.get(reverse("web-login"))
        token = page.cookies["csrftoken"].value
        response = self.client.post(reverse("web-login"), {
            "csrfmiddlewaretoken": token,
            "username": "alice",
            "password": PWD,
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.endswith("/"))

    def test_register_post_without_csrf_token_rejected(self):
        response = self.client.post(reverse("web-register"), {
            "username": "newbie",
            "password": PWD,
            "password_confirm": PWD,
        })
        self.assertEqual(response.status_code, 403)

    def test_trusted_origin_post_accepted(self):
        """来源在 CSRF_TRUSTED_ORIGINS 内时，带 token 的跨域来源应被接受。"""
        from django.test import override_settings

        origin = "https://lapped-entourage-headphone.ngrok-free.dev"
        with override_settings(CSRF_TRUSTED_ORIGINS=[origin]):
            page = self.client.get(reverse("web-login"))
            token = page.cookies["csrftoken"].value
            response = self.client.post(
                reverse("web-login"),
                {"csrfmiddlewaretoken": token, "username": "alice", "password": PWD},
                HTTP_ORIGIN=origin,
            )
        self.assertEqual(response.status_code, 302)

    def test_untrusted_origin_post_rejected(self):
        """未列入可信来源的域名必须继续被拒（这正是用户遇到的 403）。"""
        page = self.client.get(reverse("web-login"))
        token = page.cookies["csrftoken"].value
        response = self.client.post(
            reverse("web-login"),
            {"csrfmiddlewaretoken": token, "username": "alice", "password": PWD},
            HTTP_ORIGIN="https://evil.example.com",
        )
        self.assertEqual(response.status_code, 403)

    def test_api_views_are_csrf_exempt(self):
        """协议 API 走 Bearer 令牌，不受 Cookie CSRF 限制。"""
        response = self.client.post(
            "/api/v1/auth/login/",
            data='{"username": "alice", "password": "%s"}' % PWD,
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
