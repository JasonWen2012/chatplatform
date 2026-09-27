"""账号与认证测试。"""
import json

from django.test import Client, TestCase

from backend.accounts.models import Token, User
from backend.tests_utils import DEFAULT_PASSWORD as PWD
from backend.tests_utils import make_user


def post_json(client, path, payload, token=None):
    headers = {}
    if token:
        headers["HTTP_AUTHORIZATION"] = f"Bearer {token}"
    return client.post(path, data=json.dumps(payload), content_type="application/json", **headers)


class RegistrationTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_register_creates_user_and_returns_token(self):
        response = post_json(self.client, "/api/v1/auth/register/", {
            "username": "alice", "password": PWD, "nickname": "爱丽丝",
        })
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertIn("token", body["data"])
        self.assertEqual(body["data"]["user"]["nickname"], "爱丽丝")

        user = User.objects.get(username="alice")
        self.assertTrue(user.check_password(PWD))
        # 密码不能以明文出现在任何响应里
        self.assertNotIn(PWD, response.content.decode())

    def test_duplicate_username_rejected(self):
        make_user("bob")
        response = post_json(self.client, "/api/v1/auth/register/", {
            "username": "BOB", "password": PWD,
        })
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "username_taken")

    def test_short_password_rejected(self):
        response = post_json(self.client, "/api/v1/auth/register/", {
            "username": "carol", "password": "123",
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "weak_password")

    def test_password_equal_to_username_rejected(self):
        """Django 密码校验器应生效：密码与用户名完全一致必须被拒。"""
        response = post_json(self.client, "/api/v1/auth/register/", {
            "username": "dave", "password": "dave",
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "weak_password")

    def test_common_password_rejected(self):
        """常见弱口令应被 CommonPasswordValidator 拦下。"""
        response = post_json(self.client, "/api/v1/auth/register/", {
            "username": "erin", "password": "password",
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "weak_password")

    def test_all_numeric_password_rejected(self):
        """纯数字密码应被 NumericPasswordValidator 拦下。"""
        response = post_json(self.client, "/api/v1/auth/register/", {
            "username": "frank", "password": "92837465",
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "weak_password")

    def test_auto_nickname_assigned(self):
        response = post_json(self.client, "/api/v1/auth/register/", {
            "username": "dave", "password": PWD,
        })
        self.assertTrue(response.json()["data"]["user"]["nickname"])


class LoginTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = make_user("alice", "爱丽丝")

    def test_login_by_username(self):
        response = post_json(self.client, "/api/v1/auth/login/", {
            "username": "alice", "password": PWD,
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn("token", response.json()["data"])

    def test_login_by_nickname(self):
        response = post_json(self.client, "/api/v1/auth/login/", {
            "username": "爱丽丝", "password": PWD,
        })
        self.assertEqual(response.status_code, 200)

    def test_wrong_password_rejected(self):
        response = post_json(self.client, "/api/v1/auth/login/", {
            "username": "alice", "password": "wrong-password",
        })
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "invalid_credentials")


class TokenAuthTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = make_user("alice")
        self.token = Token.objects.create(user=self.user)

    def test_bearer_token_grants_access(self):
        response = self.client.get(
            "/api/v1/auth/me/", HTTP_AUTHORIZATION=f"Bearer {self.token.key}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["user"]["username"], "alice")

    def test_unauthenticated_returns_json_401(self):
        response = self.client.get("/api/v1/auth/me/")
        self.assertEqual(response.status_code, 401)
        self.assertFalse(response.json()["ok"])
        # 必须是 JSON 而不是 302 跳转，外部客户端才能预期
        self.assertEqual(response["Content-Type"].split(";")[0], "application/json")

    def test_invalid_token_rejected(self):
        response = self.client.get("/api/v1/auth/me/", HTTP_AUTHORIZATION="Bearer not-a-token")
        self.assertEqual(response.status_code, 401)

    def test_logout_revokes_token(self):
        response = self.client.post(
            "/api/v1/auth/logout/", HTTP_AUTHORIZATION=f"Bearer {self.token.key}"
        )
        self.assertEqual(response.status_code, 200)
        self.token.refresh_from_db()
        self.assertTrue(self.token.revoked)
        # 撤销后同一令牌不可再用
        again = self.client.get(
            "/api/v1/auth/me/", HTTP_AUTHORIZATION=f"Bearer {self.token.key}"
        )
        self.assertEqual(again.status_code, 401)

    def test_token_refresh_rotates_token(self):
        response = self.client.post(
            "/api/v1/auth/token/refresh/", HTTP_AUTHORIZATION=f"Bearer {self.token.key}"
        )
        self.assertEqual(response.status_code, 200)
        new_key = response.json()["data"]["token"]
        self.assertNotEqual(new_key, self.token.key)
        self.token.refresh_from_db()
        self.assertTrue(self.token.revoked)


class PasswordChangeTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = make_user("alice")
        self.token = Token.objects.create(user=self.user)

    def test_change_password_revokes_tokens(self):
        new_password = "Rk8#wq-31mv"
        response = post_json(
            self.client,
            "/api/v1/auth/password/",
            {"old_password": PWD, "new_password": new_password},
            token=self.token.key,
        )
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(new_password))
        self.token.refresh_from_db()
        self.assertTrue(self.token.revoked)

    def test_wrong_old_password_rejected(self):
        response = post_json(
            self.client,
            "/api/v1/auth/password/",
            {"old_password": "bad", "new_password": "Rk8#wq-31mv"},
            token=self.token.key,
        )
        self.assertEqual(response.status_code, 401)


class UserSearchTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.me = make_user("alice")
        self.other = make_user("bob", "小鲍")
        self.token = Token.objects.create(user=self.me)

    def test_search_excludes_self(self):
        response = self.client.get(
            "/api/v1/users/search/?q=a", HTTP_AUTHORIZATION=f"Bearer {self.token.key}"
        )
        self.assertEqual(response.status_code, 200)
        ids = [item["id"] for item in response.json()["data"]["results"]]
        self.assertNotIn(self.me.id, ids)

    def test_search_matches_nickname(self):
        response = self.client.get(
            "/api/v1/users/search/?q=小鲍", HTTP_AUTHORIZATION=f"Bearer {self.token.key}"
        )
        ids = [item["id"] for item in response.json()["data"]["results"]]
        self.assertIn(self.other.id, ids)

    def test_empty_query_rejected(self):
        response = self.client.get(
            "/api/v1/users/search/", HTTP_AUTHORIZATION=f"Bearer {self.token.key}"
        )
        self.assertEqual(response.status_code, 400)
