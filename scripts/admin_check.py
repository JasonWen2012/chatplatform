"""管理端验收脚本：授权、越权拦截、禁用、消息移除、举报、审计。

需要先启动服务（``scripts/start.ps1``），并已执行过
``manage.py grant_admin --username <管理员>``。

用法::

    .venv\\Scripts\\python.exe scripts\\admin_check.py
    $env:ADMIN_USERNAME="Jason_Wen"; .venv\\Scripts\\python.exe scripts\\admin_check.py

说明：脚本需要一个**已存在的管理员账号**。由于无法从外部得知其密码，
默认通过 Django ORM 直接确认管理员身份，并用独立会话完成页面与 API 断言；
涉及登录态的部分改用带 Bearer 令牌的协议 API（与前端同一套接口）。
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("CHAT_BASE_URL", "http://127.0.0.1:8000")
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "Jason_Wen")
PASSWORD = "Zq7#md-94kx"
PASSED, FAILED = [], []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name} {detail if not condition else ''}")


def call(method, path, payload=None, token=None, expect=None, raw=False):
    url = BASE + path
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8", "replace")
            status = response.status
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        status = exc.code
    if raw:
        return status, body
    try:
        parsed = json.loads(body) if body else {}
    except ValueError:
        parsed = {"raw": body[:300]}
    if expect is not None and status != expect:
        raise AssertionError(f"{method} {path} 期望 {expect} 实得 {status}: {parsed}")
    return status, parsed


def fetch_page(path, token=None):
    """取页面 HTML（管理页面用 Bearer 也能过鉴权，因守卫走的是 request.user）。"""
    headers = {"Accept": "text/html"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(BASE + path, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def register(username):
    return call("POST", "/api/v1/auth/register/", {
        "username": username, "password": PASSWORD, "nickname": username,
    }, expect=201)[1]["data"]["token"]


def grant_admin_via_orm(username):
    """用 Django ORM 提升为管理员并签发令牌（仅本脚本使用）。"""
    import os as _os

    import django

    _os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backend.config.settings")
    django.setup()
    from backend.accounts.models import Token, User

    user = User.objects.filter(username=username).first()
    if user is None:
        return None, None
    changed = []
    if not user.is_staff:
        user.is_staff = True
        changed.append("is_staff")
    if not user.is_superuser:
        user.is_superuser = True
        changed.append("is_superuser")
    if changed:
        user.save(update_fields=changed)
    token = Token.objects.create(user=user)
    return user.id, token.key


print("=" * 66)
print("管理端验收")
print("=" * 66)

RUN = str(int(time.time()))[-6:]
print(f"\n[0] 准备：管理员 {ADMIN_USERNAME} 与普通测试账号")
admin_id, admin_token = grant_admin_via_orm(ADMIN_USERNAME)
check(f"管理员 {ADMIN_USERNAME} 存在且已授权", admin_id is not None and bool(admin_token))
if admin_id is None:
    print("  未找到该账号，请先执行：manage.py grant_admin --username 你的账号")
    sys.exit(1)

normal_token = register(f"opn{RUN}")
victim_token = register(f"opv{RUN}")
check("准备普通账号", bool(normal_token and victim_token))

me = call("GET", "/api/v1/auth/me/", token=normal_token, expect=200)[1]["data"]["user"]
victim = call("GET", "/api/v1/auth/me/", token=victim_token, expect=200)[1]["data"]["user"]

print("\n[1] 越权拦截（普通用户）")
status, _ = call("GET", "/api/v1/ops/overview/", token=normal_token)
check("普通用户访问 /ops/overview/ → 403", status == 403, f"status={status}")
status, _ = call("GET", "/api/v1/ops/users/", token=normal_token)
check("普通用户访问 /ops/users/ → 403", status == 403, f"status={status}")
status, body = fetch_page("/manage/", token=normal_token)
check("普通用户访问 /manage/ → 403", status == 403, f"status={status}")
status, _ = call("GET", "/api/v1/ops/overview/")
check("未登录访问管理 API → 401", status == 401, f"status={status}")

print("\n[2] 管理页面渲染（管理员）")
for path, needle, label in [
    ("/manage/", "服务器概览", "概览页"),
    ("/manage/users/", "用户管理", "用户页"),
    ("/manage/messages/", "消息检索", "消息页"),
    ("/manage/conversations/", "会话浏览", "会话页"),
    ("/manage/reports/", "举报处理", "举报页"),
    ("/manage/audit/", "操作日志", "日志页"),
]:
    status, body = fetch_page(path, token=admin_token)
    check(f"{label} 可访问", status == 200 and needle in body, f"status={status}")

status, body = fetch_page("/manage/", token=admin_token)
check("管理页引用了样式表", "ops/manage.css" in body)
check("管理页引用了脚本", "ops/manage.js" in body)
check("管理页含返回聊天入口", "返回聊天" in body)
status, body = fetch_page("/static/ops/manage.css")
check("管理样式表可访问", status == 200 and "--ops-brand" in body, f"status={status}")
status, body = fetch_page("/static/ops/manage.js")
check("管理脚本可访问", status == 200 and "ops" in body, f"status={status}")

print("\n[3] 聊天界面入口按角色区分")
status, body = fetch_page("/", token=normal_token)
check("普通用户聊天页无管理入口", 'href="/manage/"' not in body)
status, body = fetch_page("/", token=admin_token)
check("管理员聊天页有管理入口", 'href="/manage/"' in body, f"status={status}")
check("管理员聊天页仍完整可用", "conversationList" in body and "messageInput" in body)

print("\n[4] 概览数据")
status, data = call("GET", "/api/v1/ops/overview/", token=admin_token, expect=200)
overview = data["data"]
check("概览含用户统计", "total" in overview["users"] and "online" in overview["users"])
check("概览含 7 日趋势", len(overview["trend"]) == 7)
check("概览含待处理举报数", "pending" in overview["reports"])

print("\n[5] 禁用 / 解禁 / 强制下线")
status, data = call("PATCH", f"/api/v1/ops/users/{victim['id']}/", {"is_active": False}, token=admin_token)
check("管理员可禁用用户", status == 200 and data["data"]["user"]["is_active"] is False)
status, _ = call("GET", "/api/v1/auth/me/", token=victim_token)
check("被禁用用户的令牌立即失效 → 401", status == 401, f"status={status}")
status, _ = call("PATCH", f"/api/v1/ops/users/{admin_id}/", {"is_active": False}, token=admin_token)
check("管理员不能禁用自己 → 400", status == 400, f"status={status}")
status, _ = call("PATCH", f"/api/v1/ops/users/{victim['id']}/", {"is_active": True}, token=admin_token)
check("管理员可解禁用户", status == 200, f"status={status}")
status, data = call("POST", f"/api/v1/ops/users/{victim['id']}/revoke-tokens/", {}, token=admin_token)
check("强制下线可用", status == 200, f"status={status}")

print("\n[6] 消息移除与实时广播")
# 用普通账号建一个会话并互发消息
victim_token = register(f"opw{RUN}")
peer_token = register(f"opp{RUN}")
peer = call("GET", "/api/v1/auth/me/", token=peer_token, expect=200)[1]["data"]["user"]
victim2 = call("GET", "/api/v1/auth/me/", token=victim_token, expect=200)[1]["data"]["user"]

# 建立好友关系以创建单聊（通过申请-接受流程）
call("POST", "/api/v1/friend-requests/", {"user_id": peer["id"]}, token=victim_token)
incoming = call("GET", "/api/v1/friend-requests/?direction=incoming", token=peer_token, expect=200)[1]["data"]
req_id = incoming["requests"][0]["id"]
call("POST", f"/api/v1/friend-requests/{req_id}/accept/", {}, token=peer_token)
conv = call("POST", "/api/v1/conversations/", {"type": "direct", "peer_id": peer["id"]},
            token=victim_token, expect=201)[1]["data"]["conversation"]
conv_id = conv["id"]

msg = call("POST", f"/api/v1/conversations/{conv_id}/messages/",
           {"body": "这是一条将被管理员移除的消息"}, token=victim_token, expect=201)[1]["data"]["message"]

# 管理员读取非成员会话（旁路）
status, data = call("GET", f"/api/v1/conversations/{conv_id}/messages/", token=admin_token)
check("管理员可读非成员会话消息", status == 200, f"status={status}")
check("旁路读取被标记为 observer",
      call("GET", f"/api/v1/conversations/{conv_id}/", token=admin_token, expect=200)[1]["data"]["conversation"]["my_role"] == "observer")
status, _ = call("POST", f"/api/v1/conversations/{conv_id}/messages/",
                 {"body": "管理员冒名"}, token=admin_token)
check("管理员不能冒名发言 → 404", status == 404, f"status={status}")

status, data = call("POST", f"/api/v1/ops/messages/{msg['id']}/remove/",
                    {"reason": "验收测试"}, token=admin_token)
check("管理员移除消息成功", status == 200, f"status={status}")
check("移除后正文为空", data["data"]["message"]["body"] == "")

status, data = call("GET", f"/api/v1/conversations/{conv_id}/messages/", token=victim_token, expect=200)
target = [m for m in data["data"]["messages"] if m["id"] == msg["id"]][0]
check("普通用户看到 removed 标记", target["removed"] is True)
check("普通用户拿不到正文", target["body"] == "")
check("普通用户拿不到操作人", target.get("removed_by") is None)

status, data = call("GET", f"/api/v1/conversations/{conv_id}/messages/", token=admin_token, expect=200)
admin_view = [m for m in data["data"]["messages"] if m["id"] == msg["id"]][0]
check("管理员视角可见操作人", admin_view.get("removed_by") == admin_id, f"removed_by={admin_view.get('removed_by')}")

status, _ = call("POST", f"/api/v1/ops/messages/{msg['id']}/remove/", {}, token=admin_token)
check("重复移除 → 409", status == 409, f"status={status}")

print("\n[7] 举报流程")
msg2 = call("POST", f"/api/v1/conversations/{conv_id}/messages/",
            {"body": "第二条将被举报的消息"}, token=victim_token, expect=201)[1]["data"]["message"]
status, data = call("POST", f"/api/v1/messages/{msg2['id']}/report/",
                    {"reason": "垃圾信息"}, token=peer_token)
check("普通用户可举报消息", status in (200, 201), f"status={status}")
status, data = call("GET", "/api/v1/ops/reports/?status=pending", token=admin_token, expect=200)
check("管理员可见待处理举报", data["data"]["pending_total"] >= 1)
report_id = None
for item in data["data"]["reports"]:
    if item["message"]["id"] == msg2["id"]:
        report_id = item["id"]
check("找到刚提交的举报", report_id is not None)
if report_id:
    status, data = call("POST", f"/api/v1/ops/reports/{report_id}/resolve/",
                        {"remove_message": True}, token=admin_token)
    check("举报结案成功", status == 200, f"status={status}")
    check("结案同时移除消息", data["data"]["message_removed"] is True)
    status, _ = call("POST", f"/api/v1/ops/reports/{report_id}/resolve/", {}, token=admin_token)
    check("重复处理 → 409", status == 409, f"status={status}")

print("\n[8] 审计日志")
status, data = call("GET", "/api/v1/ops/audit/", token=admin_token, expect=200)
actions = {log["action"] for log in data["data"]["logs"]}
check("审计含用户禁用记录", "user.ban" in actions or "user.unban" in actions, f"actions={actions}")
check("审计含消息移除记录", "message.remove" in actions)
check("审计含举报结案记录", "report.resolve" in actions)
check("审计含查看会话记录", "conversation.view" in actions)
status, _ = call("GET", "/api/v1/ops/audit/", token=normal_token)
check("普通用户看不到审计日志 → 403", status == 403)

print("\n" + "=" * 66)
print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
if FAILED:
    print("失败项：")
    for name in FAILED:
        print("  -", name)
    sys.exit(1)
print("管理端验收全部通过 ✅")
print("=" * 66)
