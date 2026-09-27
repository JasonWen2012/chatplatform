"""端到端验收脚本：通过真实 HTTP 协议走通完整聊天流程。

覆盖：注册 → 搜索 → 好友申请 → 接受 → 单聊 → 发消息 → 未读 →
长轮询收取事件 → 标记已读 → 撤回 → 图片上传 → 非成员越权下载被拒。
"""
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8000"
PASSED = []
FAILED = []


def call(method, path, payload=None, token=None, expect=None):
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
        with urllib.request.urlopen(request, timeout=40) as response:
            body = response.read().decode("utf-8")
            status = response.status
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        status = exc.code
    try:
        parsed = json.loads(body) if body else {}
    except ValueError:
        parsed = {"raw": body[:200]}
    if expect is not None and status != expect:
        raise AssertionError(f"{method} {path} 期望 {expect} 实得 {status}: {parsed}")
    return status, parsed


def check(name, condition, detail=""):
    if condition:
        PASSED.append(name)
        print(f"  [PASS] {name}")
    else:
        FAILED.append(name)
        print(f"  [FAIL] {name} {detail}")


def upload_image(path, token):
    boundary = "----dshboundary0123456789"
    with open(path, "rb") as handle:
        content = handle.read()
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="test.png"\r\n'
        f"Content-Type: image/png\r\n\r\n"
    ).encode("utf-8") + content + f"\r\n--{boundary}--\r\n".encode("utf-8")
    request = urllib.request.Request(
        BASE + "/api/v1/attachments/",
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=40) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


print("=" * 66)
print("端到端验收：类微信即时通信服务器")
print("=" * 66)

# 每次运行使用唯一用户名，脚本可重复执行而无需清库
import time  # noqa: E402

RUN = str(int(time.time()))[-6:]
ALICE = f"alice{RUN}"
BOB = f"bob{RUN}"
BOB_NICK = f"小鲍{RUN}"
PASSWORD = "Zq7#md-94kx"

# ---------------------------------------------------------------- 1. 注册
print("\n[1] 注册两个账号")
_, r1 = call("POST", "/api/v1/auth/register/", {
    "username": ALICE, "password": PASSWORD, "nickname": "爱丽丝",
}, expect=201)
_, r2 = call("POST", "/api/v1/auth/register/", {
    "username": BOB, "password": PASSWORD, "nickname": BOB_NICK,
}, expect=201)
alice_token = r1["data"]["token"]
bob_token = r2["data"]["token"]
alice_id = r1["data"]["user"]["id"]
bob_id = r2["data"]["user"]["id"]
check("两个账号注册成功并拿到令牌", bool(alice_token and bob_token))
check("昵称正确写入", r1["data"]["user"]["nickname"] == "爱丽丝")

_, dup = call("POST", "/api/v1/auth/register/", {
    "username": ALICE, "password": PASSWORD,
})
check("重名注册被拒（409）", dup.get("error", {}).get("code") == "username_taken")

# ---------------------------------------------------------------- 2. 鉴权
print("\n[2] 鉴权通道")
status, _ = call("GET", "/api/v1/auth/me/", token=alice_token, expect=200)
check("Bearer 令牌可访问 /auth/me/", status == 200)
status, anon = call("GET", "/api/v1/auth/me/")
check("未登录返回 401 JSON", status == 401 and anon.get("ok") is False)

# ---------------------------------------------------------------- 3. 搜索
print("\n[3] 搜索与好友申请")
_, search = call("GET", f"/api/v1/users/search/?q={urllib.parse.quote(BOB_NICK)}", token=alice_token, expect=200)
check("可按昵称搜索到对方（中文关键词已 URL 编码）",
      any(u["id"] == bob_id for u in search["data"]["results"]))

_, req = call("POST", "/api/v1/friend-requests/", {
    "user_id": bob_id, "message": "你好，我是爱丽丝",
}, token=alice_token, expect=201)
request_id = req["data"]["request"]["id"]
check("好友申请创建成功", req["data"]["request"]["status"] == "pending")

_, incoming = call("GET", "/api/v1/friend-requests/?direction=incoming", token=bob_token, expect=200)
check("对方能看到待处理申请", len(incoming["data"]["requests"]) == 1)

_, not_friends = call("POST", "/api/v1/conversations/", {
    "type": "direct", "peer_id": bob_id,
}, token=alice_token)
check("非好友不能发起会话（403 not_friends）",
      not_friends.get("error", {}).get("code") == "not_friends")

_, accepted = call("POST", f"/api/v1/friend-requests/{request_id}/accept/", {}, token=bob_token, expect=200)
check("接受申请成功", accepted["data"]["request"]["status"] == "accepted")

_, friends = call("GET", "/api/v1/friends/", token=alice_token, expect=200)
check("好友列表已包含对方", [f["id"] for f in friends["data"]["friends"]] == [bob_id])

# ---------------------------------------------------------------- 4. 会话
print("\n[4] 单聊会话（幂等）")
_, conv1 = call("POST", "/api/v1/conversations/", {
    "type": "direct", "peer_id": bob_id,
}, token=alice_token, expect=201)
conv_id = conv1["data"]["conversation"]["id"]
_, conv2 = call("POST", "/api/v1/conversations/", {
    "type": "direct", "peer_id": alice_id,
}, token=bob_token, expect=200)
check("单聊创建幂等，双向复用同一会话",
      conv2["data"]["conversation"]["id"] == conv_id and conv2["data"]["created"] is False)

# ---------------------------------------------------------------- 5. 长轮询基线
print("\n[5] 长轮询与消息投递")
_, boot = call("POST", "/api/v1/updates/", {"cursor": 0, "timeout": 0}, token=bob_token, expect=200)
bob_cursor = boot["data"]["cursor"]

_, sent = call("POST", f"/api/v1/conversations/{conv_id}/messages/", {
    "body": "第一条消息：你好！", "client_msg_id": "e2e-001",
}, token=alice_token, expect=201)
msg_id = sent["data"]["message"]["id"]
check("发送文本消息成功且 seq=1", sent["data"]["message"]["seq"] == 1)

_, retry = call("POST", f"/api/v1/conversations/{conv_id}/messages/", {
    "body": "第一条消息：你好！", "client_msg_id": "e2e-001",
}, token=alice_token, expect=200)
check("client_msg_id 幂等，重发不产生新消息", retry["data"]["created"] is False)

_, updates = call("POST", "/api/v1/updates/", {
    "cursor": bob_cursor, "timeout": 25,
}, token=bob_token, expect=200)
new_events = [e for e in updates["data"]["events"] if e["type"] == "message.new"]
check("对方长轮询立即收到 message.new", len(new_events) == 1 and new_events[0]["payload"]["body"].startswith("第一条消息"))
check("事件带正确会话号", new_events[0]["conversation_id"] == conv_id)
check("游标已推进", updates["data"]["cursor"] > bob_cursor)

_, convs = call("GET", "/api/v1/conversations/", token=bob_token, expect=200)
item = convs["data"]["conversations"][0]
check("接收方未读数为 1", item["unread_count"] == 1)
check("会话标题取对方昵称", item["title"] == "爱丽丝")
check("未读总数正确", convs["data"]["unread_total"] == 1)

# ---------------------------------------------------------------- 6. 已读
print("\n[6] 已读回执")
_, read = call("POST", f"/api/v1/conversations/{conv_id}/read/", {"up_to_seq": 1}, token=bob_token, expect=200)
check("标记已读后水位=1", read["data"]["last_read_seq"] == 1)
_, convs2 = call("GET", "/api/v1/conversations/", token=bob_token, expect=200)
check("已读后未读清零", convs2["data"]["conversations"][0]["unread_count"] == 0)

_, msgs = call("GET", f"/api/v1/conversations/{conv_id}/messages/", token=alice_token, expect=200)
reads = msgs["data"]["messages"][0]["reads"]
check("发送方可见对方已读回执", any(r["user_id"] == bob_id for r in reads))

# ---------------------------------------------------------------- 7. 输入状态
print("\n[7] 正在输入")
_, type_boot = call("POST", "/api/v1/updates/", {"cursor": 0, "timeout": 0}, token=bob_token, expect=200)
check("输入状态上报成功",
      call("POST", f"/api/v1/conversations/{conv_id}/typing/", {}, token=alice_token, expect=200)[0] == 200)
_, typing_updates = call("POST", "/api/v1/updates/", {
    "cursor": type_boot["data"]["cursor"], "timeout": 5,
}, token=bob_token, expect=200)
typing_events = [e for e in typing_updates["data"]["events"] if e["type"] == "typing"]
check("对方收到 typing 事件", len(typing_events) == 1 and typing_events[0]["payload"]["user_id"] == alice_id)

# ---------------------------------------------------------------- 8. 群聊
print("\n[8] 群聊")
_, group = call("POST", "/api/v1/conversations/", {
    "type": "group", "title": "验收群", "member_ids": [bob_id],
}, token=alice_token, expect=201)
group_id = group["data"]["conversation"]["id"]
check("建群成功且群主是我", group["data"]["conversation"]["my_role"] == "owner")
_, gdetail = call("GET", f"/api/v1/conversations/{group_id}/", token=alice_token, expect=200)
check("群成员数为 2", len(gdetail["data"]["members"]) == 2)
check("建群写入系统消息",
      any(m["kind"] == "system" for m in call(
          "GET", f"/api/v1/conversations/{group_id}/messages/", token=alice_token, expect=200
      )[1]["data"]["messages"]))

# ---------------------------------------------------------------- 9. 撤回
print("\n[9] 消息撤回")
_, revoke = call("POST", f"/api/v1/messages/{msg_id}/revoke/", {}, token=alice_token, expect=200)
check("发送者 2 分钟内可撤回", revoke["data"]["message"]["revoked"] is True)
check("撤回后正文被抹除", revoke["data"]["message"]["body"] == "")
_, twice = call("POST", f"/api/v1/messages/{msg_id}/revoke/", {}, token=alice_token, expect=200)
check("重复撤回幂等", twice["data"]["changed"] is False)
status, forbidden = call("POST", f"/api/v1/messages/{msg_id}/revoke/", {}, token=bob_token)
check("他人撤回被拒（403）", status == 403)

# ---------------------------------------------------------------- 10. 附件
print("\n[10] 图片上传与越权防护")
import os
import struct
import zlib


def make_png(width=40, height=30):
    """不依赖 Pillow 生成一张最小 PNG，确保脚本可在任意环境运行。"""
    def chunk(tag, data):
        payload = tag + data
        return struct.pack(">I", len(data)) + payload + struct.pack(">I", zlib.crc32(payload) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + b"\x07\xc1\x60" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


png_path = "_e2e_image.png"
with open(png_path, "wb") as handle:
    handle.write(make_png())

status, up = upload_image(png_path, alice_token)
check("图片上传成功（Pillow 真实解码）", status == 201 and up["data"]["attachment"]["is_image"] is True)
check("识别出图片尺寸 40x30",
      up["data"]["attachment"]["width"] == 40 and up["data"]["attachment"]["height"] == 30)
check("生成缩略图", up["data"]["attachment"]["thumb_url"] is not None)
attachment_id = up["data"]["attachment"]["id"]

_, img_msg = call("POST", f"/api/v1/conversations/{conv_id}/messages/", {
    "attachment_id": attachment_id,
}, token=alice_token, expect=201)
check("图片消息类型为 image", img_msg["data"]["message"]["kind"] == "image")

# 会话成员可下载
request = urllib.request.Request(
    f"{BASE}/api/v1/attachments/{attachment_id}/download/",
    headers={"Authorization": f"Bearer {bob_token}"},
)
with urllib.request.urlopen(request, timeout=20) as response:
    check("会话成员可下载附件", response.status == 200 and response.read(8) == b"\x89PNG\r\n\x1a\n")

# 非成员越权下载
_, outsider = call("POST", "/api/v1/auth/register/", {
    "username": f"eve{RUN}", "password": PASSWORD,
})
eve_token = outsider["data"]["token"]
status, denied = call("GET", f"/api/v1/attachments/{attachment_id}/download/", token=eve_token)
check("非成员下载被拒（404，不暴露存在性）", status == 404)

# 非成员读取会话
status, _ = call("GET", f"/api/v1/conversations/{conv_id}/messages/", token=eve_token)
check("非成员读取会话被拒（404）", status == 404)

os.remove(png_path)

# ---------------------------------------------------------------- 汇总
print("\n" + "=" * 66)
print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
if FAILED:
    print("失败项：")
    for name in FAILED:
        print(f"  - {name}")
    sys.exit(1)
print("全部验收通过 ✅")
print("=" * 66)
