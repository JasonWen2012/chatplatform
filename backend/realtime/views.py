"""实时层 HTTP 视图：长轮询事件流与在线状态。"""
import time

from django.conf import settings
from django.utils import timezone

from backend.accounts.api.api import ApiError, api_view, json_body, ok
from backend.accounts.models import User
from backend.realtime import stream


@api_view()
def updates(request, user=None):
    """POST /api/v1/updates/ — 长轮询拉取增量事件。

    请求：``{"cursor": 123, "timeout": 25}``
    响应：``{"cursor": 130, "events": [...], "timed_out": false}``

    语义：先立即取一次；无事件则挂起轮询，直到有新事件或超时。
    游标只增不减，客户端据此保证不漏不重。
    """
    if request.method != "POST":
        raise ApiError("method_not_allowed", "仅支持 POST", 405)

    body = json_body(request)
    raw_cursor = body.get("cursor")
    if raw_cursor in (None, ""):
        # 未提供游标时从当前水位开始，避免一次性回放历史事件
        cursor = stream.store.current_cursor()
    else:
        try:
            cursor = int(raw_cursor)
        except (TypeError, ValueError):
            raise ApiError("invalid_cursor", "cursor 必须是整数")
    if cursor < 0:
        raise ApiError("invalid_cursor", "cursor 不能为负数")

    max_timeout = getattr(settings, "LONGPOLL_TIMEOUT_SECONDS", 25)
    # 注意不能写成 `body.get("timeout") or max_timeout`：
    # 那样 timeout=0（要求立即返回）会被当成假值而回落到上限，导致请求被挂住。
    raw_timeout = body.get("timeout")
    if raw_timeout in (None, ""):
        raw_timeout = max_timeout
    try:
        timeout = int(raw_timeout)
    except (TypeError, ValueError):
        raise ApiError("invalid_timeout", "timeout 必须是整数")
    if timeout < 0:
        raise ApiError("invalid_timeout", "timeout 不能为负数")
    timeout = min(timeout, max_timeout)

    interval = getattr(settings, "LONGPOLL_POLL_INTERVAL", 0.5)
    stream.store.touch(user.id)
    _mark_online(user)

    deadline = time.monotonic() + timeout
    events = stream.store.fetch(user.id, cursor)

    while not events and time.monotonic() < deadline:
        time.sleep(interval)
        stream.store.touch(user.id)
        events = stream.store.fetch(user.id, cursor)

    if events:
        cursor = max(event["event_id"] for event in events)

    return ok(
        {
            "cursor": cursor,
            "events": events,
            "timed_out": not events,
        }
    )


def _mark_online(user):
    """把用户标记为在线（仅在状态变化时写库，避免每次轮询都写）。"""
    if not user.is_online:
        User.objects.filter(pk=user.pk).update(is_online=True, last_seen=timezone.now())
        user.is_online = True


@api_view()
def presence(request, user=None):
    """GET /api/v1/presence/ — 查询若干用户的在线状态。"""
    raw = request.GET.get("user_ids") or ""
    ids = [item for item in raw.split(",") if item.strip().isdigit()]
    if not ids:
        raise ApiError("missing_ids", "请提供 user_ids，例如 ?user_ids=1,2,3")
    ids = [int(item) for item in ids[:200]]

    online = stream.store.online_ids()
    users = User.objects.filter(id__in=ids)
    return ok(
        {
            "presence": [
                {
                    "user_id": item.id,
                    "is_online": item.id in online,
                    "last_seen": item.last_seen.isoformat(),
                }
                for item in users
            ]
        }
    )
