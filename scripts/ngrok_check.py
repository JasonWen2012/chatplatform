"""ngrok / 反向代理场景验证。

关键点：登录流程必须使用**全新会话**独立进行，不能复用注册接口下发的
Cookie（注册本身会建立登录态，否则会被重定向到聊天页，取不到登录表单令牌）。

同时注意：HTTPS 场景下 Django 会给 Cookie 加 Secure 标记，
普通 HTTP 探针需要手动回传，这里用轻量 Session 显式管理。

待验证的公网域名可通过环境变量覆盖，默认使用示例中的 ngrok 域名：
    $env:NGROK_ORIGIN="https://<你的域名>.ngrok-free.dev"
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# 被测域名与环境变量 DSH_PUBLIC_ORIGIN 保持一致，便于换域名后直接复跑
NGROK_ORIGIN = os.environ.get("NGROK_ORIGIN") or os.environ.get("DSH_PUBLIC_ORIGIN") \
    or "https://lapped-entourage-headphone.ngrok-free.dev"
BASE = os.environ.get("CHAT_BASE_URL", "http://127.0.0.1:8000")
PASSED, FAILED = [], []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name} {detail if not condition else ''}")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Session:
    """独立会话：手动管理 Cookie，避免 Secure 标记导致探针丢弃 Cookie。"""

    def __init__(self):
        self.cookies = {}
        self.opener = urllib.request.build_opener(NoRedirect)

    def _absorb(self, response):
        for header in response.headers.get_all("Set-Cookie") or []:
            pair = header.split(";", 1)[0]
            if "=" in pair:
                name, _, value = pair.partition("=")
                self.cookies[name.strip()] = value.strip()

    def _send(self, path, data=None, extra=None, method="GET"):
        headers = dict(extra or {})
        if self.cookies:
            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        request = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
        try:
            response = self.opener.open(request, timeout=30)
        except urllib.error.HTTPError as exc:
            self._absorb(exc)
            return exc.code, exc.read().decode("utf-8", "replace")
        self._absorb(response)
        return response.status, response.read().decode("utf-8", "replace")

    def get(self, path, extra=None):
        return self._send(path, extra=extra)

    def post_form(self, path, fields, extra=None):
        return self._send(
            path,
            data=urllib.parse.urlencode(fields).encode(),
            extra={"Content-Type": "application/x-www-form-urlencoded", **(extra or {})},
            method="POST",
        )


def register(username, password):
    """用独立的一次性请求建号，不污染任何后续会话。"""
    request = urllib.request.Request(
        BASE + "/api/v1/auth/register/",
        data=json.dumps({"username": username, "password": password}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.status


def form_login(username, password, origin, https=True):
    """在全新会话里走完整登录流程，返回 (最终状态码, 页面内容, 是否拿到会话 Cookie)。"""
    session = Session()
    extra = {"X-Forwarded-Proto": "https"} if https else None

    status, page = session.get("/login/", extra=extra)
    if status != 200:
        return status, f"GET /login/ 返回 {status}", False

    match = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page)
    if not match:
        return status, "登录页未找到 CSRF 令牌", False

    headers = {}
    if origin:
        headers["Origin"] = origin
        headers["Referer"] = f"{origin}/login/"
    if https:
        headers["X-Forwarded-Proto"] = "https"

    status, body = session.post_form(
        "/login/",
        {"csrfmiddlewaretoken": match.group(1), "username": username, "password": password},
        headers,
    )
    got_session = "sessionid" in session.cookies
    if status == 302:
        status, body = session.get("/", extra={"X-Forwarded-Proto": "https"})
    return status, body, got_session


print("=" * 66)
print("ngrok / 反向代理场景验证")
print("=" * 66)

RUN = str(int(time.time()))[-6:]
USERNAME = f"ng{RUN}"
PASSWORD = "Zq7#md-94kx"

check("准备测试账号", register(USERNAME, PASSWORD) == 201)

print("\n[1] 经 ngrok 访问（可信来源 + X-Forwarded-Proto）")
status, body, got_session = form_login(USERNAME, PASSWORD, NGROK_ORIGIN)
check("登录表单提交成功并进入聊天页",
      status == 200 and "boot-data" in body, f"status={status} body={body[:80]}")
check("服务端下发了会话 Cookie", got_session)
check("未出现 CSRF 失败", "Origin checking failed" not in body and "CSRF" not in body)

print("\n[2] 回归：本地直连（无 Origin）")
status, body, got_session = form_login(USERNAME, PASSWORD, None, https=False)
check("本地登录正常进入聊天页", status == 200 and "boot-data" in body, f"status={status}")

print("\n[3] 安全回归：不可信来源仍被拒绝")
status, body, _ = form_login(USERNAME, PASSWORD, "https://evil.example.com")
check("未列入 CSRF_TRUSTED_ORIGINS 的来源被拒（403）", status == 403, f"status={status}")
check("拒绝原因为 Origin 校验", "Origin checking failed" in body)

print("\n[4] 静态资源与协议文档")
session = Session()
for path, needle in [("/static/js/app.js", "startEventStream"), ("/static/css/app.css", "--brand"),
                     ("/api-docs/", "updates/")]:
    status, body = session.get(path)
    check(f"{path} 正常返回", status == 200 and needle in body, f"status={status}")

print("\n" + "=" * 66)
print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
if FAILED:
    print("失败项：" + "; ".join(FAILED))
    sys.exit(1)
print("ngrok 场景验证全部通过 ✅")
print("=" * 66)
