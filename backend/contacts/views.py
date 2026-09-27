"""联系人 API：好友搜索、好友申请流转、好友列表。"""
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from backend.accounts.api.api import ApiError, api_view, json_body, ok, user_brief
from backend.accounts.models import User

from .models import FriendRequest, Friendship


def _serialize_request(item, viewer_id):
    """好友申请序列化；viewer_id 用于标注方向，便于前端区分收/发。"""
    return {
        "id": item.id,
        "direction": "outgoing" if item.from_user_id == viewer_id else "incoming",
        "from_user": user_brief(item.from_user),
        "to_user": user_brief(item.to_user),
        "message": item.message,
        "status": item.status,
        "created": item.created.isoformat(),
        "handled_at": item.handled_at.isoformat() if item.handled_at else None,
    }


@api_view()
def friend_list(request, user=None):
    """GET /api/v1/friends/ — 好友列表（含在线状态）。"""
    friend_ids = Friendship.friend_ids(user.id)
    if not friend_ids:
        return ok({"friends": []})

    users = {u.id: u for u in User.objects.filter(id__in=friend_ids)}
    created_map = {
        row["user_high_id"] if row["user_low_id"] == user.id else row["user_low_id"]: row["created"]
        for row in Friendship.objects.filter(Q(user_low_id=user.id) | Q(user_high_id=user.id)).values(
            "user_low_id", "user_high_id", "created"
        )
    }
    friends = []
    for fid in sorted(friend_ids):
        target = users.get(fid)
        if target is None:
            continue  # 用户已被删除，关系行理论上会被级联清理
        entry = user_brief(target)
        entry["friends_since"] = created_map.get(fid).isoformat() if created_map.get(fid) else None
        friends.append(entry)
    return ok({"friends": friends})


@api_view()
def friend_requests(request, user=None):
    """GET  列出申请（incoming=收到的待处理，outgoing=我发出的）
    POST 发起好友申请
    """
    if request.method == "GET":
        direction = request.GET.get("direction", "incoming")
        queryset = FriendRequest.objects.select_related("from_user", "to_user")
        if direction == "outgoing":
            queryset = queryset.filter(from_user=user, status=FriendRequest.STATUS_PENDING)
        else:
            queryset = queryset.filter(to_user=user, status=FriendRequest.STATUS_PENDING)
        return ok({"requests": [_serialize_request(item, user.id) for item in queryset[:100]]})

    if request.method != "POST":
        raise ApiError("method_not_allowed", "仅支持 GET/POST", 405)

    body = json_body(request)
    target_id = body.get("user_id")
    note = (body.get("message") or "").strip()
    if len(note) > 140:
        raise ApiError("invalid_message", "验证消息不能超过 140 个字符")

    target = User.objects.filter(pk=target_id).first()
    if target is None:
        raise ApiError("user_not_found", "用户不存在", 404)
    if target.id == user.id:
        raise ApiError("self_request", "不能添加自己为好友")
    if Friendship.are_friends(user.id, target.id):
        raise ApiError("already_friends", "你们已经是好友了", 409)

    # 对方已向我发起申请 → 直接互相成为好友，符合微信式直觉
    inverse = FriendRequest.objects.filter(
        from_user=target, to_user=user, status=FriendRequest.STATUS_PENDING
    ).first()
    if inverse is not None:
        with transaction.atomic():
            inverse.status = FriendRequest.STATUS_ACCEPTED
            inverse.handled_at = timezone.now()
            inverse.save(update_fields=["status", "handled_at"])
            _create_friendship(user.id, target.id)
        return ok({"detail": "对方已向你发起申请，已直接成为好友", "auto_accepted": True,
                   "friend": user_brief(target)})

    if FriendRequest.objects.filter(
        from_user=user, to_user=target, status=FriendRequest.STATUS_PENDING
    ).exists():
        raise ApiError("request_pending", "已发送过申请，请等待对方处理", 409)

    item = FriendRequest.objects.create(from_user=user, to_user=target, message=note)
    _notify_friend_request(item)
    return ok({"request": _serialize_request(item, user.id)}, status=201)


def _create_friendship(a_id, b_id):
    """幂等地建立好友关系。"""
    low, high = Friendship.pair(a_id, b_id)
    try:
        Friendship.objects.get_or_create(user_low_id=low, user_high_id=high)
    except IntegrityError:  # 并发下唯一约束兜底
        pass


def _notify_friend_request(item):
    """把申请推给收件人的实时事件流（实时层不可用时静默降级）。"""
    try:
        from backend.realtime.stream import publish

        publish(
            item.to_user_id,
            "friend.request",
            {
                "request_id": item.id,
                "from_user": user_brief(item.from_user),
                "message": item.message,
            },
        )
    except Exception:  # pragma: no cover - 实时层故障不应阻断业务写入
        pass


@api_view()
def handle_request(request, request_id, action, user=None):
    """POST /api/v1/friend-requests/<id>/<accept|reject>/ — 仅收件人可处理。"""
    if request.method != "POST":
        raise ApiError("method_not_allowed", "仅支持 POST", 405)
    if action not in {"accept", "reject"}:
        raise ApiError("invalid_action", "仅支持 accept 或 reject", 404)

    item = FriendRequest.objects.select_related("from_user", "to_user").filter(pk=request_id).first()
    if item is None:
        raise ApiError("not_found", "申请不存在", 404)
    if item.to_user_id != user.id:
        # 非收件人一律 404，不暴露申请是否存在
        raise ApiError("not_found", "申请不存在", 404)
    if item.status != FriendRequest.STATUS_PENDING:
        raise ApiError("already_handled", "该申请已被处理", 409)

    with transaction.atomic():
        item.status = (
            FriendRequest.STATUS_ACCEPTED if action == "accept" else FriendRequest.STATUS_REJECTED
        )
        item.handled_at = timezone.now()
        item.save(update_fields=["status", "handled_at"])
        if action == "accept":
            _create_friendship(item.from_user_id, item.to_user_id)

    if action == "accept":
        _notify_friendship(item.from_user_id, user)
    return ok({"request": _serialize_request(item, user.id)})


def _notify_friendship(other_id, me):
    try:
        from backend.realtime.stream import publish

        publish(other_id, "friend.accepted", {"friend": user_brief(me)})
    except Exception:  # pragma: no cover
        pass


@api_view()
def friend_detail(request, friend_id, user=None):
    """DELETE /api/v1/friends/<id>/ — 删除好友（双向解除，不删历史消息）。"""
    if request.method != "DELETE":
        raise ApiError("method_not_allowed", "仅支持 DELETE", 405)
    low, high = Friendship.pair(user.id, friend_id)
    deleted, _ = Friendship.objects.filter(user_low_id=low, user_high_id=high).delete()
    if not deleted:
        raise ApiError("not_friends", "你们不是好友", 404)
    # 清理双方之间残留的待处理申请，避免删好友后立刻「自动复合」
    FriendRequest.objects.filter(
        Q(from_user_id=user.id, to_user_id=friend_id) | Q(from_user_id=friend_id, to_user_id=user.id),
        status=FriendRequest.STATUS_PENDING,
    ).update(status=FriendRequest.STATUS_REJECTED, handled_at=timezone.now())
    return ok({"detail": "已删除好友"})
