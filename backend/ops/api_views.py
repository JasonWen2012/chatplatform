"""管理员 API（``/api/v1/ops/``）。

所有视图使用 ``api_view(admin_only=True)``，非管理员一律 403。
除列表查询外，每个写操作都会向 ``AdminActionLog`` 落一条审计记录。
"""
from datetime import timedelta

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from backend.accounts.api.api import ApiError, api_view, json_body, ok, user_brief
from backend.accounts.models import Token, User
from backend.chat.models import (
    Attachment,
    Conversation,
    Membership,
    Message,
    MessageReport,
)
from backend.chat.serializers import (
    admin_observer_membership,
    serialize_attachment,
    serialize_conversation,
    serialize_member,
    serialize_message,
)
from backend.realtime import stream
from backend.ops.models import (
    ACTION_MESSAGE_REMOVE,
    ACTION_REPORT_DISMISS,
    ACTION_REPORT_RESOLVE,
    ACTION_USER_BAN,
    ACTION_USER_REVOKE_TOKENS,
    ACTION_USER_UNBAN,
    AdminActionLog,
    log_action,
)
from backend.ops.pagination import clamp_query, page_meta, paginate

TREND_DAYS = 7
MESSAGE_PREVIEW_LIMIT = 200


# ---------------------------------------------------------------- 序列化辅助
def _ops_user(user, message_count=None, conversation_count=None):
    data = user_brief(user)
    data.update(
        {
            "is_active": user.is_active,
            "is_staff": user.is_staff,
            "is_superuser": user.is_superuser,
            "date_joined": user.date_joined.isoformat(),
            "last_login": user.last_login.isoformat() if user.last_login else None,
        }
    )
    if message_count is not None:
        data["message_count"] = message_count
    if conversation_count is not None:
        data["conversation_count"] = conversation_count
    return data


def _ops_conversation(conversation, member_count=None, message_count=None):
    members = list(conversation.memberships.select_related("user")[:1])
    observer = members[0] if members else None
    return {
        "id": conversation.pk,
        "type": conversation.type,
        "title": conversation.title or f"会话 {conversation.pk}",
        "owner_id": conversation.owner_id,
        "created": conversation.created.isoformat(),
        "last_message_at": conversation.last_message_at.isoformat(),
        "last_seq": conversation.last_seq,
        "member_count": member_count if member_count is not None else conversation.memberships.count(),
        "message_count": message_count,
    }


# ---------------------------------------------------------------- 概览
@api_view(admin_only=True)
def overview(request, user=None):
    """GET /api/v1/ops/overview/ — 关键指标与近 7 日消息趋势。"""
    now = timezone.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    trend_start = today_start - timedelta(days=TREND_DAYS - 1)

    total_users = User.objects.count()
    active_users = User.objects.filter(is_active=True).count()
    staff_users = User.objects.filter(is_staff=True).count()

    # 在线状态来自实时层内存表，比数据库字段更贴近「此刻」
    online_ids = stream.store.online_ids()

    conversations = Conversation.objects.count()
    messages_today = Message.objects.filter(created_at__gte=today_start).count()
    messages_total = Message.objects.count()
    attachments_total = Attachment.objects.count()
    pending_reports = MessageReport.objects.filter(status=MessageReport.STATUS_PENDING).count()

    # 近 7 日趋势：在 Python 中按天归并，避免依赖各数据库的日期截断函数
    buckets = {}
    for offset in range(TREND_DAYS):
        day = (trend_start + timedelta(days=offset)).date()
        buckets[day] = 0
    for created_at in Message.objects.filter(created_at__gte=trend_start).values_list(
        "created_at", flat=True
    ):
        day = timezone.localtime(created_at).date()
        if day in buckets:
            buckets[day] += 1

    top_conversations = [
        _ops_conversation(row, member_count=row.member_count, message_count=row.message_count)
        for row in Conversation.objects.annotate(
            member_count=Count("memberships", distinct=True),
            message_count=Count("messages", distinct=True),
        ).order_by("-message_count")[:5]
    ]

    return ok(
        {
            "users": {
                "total": total_users,
                "active": active_users,
                "banned": total_users - active_users,
                "staff": staff_users,
                "online": len(online_ids),
            },
            "conversations": conversations,
            "messages": {"total": messages_total, "today": messages_today},
            "attachments": attachments_total,
            "reports": {"pending": pending_reports},
            "trend": [
                {"date": day.isoformat(), "count": count} for day, count in buckets.items()
            ],
            "top_conversations": top_conversations,
        }
    )


# ---------------------------------------------------------------- 用户管理
@api_view(admin_only=True)
def users(request, user=None):
    """GET /api/v1/ops/users/ — 用户列表（含消息数、会话数）。"""
    keyword = clamp_query(request)
    status = (request.GET.get("status") or "").strip()

    queryset = User.objects.all()
    if keyword:
        queryset = queryset.filter(Q(username__icontains=keyword) | Q(nickname__icontains=keyword))
    if status == "active":
        queryset = queryset.filter(is_active=True)
    elif status == "banned":
        queryset = queryset.filter(is_active=False)
    elif status == "staff":
        queryset = queryset.filter(is_staff=True)
    elif status:
        raise ApiError("invalid_status", "status 仅支持 active / banned / staff")

    queryset = queryset.annotate(
        message_count=Count("sent_messages", distinct=True),
        conversation_count=Count("memberships", distinct=True),
    ).order_by("-id")

    page, page_size, total, rows = paginate(request, queryset)
    return ok(
        {
            "users": [
                _ops_user(row, row.message_count, row.conversation_count) for row in rows
            ],
            "pagination": page_meta(page, page_size, total),
        }
    )


def _load_user(user_id):
    target = User.objects.filter(pk=user_id).first()
    if target is None:
        raise ApiError("user_not_found", "用户不存在", 404)
    return target


@api_view(admin_only=True)
def user_detail(request, user_id, user=None):
    """PATCH /api/v1/ops/users/<id>/ — 禁用 / 解禁。"""
    target = _load_user(user_id)
    if request.method != "PATCH":
        raise ApiError("method_not_allowed", "仅支持 PATCH", 405)

    body = json_body(request)
    if "is_active" not in body:
        raise ApiError("missing_field", "请提供 is_active")

    want_active = bool(body["is_active"])
    if not want_active:
        # 防自锁：管理员禁用自己会导致再无人能进入管理界面
        if target.id == user.id:
            raise ApiError("self_ban", "不能禁用自己的账号", 400)

    if target.is_active != want_active:
        with transaction.atomic():
            target.is_active = want_active
            target.save(update_fields=["is_active"])
            revoked = 0
            if not want_active:
                # 禁用即刻生效：撤销全部令牌并踢掉现有会话
                revoked = Token.objects.filter(user=target, revoked=False).update(revoked=True)
        log_action(
            user,
            ACTION_USER_UNBAN if want_active else ACTION_USER_BAN,
            target_type="user",
            target_id=target.id,
            detail=f"{'解禁' if want_active else '禁用'}账号 {target.username}"
            + (f"，同时撤销 {revoked} 个令牌" if revoked else ""),
        )
    return ok({"user": _ops_user(target)})


@api_view(admin_only=True)
def user_revoke_tokens(request, user_id, user=None):
    """POST /api/v1/ops/users/<id>/revoke-tokens/ — 强制下线所有设备。"""
    target = _load_user(user_id)
    if request.method != "POST":
        raise ApiError("method_not_allowed", "仅支持 POST", 405)

    with transaction.atomic():
        revoked = Token.objects.filter(user=target, revoked=False).update(revoked=True)
        # 清空会话表，确保基于 Cookie 的登录也一并失效
        from django.contrib.sessions.models import Session

        for session in Session.objects.filter(expire_date__gte=timezone.now()):
            try:
                data = session.get_decoded()
            except Exception:
                continue
            if str(target.id) == str(data.get("_auth_user_id")):
                session.delete()

    log_action(
        user,
        ACTION_USER_REVOKE_TOKENS,
        target_type="user",
        target_id=target.id,
        detail=f"强制下线 {target.username}，撤销 {revoked} 个令牌",
    )
    return ok({"detail": f"已强制下线 {target.username}", "revoked_tokens": revoked})


# ---------------------------------------------------------------- 会话浏览
@api_view(admin_only=True)
def conversations(request, user=None):
    """GET /api/v1/ops/conversations/ — 会话列表。"""
    keyword = clamp_query(request)
    conv_type = (request.GET.get("type") or "").strip()

    queryset = Conversation.objects.annotate(
        member_count=Count("memberships", distinct=True),
        message_count=Count("messages", distinct=True),
    )
    if conv_type:
        if conv_type not in {Conversation.TYPE_DIRECT, Conversation.TYPE_GROUP}:
            raise ApiError("invalid_type", "type 仅支持 direct / group")
        queryset = queryset.filter(type=conv_type)
    if keyword:
        queryset = queryset.filter(
            Q(title__icontains=keyword) | Q(memberships__user__nickname__icontains=keyword)
        ).distinct()

    queryset = queryset.order_by("-last_message_at")
    page, page_size, total, rows = paginate(request, queryset)
    return ok(
        {
            "conversations": [
                _ops_conversation(row, row.member_count, row.message_count) for row in rows
            ],
            "pagination": page_meta(page, page_size, total),
        }
    )


@api_view(admin_only=True)
def conversation_detail(request, conversation_id, user=None):
    """GET /api/v1/ops/conversations/<id>/ — 会话详情、成员与最近消息。"""
    conversation = Conversation.objects.filter(pk=conversation_id).first()
    if conversation is None:
        raise ApiError("not_found", "会话不存在", 404)

    members = list(conversation.memberships.select_related("user"))
    recent = list(
        Message.objects.filter(conversation=conversation)
        .select_related("sender", "attachment", "reply_to")
        .order_by("-seq")[:50]
    )
    recent.reverse()

    observer = admin_observer_membership(conversation, user)
    return ok(
        {
            "conversation": serialize_conversation(
                conversation, observer, memberships=members
            ),
            "members": [serialize_member(row) for row in members],
            "messages": [
                serialize_message(message, viewer_is_admin=True) for message in recent
            ],
        }
    )


# ---------------------------------------------------------------- 消息检索与移除
@api_view(admin_only=True)
def messages(request, user=None):
    """GET /api/v1/ops/messages/ — 跨会话消息检索。"""
    keyword = clamp_query(request)
    conversation_id = request.GET.get("conversation")
    sender_id = request.GET.get("sender")

    queryset = Message.objects.select_related(
        "sender", "attachment", "conversation", "reply_to"
    )
    if keyword:
        queryset = queryset.filter(body__icontains=keyword)
    if conversation_id:
        if not str(conversation_id).isdigit():
            raise ApiError("invalid_conversation", "conversation 必须是会话 id")
        queryset = queryset.filter(conversation_id=int(conversation_id))
    if sender_id:
        if not str(sender_id).isdigit():
            raise ApiError("invalid_sender", "sender 必须是用户 id")
        queryset = queryset.filter(sender_id=int(sender_id))

    queryset = queryset.order_by("-created_at")
    page, page_size, total, rows = paginate(request, queryset)
    return ok(
        {
            "messages": [
                {
                    **serialize_message(row, viewer_is_admin=True),
                    "conversation_title": row.conversation.title
                    or f"会话 {row.conversation_id}",
                    "conversation_type": row.conversation.type,
                }
                for row in rows
            ],
            "pagination": page_meta(page, page_size, total),
        }
    )


@api_view(admin_only=True)
def message_remove(request, message_id, user=None):
    """POST /api/v1/ops/messages/<id>/remove/ — 移除消息（软删除 + 广播）。"""
    if request.method != "POST":
        raise ApiError("method_not_allowed", "仅支持 POST", 405)

    message = (
        Message.objects.select_related("conversation", "sender", "attachment")
        .filter(pk=message_id)
        .first()
    )
    if message is None:
        raise ApiError("not_found", "消息不存在", 404)
    if not message.can_remove():
        # 幂等：已移除或已撤回都视为终态，但明确告知原因
        reason = "已移除" if message.is_removed else "已撤回"
        raise ApiError("not_removable", f"该消息{reason}，无需重复处理", 409)

    body = json_body(request)
    reason_text = (body.get("reason") or "").strip()[:100]

    with transaction.atomic():
        # 与撤回一致：正文与附件指针必须真正清空，不能只靠前端隐藏
        Message.objects.filter(pk=message.pk).update(
            removed_at=timezone.now(),
            removed_by=user,
            body="",
            attachment=None,
        )
        message.refresh_from_db(fields=["removed_at", "removed_by", "body", "attachment"])

    log_action(
        user,
        ACTION_MESSAGE_REMOVE,
        target_type="message",
        target_id=message.pk,
        detail=f"移除会话 {message.conversation_id} 中 seq={message.seq} 的消息"
        + (f"；理由：{reason_text}" if reason_text else ""),
    )

    # 广播给会话成员，让所有端实时变为「消息已被移除」
    member_ids = list(
        Membership.objects.filter(conversation_id=message.conversation_id).values_list(
            "user_id", flat=True
        )
    )
    if member_ids:
        stream.publish_many(
            member_ids,
            "message.removed",
            {
                "message": serialize_message(message, viewer_is_admin=False),
                "conversation_id": message.conversation_id,
                "removed_by": user.id,
            },
            message.conversation_id,
        )

    return ok({"message": serialize_message(message, viewer_is_admin=True)})


# ---------------------------------------------------------------- 举报处理
def _serialize_report(report):
    message = report.message
    return {
        "id": report.pk,
        "status": report.status,
        "reason": report.reason,
        "created": report.created.isoformat(),
        "handled_at": report.handled_at.isoformat() if report.handled_at else None,
        "handled_by": user_brief(report.handled_by) if report.handled_by_id else None,
        "reporter": user_brief(report.reporter),
        "message": {
            **serialize_message(message, viewer_is_admin=True),
            "conversation_id": message.conversation_id,
        },
    }


def _load_report(report_id):
    report = (
        MessageReport.objects.select_related(
            "reporter", "handled_by", "message", "message__sender", "message__conversation"
        )
        .filter(pk=report_id)
        .first()
    )
    if report is None:
        raise ApiError("not_found", "举报不存在", 404)
    return report


@api_view(admin_only=True)
def reports(request, user=None):
    """GET /api/v1/ops/reports/ — 举报列表。"""
    status = (request.GET.get("status") or "").strip()
    queryset = MessageReport.objects.select_related(
        "reporter", "handled_by", "message", "message__sender", "message__conversation"
    )
    if status:
        allowed = {
            MessageReport.STATUS_PENDING,
            MessageReport.STATUS_RESOLVED,
            MessageReport.STATUS_DISMISSED,
        }
        if status not in allowed:
            raise ApiError("invalid_status", "status 仅支持 pending / resolved / dismissed")
        queryset = queryset.filter(status=status)

    page, page_size, total, rows = paginate(request, queryset.order_by("-created"))
    pending_total = MessageReport.objects.filter(status=MessageReport.STATUS_PENDING).count()
    return ok(
        {
            "reports": [_serialize_report(row) for row in rows],
            "pending_total": pending_total,
            "pagination": page_meta(page, page_size, total),
        }
    )


@api_view(admin_only=True)
def report_action(request, report_id, action, user=None):
    """POST /api/v1/ops/reports/<id>/resolve|dismiss/ — 处理举报。

    ``resolve`` 可同时移除被举报消息（``remove_message`` 默认 true）；
    ``dismiss`` 只改状态，绝不动消息。
    """
    if request.method != "POST":
        raise ApiError("method_not_allowed", "仅支持 POST", 405)
    if action not in {"resolve", "dismiss"}:
        raise ApiError("invalid_action", "仅支持 resolve 或 dismiss", 404)

    report = _load_report(report_id)
    if report.status != MessageReport.STATUS_PENDING:
        raise ApiError("already_handled", "该举报已被处理", 409)

    body = json_body(request)
    remove_message = bool(body.get("remove_message", action == "resolve"))
    removed = False
    if action == "resolve" and remove_message and report.message.can_remove():
        target = report.message
        with transaction.atomic():
            Message.objects.filter(pk=target.pk).update(
                removed_at=timezone.now(), removed_by=user, body="", attachment=None
            )
        removed = True
        log_action(
            user,
            ACTION_MESSAGE_REMOVE,
            target_type="message",
            target_id=target.pk,
            detail=f"处理举报时移除消息（会话 {target.conversation_id} seq={target.seq}）",
        )
        member_ids = list(
            Membership.objects.filter(conversation_id=target.conversation_id).values_list(
                "user_id", flat=True
            )
        )
        if member_ids:
            target.refresh_from_db(fields=["removed_at", "removed_by", "body", "attachment"])
            stream.publish_many(
                member_ids,
                "message.removed",
                {
                    "message": serialize_message(target, viewer_is_admin=False),
                    "conversation_id": target.conversation_id,
                    "removed_by": user.id,
                },
                target.conversation_id,
            )

    new_status = (
        MessageReport.STATUS_RESOLVED if action == "resolve" else MessageReport.STATUS_DISMISSED
    )
    with transaction.atomic():
        MessageReport.objects.filter(pk=report.pk).update(
            status=new_status, handled_at=timezone.now(), handled_by=user
        )
    report.refresh_from_db()

    log_action(
        user,
        ACTION_REPORT_RESOLVE if action == "resolve" else ACTION_REPORT_DISMISS,
        target_type="report",
        target_id=report.pk,
        detail=f"{'结案' if action == 'resolve' else '驳回'}举报 #{report.pk}"
        + ("，并移除消息" if removed else ""),
    )
    return ok({"report": _serialize_report(report), "message_removed": removed})


# ---------------------------------------------------------------- 操作日志
@api_view(admin_only=True)
def audit(request, user=None):
    """GET /api/v1/ops/audit/ — 管理员操作日志。"""
    action = (request.GET.get("action") or "").strip()
    queryset = AdminActionLog.objects.select_related("actor")
    if action:
        queryset = queryset.filter(action=action)

    page, page_size, total, rows = paginate(request, queryset.order_by("-created"))
    return ok(
        {
            "logs": [
                {
                    "id": row.pk,
                    "action": row.action,
                    "action_label": row.get_action_display(),
                    "actor": user_brief(row.actor) if row.actor_id else None,
                    "target_type": row.target_type,
                    "target_id": row.target_id,
                    "detail": row.detail,
                    "created": row.created.isoformat(),
                }
                for row in rows
            ],
            "pagination": page_meta(page, page_size, total),
        }
    )
