"""网页界面冒烟检查：模板渲染、静态资源、登录态跳转、聊天页关键节点。

关于 Cookie：经 HTTPS 隧道访问时服务端会给 Cookie 加 Secure 标记，
而本探针走明文 HTTP，标准 Cookie 容器会拒绝回传，导致误报 403。
因此这里用轻量 Session 手动管理 Cookie，等价于浏览器在 HTTPS 页面上的行为。
"""
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("CHAT_BASE_URL", "http://127.0.0.1:8000")
PASSED, FAILED = [], []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name} {detail if not condition else ''}")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Session:
    """极简会话：手动收存与回传 Cookie，不受 Secure 标记影响。"""

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
            self.last_location = exc.headers.get("Location")
            return exc.code, exc.read().decode("utf-8", "replace"), exc.geturl()
        self._absorb(response)
        self.last_location = response.headers.get("Location")
        return response.status, response.read().decode("utf-8", "replace"), response.geturl()

    def get(self, path, extra=None):
        self.last_location = None
        return self._send(path, extra=extra)

    def post_form(self, path, fields, extra=None):
        return self._send(
            path,
            data=urllib.parse.urlencode(fields).encode(),
            extra={"Content-Type": "application/x-www-form-urlencoded", **(extra or {})},
            method="POST",
        )


ANON = Session()

print("=" * 66)
print("网页界面冒烟检查")
print("=" * 66)

print("\n[1] 静态资源")
for path, needle, label in [
    ("/static/css/app.css", "--brand: #07c160", "样式表"),
    # 实时逻辑已抽到传输层，主脚本改为检查事件处理入口
    ("/static/js/app.js", "startEventStream", "主脚本"),
    ("/static/js/transport.js", "createTransport", "传输层"),
    ("/static/js/api.js", "/api/v1", "API 客户端"),
    ("/static/js/ui.js", "escapeHtml", "UI 工具"),
]:
    status, body, _ = ANON.get(path)
    check(f"{label} 可访问且内容正确", status == 200 and needle in body, f"status={status}")

print("\n[2] 页面渲染")
status, body, _ = ANON.get("/login/")
check("登录页可访问", status == 200 and "登 录" in body, f"status={status}")
check("登录页含 CSRF 令牌", "csrfmiddlewaretoken" in body)

status, body, _ = ANON.get("/register/")
check("注册页可访问", status == 200 and "注 册" in body, f"status={status}")

status, body, _ = ANON.get("/api-docs/")
check("协议文档可访问", status == 200 and "/api/v1/updates/" in body, f"status={status}")
check("协议文档含事件类型说明", "message.new" in body and "typing" in body)

print("\n[3] 未登录跳转（必须落在真实存在的登录页）")
status, body, url = ANON.get("/")
location = ANON.last_location or ""
check("未登录访问 / 返回跳转", status in (301, 302), f"status={status}")
check("跳转目标是 /login/ 而不是 /accounts/login/",
      location.startswith("/login/") and "accounts/login" not in location,
      f"Location={location}")
check("跳转带上了 next 回跳参数", "next=" in location, f"Location={location}")

# 关键：显式验证跳转目标本身可达，而不是让客户端自动跟随掩盖中间的错误跳转
status2, body2, _ = ANON.get("/login/")
check("跳转目标页面可用（非 404）",
      status2 == 200 and "csrfmiddlewaretoken" in body2, f"status={status2}")

print("\n[4] 注册并进入聊天页")
username = f"web{str(int(time.time()))[-6:]}"
password = "Zq7#md-94kx"

session = Session()
status, body, _ = session.get("/register/")
token_match = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', body)
check("拿到注册页 CSRF 令牌", token_match is not None)

status, body, url = session.post_form(
    "/register/",
    {
        "csrfmiddlewaretoken": token_match.group(1) if token_match else "",
        "username": username,
        "nickname": "网页用户",
        "password": password,
        "password_confirm": password,
    },
    extra={"Referer": BASE + "/register/"},
)
if status == 302:
    status, body, url = session.get("/")
check("网页注册成功并进入聊天页",
      status == 200 and "boot-data" in body, f"status={status} url={url}")

check("聊天页注入当前用户", "网页用户" in body)
check("聊天页注入访问令牌", re.search(r'"token": "[A-Za-z0-9_\-]{20,}"', body) is not None)
for element in ["conversationList", "messageList", "messageInput", "emojiPanel", "chatView"]:
    check(f"聊天页含关键节点 {element}", f'id="{element}"' in body)

print("\n" + "=" * 66)
print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
if FAILED:
    print("失败项：" + ", ".join(FAILED))
    sys.exit(1)
print("网页界面检查全部通过 ✅")
print("=" * 66)
