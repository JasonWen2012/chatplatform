"""二期接口预留验收：确认 App / WebSocket 的接入面已经就绪。

检查项：
* ``/api/v1/capabilities/`` 可用且声明了 transport 与 features；
* 登录响应携带同一份 capabilities；
* 前端传输层抽象与后端事件存储工厂文件存在且满足约定；
* 协议文档已发布 WebSocket 契约（路径、订阅、心跳、断线续传）；
* 事件存储后端可通过配置切换（memory 可用，redis 明确报未实现而不是静默失败）。

需要先启动服务（``scripts/start.ps1``）。
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("CHAT_BASE_URL", "http://127.0.0.1:8000")
# 兼容两种执行方式：直接运行（有 __file__）与内联 exec（无 __file__，回退到工作目录）
try:
    PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
except NameError:  # pragma: no cover - 内联执行时走到这里
    PROJECT_ROOT = os.getcwd()
PASSED, FAILED = [], []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name} {detail if not condition else ''}")


def get(path, token=None):
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(BASE + path, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def post(path, payload, token=None):
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


print("=" * 66)
print("二期接口预留验收")
print("=" * 66)

print("\n[1] 能力声明端点")
status, body = get("/api/v1/capabilities/")
check("/capabilities/ 无需鉴权即可访问", status == 200, f"status={status}")
caps = json.loads(body)["data"]["capabilities"] if status == 200 else {}
check("声明了 api_version", caps.get("api_version") == "v1", f"caps={caps}")
check("声明了 longpoll 传输", "longpoll" in (caps.get("transport") or []))
check("features 覆盖关键词功能",
      {"groups", "attachments", "read_receipts", "revoke", "typing"} <= set(caps.get("features") or []))

print("\n[2] 登录响应携带能力声明（App 握手）")
RUN = str(int(time.time()))[-6:]
username = f"wsc{RUN}"
status, payload = post("/api/v1/auth/register/", {
    "username": username, "password": "Zq7#md-94kx", "nickname": "契约测试",
})
check("注册成功", status == 201, f"status={status}")
data = payload.get("data", {}) if status == 201 else {}
check("注册响应含 capabilities", "capabilities" in data)
check("capabilities 与公开端点一致", data.get("capabilities") == caps)
check("注册响应仍含 token", bool(data.get("token")))
token = data.get("token")

print("\n[3] 前端传输层抽象")
transport_path = os.path.join(PROJECT_ROOT, "backend", "static", "js", "transport.js")
check("transport.js 存在", os.path.isfile(transport_path))
if os.path.isfile(transport_path):
    with open(transport_path, encoding="utf-8") as handle:
        source = handle.read()
    for method in ["start", "stop", "setCursor", "getCursor"]:
        check(f"传输层声明了 {method}()", f"{method}(" in source)
    check("传输层暴露 createTransport 工厂", "createTransport" in source)
    check("传输层已为 websocket 预留判断", "websocket" in source)
    check("传输层与后端信封字段一致",
          all(field in source for field in ["event_id", "conversation_id", "payload"]))

print("\n[4] 后端事件存储工厂")
registry_path = os.path.join(PROJECT_ROOT, "backend", "realtime", "registry.py")
check("registry.py 存在", os.path.isfile(registry_path))
if os.path.isfile(registry_path):
    with open(registry_path, encoding="utf-8") as handle:
        registry = handle.read()
    check("提供 get_event_store()", "def get_event_store" in registry)
    check("读取 EVENT_STORE_BACKEND 配置", "EVENT_STORE_BACKEND" in registry)
    check("redis 分支明确报未实现而非静默失败", "ImproperlyConfigured" in registry)
    check("声明了接口清单（二期实现对照用）", "EVENT_STORE_INTERFACE" in registry)

print("\n[5] 协议文档已冻结 WebSocket 契约")
status, html = get("/api-docs/")
check("协议文档可访问", status == 200, f"status={status}")
for keyword, label in [
    ("/ws/events/", "WebSocket 路径"),
    ("subscribe", "订阅帧"),
    ("ping", "心跳"),
    ("event_id", "断线续传游标"),
    ("capabilities", "能力声明说明"),
    ("message.removed", "管理员移除事件"),
]:
    check(f"文档含{label}", keyword in html, f"keyword={keyword}")

print("\n[6] 健康检查（部署探针）")
status, body = get("/healthz")
check("/healthz 可访问", status == 200, f"status={status}")
try:
    health = json.loads(body)
    check("返回 ok 与 db 状态", health.get("ok") is True and health.get("db") is True)
    check("包含版本号", bool(health.get("version")))
    check("不泄露配置细节", "SECRET" not in body and "MEDIA_ROOT" not in body)
except ValueError:
    check("healthz 返回 JSON", False, body[:120])

print("\n[7] 传输层与后端事件契约对齐")
if token:
    boot = post("/api/v1/updates/", {"cursor": 0, "timeout": 0}, token=token)
    check("长轮询端点可用", boot[0] == 200, f"status={boot[0]}")
    payload = boot[1].get("data", {})
    check("响应含 cursor 与 events 字段", "cursor" in payload and "events" in payload)
    check("空轮询返回 timed_out 标记", "timed_out" in payload)

print("\n" + "=" * 66)
print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
if FAILED:
    print("失败项：")
    for name in FAILED:
        print("  -", name)
    sys.exit(1)
print("二期接口预留验收全部通过 ✅")
print("=" * 66)
