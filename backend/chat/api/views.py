"""聊天 API：会话管理、消息收发、撤回、已读、输入状态、附件。"""
import hashlib

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse, Http404
from django.utils import timezone
from PIL import Image

from backend.accounts.api.api import (
    ApiError,
    api_view,
    json_body,
    ok,
    user_brief,
    user_is_admin,
)
from backend.accounts.models import User
from backend.contacts.models import Friendship
from backend.realtime import stream

from backend.chat import services
from backend.chat.models import (
    Attachment,
    Conversation,
    Membership,
    Message,
    MessageRead,
    MessageReport,
)
from backend.chat.serializers import (
    admin_observer_membership,
    direct_peer,
    serialize_attachment,
    serialize_conversation,
    serialize_member,
    serialize_message,
)

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


# ---------------------------------------------------------------- 权限辅助
def get_membership(conversation_id, user, with_conversation=True, allow_admin=False):
    """取成员关系；不是成员一律抛 404，避免泄露会话是否存在。

    ``allow_admin=True`` 时，服务器管理员（``is_staff``）即使不是成员也放行，
    返回 ``None`` 表示「管理员旁路访问」。

    **仅限读路径使用**：发消息、撤回、已读、加/踢成员等写操作必须保持真实成员身份，
    否则管理员就能冒名发言。是否旁路由调用方显式声明，不做隐式判断。
    """
    queryset = Membership.objects.select_related("conversation", "user")
    membership = queryset.filter(conversation_id=conversation_id, user=user).first()
    if membership is None:
        if allow_admin and user_is_admin(user):
            # 会话必须真实存在，否则依然 404
            if Conversation.objects.filter(pk=conversation_id).exists():
                return None
        raise ApiError("not_found", "会话不存在或无权访问", 404)
    return membership


def get_conversation(conversation_id, user):
    return get_membership(conversation_id, user).conversation


def ensure_member_ids_exist(user_ids):
    unique = list(dict.fromkeys(int(uid) for uid in user_ids))
    found = set(User.objects.filter(id__in=unique).values_list("id", flat=True))
    missing = [uid for uid in unique if uid not in found]
    if missing:
        raise ApiError("user_not_found", f"用户不存在: {missing}", 404)
    return unique


# ---------------------------------------------------------------- 会话列表
@api_view()
def conversations(request, user=None):
    """GET  会话列表（含未读数与最后一条消息）
    POST 创建会话：单聊幂等复用，群聊新建
    """
    if request.method == "GET":
        memberships = list(
            Membership.objects.filter(user=user)
            .select_related("conversation")
            .order_by("-is_pinned", "-conversation__last_message_at")
        )
        if not memberships:
            return ok({"conversations": [], "unread_total": 0})

        conversation_ids = [m.conversation_id for m in memberships]
        # 一次性取回所有会话的最后一条消息，避免逐会话查询
        last_messages = {}
        for message in (
            Message.objects.filter(conversation_id__in=conversation_ids)
            .select_related("sender", "attachment")
            .order_by("conversation_id", "-seq")
        ):
            last_messages.setdefault(message.conversation_id, message)

        # 一次性取回所有会话的成员，供单聊取名与在线统计
        members_by_conversation = {}
        for row in (
            Membership.objects.filter(conversation_id__in=conversation_ids).select_related("user")
        ):
            members_by_conversation.setdefault(row.conversation_id, []).append(row)

        items = [
            serialize_conversation(
                membership.conversation,
                membership,
                last_message=last_messages.get(membership.conversation_id),
                memberships=members_by_conversation.get(membership.conversation_id, []),
            )
            for membership in memberships
        ]
        return ok({"conversations": items, "unread_total": services.unread_total(user)})

    if request.method != "POST":
        raise ApiError("method_not_allowed", "仅支持 GET/POST", 405)

    body = json_body(request)
    conv_type = body.get("type") or Conversation.TYPE_DIRECT

    if conv_type == Conversation.TYPE_DIRECT:
        return _create_direct(user, body)
    if conv_type == Conversation.TYPE_GROUP:
        return _create_group(user, body)
    raise ApiError("invalid_type", "会话类型只能是 direct 或 group")


def _create_direct(user, body):
    """单聊创建：必须已是好友；已存在则直接返回，保证幂等。"""
    peer_id = body.get("peer_id") or body.get("user_id")
    if not peer_id:
        raise ApiError("missing_peer", "缺少 peer_id")
    peer = User.objects.filter(pk=peer_id).first()
    if peer is None:
        raise ApiError("user_not_found", "用户不存在", 404)
    if peer.id == user.id:
        raise ApiError("self_conversation", "不能和自己发起会话")
    if not Friendship.are_friends(user.id, peer.id):
        raise ApiError("not_friends", "需要先成为好友才能发起会话", 403)

    # 找出同时包含两人的单聊
    existing = (
        Conversation.objects.filter(type=Conversation.TYPE_DIRECT, memberships__user=user)
        .filter(memberships__user=peer)
        .distinct()
        .first()
    )
    if existing is not None:
        membership = Membership.objects.get(conversation=existing, user=user)
        return ok(
            {
                "conversation": serialize_conversation(existing, membership, memberships=None),
                "created": False,
            }
        )

    with transaction.atomic():
        conversation = Conversation.objects.create(type=Conversation.TYPE_DIRECT)
        Membership.objects.bulk_create(
            [
                Membership(conversation=conversation, user=user, role=Membership.ROLE_MEMBER),
                Membership(conversation=conversation, user=peer, role=Membership.ROLE_MEMBER),
            ]
        )
    membership = Membership.objects.get(conversation=conversation, user=user)
    return ok(
        {"conversation": serialize_conversation(conversation, membership), "created": True},
        status=201,
    )


def _create_group(user, body):
    title = (body.get("title") or "").strip()
    member_ids = body.get("member_ids") or []
    if not title:
        raise ApiError("missing_title", "请提供群名称")
    if len(title) > 64:
        raise ApiError("invalid_title", "群名称不能超过 64 个字符")
    if not isinstance(member_ids, list):
        raise ApiError("invalid_members", "member_ids 必须是数组")
    if len(member_ids) > 200:
        raise ApiError("too_many_members", "单个群成员上限为 200 人")

    others = [uid for uid in ensure_member_ids_exist(member_ids) if uid != user.id]

    with transaction.atomic():
        conversation = Conversation.objects.create(
            type=Conversation.TYPE_GROUP, title=title, owner=user
        )
        Membership.objects.bulk_create(
            [Membership(conversation=conversation, user=user, role=Membership.ROLE_OWNER)]
            + [
                Membership(conversation=conversation, user_id=uid, role=Membership.ROLE_MEMBER)
                for uid in others
            ]
        )
    services.create_system_message(conversation, f"{user.display_name} 创建了群聊「{title}」", actor=user)

    membership = Membership.objects.get(conversation=conversation, user=user)
    return ok(
        {"conversation": serialize_conversation(conversation, membership), "created": True},
        status=201,
    )


# ---------------------------------------------------------------- 会话详情
@api_view()
def conversation_detail(request, conversation_id, user=None):
    """GET   会话详情（含成员列表）
    PATCH 修改群名（群主/管理员）或置顶、免打扰

    GET 允许服务器管理员旁路读取（不是成员也能查看，用于治理）；
    PATCH 必须真实成员，且需群主/群管理员身份。
    """
    if request.method == "GET":
        membership = get_membership(conversation_id, user, allow_admin=True)
        conversation = (
            membership.conversation
            if membership is not None
            else Conversation.objects.get(pk=conversation_id)
        )
        if membership is None:
            # 管理员查看非本人会话属于敏感读取，留存审计
            from backend.ops.models import ACTION_CONVERSATION_VIEW, log_action

            log_action(
                user,
                ACTION_CONVERSATION_VIEW,
                target_type="conversation",
                target_id=conversation.pk,
                detail=f"管理员查看会话「{conversation.title or conversation.pk}」",
            )
            membership = admin_observer_membership(conversation, user)

        members = list(conversation.memberships.select_related("user"))
        return ok(
            {
                "conversation": serialize_conversation(
                    conversation, membership, memberships=members
                ),
                "members": [serialize_member(row) for row in members],
                "admin_observer": membership.role == "observer",
            }
        )

    membership = get_membership(conversation_id, user)
    conversation = membership.conversation

    if request.method == "PATCH":
        body = json_body(request)
        conv_fields = []
        my_fields = []

        if "title" in body:
            if conversation.type != Conversation.TYPE_GROUP:
                raise ApiError("not_group", "单聊不支持修改名称")
            if not membership.is_manager:
                raise ApiError("forbidden", "只有群主或管理员可以修改群名", 403)
            title = (body.get("title") or "").strip()
            if not title:
                raise ApiError("invalid_title", "群名称不能为空")
            if len(title) > 64:
                raise ApiError("invalid_title", "群名称不能超过 64 个字符")
            conversation.title = title
            conv_fields.append("title")

        if "is_muted" in body:
            membership.is_muted = bool(body["is_muted"])
            my_fields.append("is_muted")
        if "is_pinned" in body:
            membership.is_pinned = bool(body["is_pinned"])
            my_fields.append("is_pinned")

        with transaction.atomic():
            if conv_fields:
                conversation.save(update_fields=conv_fields)
            if my_fields:
                membership.save(update_fields=my_fields)

        members = list(conversation.memberships.select_related("user"))
        if conv_fields:
            stream.publish_many(
                [row.user_id for row in members],
                stream.EVENT_CONVERSATION_UPDATED,
                {"conversation_id": conversation.pk, "title": conversation.title},
                conversation.pk,
            )
        return ok(
            {
                "conversation": serialize_conversation(
                    conversation, membership, memberships=members
                )
            }
        )

    raise ApiError("method_not_allowed", "仅支持 GET/PATCH", 405)


@api_view()
def conversation_members(request, conversation_id, user=None):
    """POST 添加成员（仅群聊，需管理员）"""
    membership = get_membership(conversation_id, user)
    conversation = membership.conversation
    if conversation.type != Conversation.TYPE_GROUP:
        raise ApiError("not_group", "单聊不支持添加成员")
    if not membership.is_manager:
        raise ApiError("forbidden", "只有群主或管理员可以添加成员", 403)
    if request.method != "POST":
        raise ApiError("method_not_allowed", "仅支持 POST", 405)

    body = json_body(request)
    add_ids = [
        uid
        for uid in ensure_member_ids_exist(body.get("member_ids") or [])
        if not Membership.objects.filter(conversation=conversation, user_id=uid).exists()
    ]
    if not add_ids:
        raise ApiError("no_new_members", "没有需要添加的新成员")

    existing_count = conversation.memberships.count()
    if existing_count + len(add_ids) > 200:
        raise ApiError("too_many_members", "单个群成员上限为 200 人")

    with transaction.atomic():
        Membership.objects.bulk_create(
            [Membership(conversation=conversation, user_id=uid) for uid in add_ids]
        )
    names = ", ".join(
        User.objects.filter(id__in=add_ids).values_list("nickname", flat=True)
    ) or f"{len(add_ids)} 人"
    services.create_system_message(conversation, f"{user.display_name} 邀请 {names} 加入群聊", actor=user)

    members = list(conversation.memberships.select_related("user"))
    return ok({"members": [serialize_member(row) for row in members]})


@api_view()
def member_detail(request, conversation_id, member_id, user=None):
    """DELETE 移除成员（管理员踢人，或成员自退）"""
    membership = get_membership(conversation_id, user)
    conversation = membership.conversation
    if request.method != "DELETE":
        raise ApiError("method_not_allowed", "仅支持 DELETE", 405)

    target = Membership.objects.filter(conversation=conversation, user_id=member_id).first()
    if target is None:
        raise ApiError("not_found", "该用户不在会话中", 404)

    is_self = target.user_id == user.id
    if not is_self and not membership.is_manager:
        raise ApiError("forbidden", "只有群主或管理员可以移除成员", 403)
    if target.role == Membership.ROLE_OWNER and not is_self:
        raise ApiError("forbidden", "不能移除群主", 403)

    target_name = target.user.display_name
    with transaction.atomic():
        target.delete()
        if not is_self:
            services.create_system_message(
                conversation, f"{target_name} 被 {user.display_name} 移出群聊", actor=user
            )

    remaining = conversation.memberships.select_related("user")
    if not remaining.exists():
        conversation.delete()
        return ok({"detail": "成员已移除，会话已无成员故一并删除"})

    # 群主退出则顺位移交，避免出现无人管理的群
    new_owner_id = None
    if target.role == Membership.ROLE_OWNER:
        successor = remaining.order_by("joined_at").first()
        if successor is not None:
            conversation.owner = successor.user
            conversation.save(update_fields=["owner"])
            successor.role = Membership.ROLE_OWNER
            successor.save(update_fields=["role"])
            new_owner_id = successor.user_id
            services.create_system_message(
                conversation, f"{successor.user.display_name} 成为新群主", actor=None
            )

    return ok({"detail": "已退出会话" if is_self else "已移除成员", "new_owner_id": new_owner_id})


@api_view()
def conversation_leave(request, conversation_id, user=None):
    """POST 退出会话（单聊直接删除会话）。"""
    membership = get_membership(conversation_id, user)
    conversation = membership.conversation
    if request.method != "POST":
        raise ApiError("method_not_allowed", "仅支持 POST", 405)

    with transaction.atomic():
        was_owner = membership.role == Membership.ROLE_OWNER
        membership.delete()
        if conversation.type == Conversation.TYPE_DIRECT:
            # 单聊一方退出即整段清理，历史消息随会话级联删除
            conversation.delete()
            return ok({"detail": "已删除会话"})

        remaining = conversation.memberships.select_related("user")
        if not remaining.exists():
            conversation.delete()
            return ok({"detail": "已退出会话"})
        if was_owner:
            successor = remaining.order_by("joined_at").first()
            conversation.owner = successor.user
            conversation.save(update_fields=["owner"])
            successor.role = Membership.ROLE_OWNER
            successor.save(update_fields=["role"])
            services.create_system_message(
                conversation, f"{successor.user.display_name} 成为新群主", actor=None
            )
        else:
            services.create_system_message(
                conversation, f"{user.display_name} 退出了群聊", actor=None
            )
    return ok({"detail": "已退出会话"})


# ---------------------------------------------------------------- 消息
def _read_map(messages, conversation, memberships=None):
    """批量构造已读信息：单聊返回每条的读者，群聊返回已读人数。"""
    if not messages:
        return {}, {}
    message_ids = [m.id for m in messages]
    rows = MessageRead.objects.filter(message_id__in=message_ids).values("message_id", "user_id")
    read_map = {}
    for row in rows:
        read_map.setdefault(row["message_id"], set()).add(row["user_id"])

    if conversation.type == Conversation.TYPE_DIRECT:
        return read_map, {}

    member_rows = memberships or list(conversation.memberships.all())
    reader_counts = {}
    for message in messages:
        reader_counts[message.id] = sum(
            1
            for row in member_rows
            if row.user_id != message.sender_id and (row.last_read_seq or 0) >= message.seq
        )
    return read_map, reader_counts


@api_view()
def messages(request, conversation_id, user=None):
    """GET  拉取消息（历史回溯 / 增量同步）
    POST 发送消息

    GET 允许服务器管理员旁路读取（治理用）；
    POST 必须是真实会话成员，管理员不能冒名发言。
    """
    if request.method == "GET":
        membership = get_membership(conversation_id, user, allow_admin=True)
        conversation = (
            membership.conversation
            if membership is not None
            else Conversation.objects.get(pk=conversation_id)
        )
        if membership is None:
            membership = admin_observer_membership(conversation, user)
        return _list_messages(request, conversation, membership, viewer_is_admin=user_is_admin(user))

    membership = get_membership(conversation_id, user)
    conversation = membership.conversation

    if request.method == "POST":
        return _send_message(request, conversation, membership)
    raise ApiError("method_not_allowed", "仅支持 GET/POST", 405)


def _list_messages(request, conversation, membership, viewer_is_admin=False):
    limit = min(int(request.GET.get("limit") or DEFAULT_PAGE_SIZE), MAX_PAGE_SIZE)
    queryset = Message.objects.filter(conversation=conversation).select_related(
        "sender", "attachment", "reply_to"
    )

    after_seq = request.GET.get("after_seq")
    before_seq = request.GET.get("before_seq")

    if after_seq is not None:
        # 增量同步：按 seq 升序，客户端用于补齐断线期间的消息
        try:
            after_seq = int(after_seq)
        except ValueError:
            raise ApiError("invalid_cursor", "after_seq 必须是整数")
        rows = list(queryset.filter(seq__gt=after_seq).order_by("seq")[:limit])
    else:
        # 历史回溯：先按 before_seq 过滤，再倒序取一页，最后反转为升序
        if before_seq is not None:
            try:
                before_seq = int(before_seq)
            except ValueError:
                raise ApiError("invalid_cursor", "before_seq 必须是整数")
            queryset = queryset.filter(seq__lt=before_seq)
        rows = list(queryset.order_by("-seq")[:limit])
        rows.reverse()

    read_map, reader_counts = _read_map(rows, conversation)

    items = []
    for message in rows:
        item = serialize_message(
            message,
            include_reads=conversation.type == Conversation.TYPE_DIRECT,
            reader_count=reader_counts.get(message.id),
            viewer_is_admin=viewer_is_admin,
        )
        if conversation.type == Conversation.TYPE_DIRECT:
            item["reads"] = [
                {"user_id": uid} for uid in sorted(read_map.get(message.id, set()))
            ]
        items.append(item)

    return ok(
        {
            "messages": items,
            "has_more": len(rows) >= limit,
            "first_seq": rows[0].seq if rows else None,
            "last_seq": rows[-1].seq if rows else None,
        }
    )


def _send_message(request, conversation, membership):
    body = json_body(request)
    text = (body.get("body") or "").strip()
    attachment_id = body.get("attachment_id")
    client_msg_id = (body.get("client_msg_id") or "").strip()[:64]

    attachment = None
    if attachment_id:
        attachment = Attachment.objects.filter(pk=attachment_id).first()
        if attachment is None:
            raise ApiError("attachment_not_found", "附件不存在", 404)
        if attachment.uploader_id != membership.user_id:
            raise ApiError("forbidden", "不能引用他人的附件", 403)

    if not text and attachment is None:
        raise ApiError("empty_message", "消息内容不能为空")
    if len(text) > 5000:
        raise ApiError("message_too_long", "单条消息不能超过 5000 字")

    if attachment is not None:
        kind = Message.KIND_IMAGE if attachment.is_image else Message.KIND_FILE
    else:
        kind = Message.KIND_TEXT

    reply_to = None
    reply_id = body.get("reply_to_id")
    if reply_id:
        reply_to = Message.objects.filter(pk=reply_id, conversation=conversation).first()
        if reply_to is None:
            raise ApiError("reply_not_found", "引用的消息不存在", 404)

    message, created = services.create_message(
        conversation,
        membership.user,
        kind=kind,
        body=text,
        attachment=attachment,
        reply_to=reply_to,
        client_msg_id=client_msg_id,
    )
    # 自己发出的消息视为已读，避免自己的未读角标异常
    Membership.objects.filter(pk=membership.pk).update(unread_count=0)

    read_map, reader_counts = _read_map([message], conversation)
    result = serialize_message(
        message,
        include_reads=conversation.type == Conversation.TYPE_DIRECT,
        reader_count=reader_counts.get(message.id),
    )
    if conversation.type == Conversation.TYPE_DIRECT:
        result["reads"] = [{"user_id": uid} for uid in sorted(read_map.get(message.id, set()))]
    return ok({"message": result, "created": created}, status=201 if created else 200)


@api_view()
def message_report(request, message_id, user=None):
    """POST /api/v1/messages/<id>/report/ — 举报消息。

    仅做记录，不自动处置；由管理员在 /manage/reports/ 判断是否移除。
    只有会话成员能举报其中的消息，避免任意用户举报他人私聊内容。
    """
    if request.method != "POST":
        raise ApiError("method_not_allowed", "仅支持 POST", 405)

    message = Message.objects.select_related("sender").filter(pk=message_id).first()
    if message is None:
        raise ApiError("not_found", "消息不存在", 404)
    if not Membership.objects.filter(
        conversation_id=message.conversation_id, user=user
    ).exists():
        # 非成员一律 404，不暴露消息是否存在
        raise ApiError("not_found", "消息不存在", 404)
    if message.sender_id == user.id:
        raise ApiError("self_report", "不能举报自己发送的消息", 400)

    body = json_body(request)
    reason = (body.get("reason") or "").strip()
    if len(reason) > 200:
        raise ApiError("reason_too_long", "举报理由不能超过 200 个字符")

    report, created = MessageReport.objects.get_or_create(
        reporter=user,
        message=message,
        defaults={"reason": reason},
    )
    if not created:
        raise ApiError("already_reported", "你已举报过该消息", 409)

    return ok(
        {
            "report": {
                "id": report.pk,
                "status": report.status,
                "reason": report.reason,
                "created": report.created.isoformat(),
            }
        },
        status=201,
    )


@api_view()
def message_revoke(request, message_id, user=None):
    """POST /api/v1/messages/<id>/revoke/ — 撤回消息。"""
    if request.method != "POST":
        raise ApiError("method_not_allowed", "仅支持 POST", 405)
    message = Message.objects.select_related("conversation").filter(pk=message_id).first()
    if message is None:
        raise ApiError("not_found", "消息不存在", 404)
    membership = Membership.objects.filter(
        conversation_id=message.conversation_id, user=user
    ).first()
    if membership is None:
        raise ApiError("not_found", "消息不存在", 404)

    revoked, changed = services.revoke_message(message, user, membership)
    if revoked is None:
        raise ApiError("revoke_forbidden", "超过 2 分钟或无权撤回该消息", 403)
    return ok({"message": serialize_message(revoked), "changed": changed})


@api_view()
def conversation_read(request, conversation_id, user=None):
    """POST /api/v1/conversations/<id>/read/ — 标记已读。"""
    if request.method != "POST":
        raise ApiError("method_not_allowed", "仅支持 POST", 405)
    membership = get_membership(conversation_id, user)
    body = json_body(request)
    up_to_seq = body.get("up_to_seq")
    if up_to_seq is None:
        # 未显式指定则读到最新
        up_to_seq = membership.conversation.last_seq
    try:
        up_to_seq = int(up_to_seq)
    except (TypeError, ValueError):
        raise ApiError("invalid_seq", "up_to_seq 必须是整数")

    advanced = services.mark_read(membership.conversation, membership, up_to_seq)
    return ok({"last_read_seq": advanced, "unread_count": 0})


@api_view()
def conversation_typing(request, conversation_id, user=None):
    """POST /api/v1/conversations/<id>/typing/ — 上报输入状态。"""
    if request.method != "POST":
        raise ApiError("method_not_allowed", "仅支持 POST", 405)
    membership = get_membership(conversation_id, user)
    conversation = membership.conversation

    stream.store.set_typing(conversation.pk, user.id)
    others = list(
        Membership.objects.filter(conversation=conversation)
        .exclude(user=user)
        .values_list("user_id", flat=True)
    )
    if others:
        stream.publish_many(
            others,
            stream.EVENT_TYPING,
            {
                "conversation_id": conversation.pk,
                "user_id": user.id,
                "nickname": user.display_name,
                "ttl": getattr(settings, "TYPING_TTL_SECONDS", 5),
            },
            conversation.pk,
        )
    return ok({"ttl": getattr(settings, "TYPING_TTL_SECONDS", 5)})


# ---------------------------------------------------------------- 附件
def max_upload_size():
    """每次读取配置，便于测试通过 override_settings 调整上限。"""
    return getattr(settings, "MAX_UPLOAD_SIZE", 20 * 1024 * 1024)


@api_view()
def attachments(request, user=None):
    """POST /api/v1/attachments/ — 上传附件（multipart，字段名 file）。"""
    if request.method != "POST":
        raise ApiError("method_not_allowed", "仅支持 POST", 405)

    upload = request.FILES.get("file")
    if upload is None:
        raise ApiError("missing_file", "请通过 file 字段上传文件")
    if upload.size <= 0:
        raise ApiError("empty_file", "文件内容为空")
    limit = max_upload_size()
    if upload.size > limit:
        raise ApiError(
            "file_too_large",
            f"文件大小超过上限 {limit // (1024 * 1024)}MB",
            413,
        )

    digest = hashlib.sha256()
    for chunk in upload.chunks():
        digest.update(chunk)
    upload.seek(0)

    mime = (upload.content_type or "").lower()
    image = None
    if _looks_like_image(mime, upload.name):
        image = _validate_image(upload)  # 非图片或损坏则抛 ApiError

    attachment = Attachment(
        uploader=user,
        original_name=(upload.name or "unnamed")[:255],
        size=upload.size,
        mime=mime or ("image/jpeg" if image else "application/octet-stream"),
        sha256=digest.hexdigest(),
    )
    if image is not None:
        attachment.width, attachment.height = image.size
    attachment.file = upload
    attachment.save()

    if image is not None:
        thumb = _make_thumbnail(image)
        if thumb is not None:
            attachment.thumb.save(f"thumb_{attachment.pk}.jpg", thumb, save=True)

    return ok({"attachment": serialize_attachment(attachment)}, status=201)


def _looks_like_image(mime, filename):
    if mime.startswith("image/"):
        return True
    suffix = (filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""
    allowed = getattr(settings, "ALLOWED_IMAGE_EXTENSIONS", set())
    return suffix in allowed


def _validate_image(upload):
    """用 Pillow 真实解码，不信任扩展名与 Content-Type。"""
    from django.core.files.uploadedfile import InMemoryUploadedFile  # noqa: F401

    max_pixels = getattr(settings, "MAX_IMAGE_PIXELS", 40_000_000)
    allowed = getattr(settings, "ALLOWED_IMAGE_EXTENSIONS", set())
    try:
        upload.seek(0)
        image = Image.open(upload)
        image.load()
    except Exception:
        raise ApiError("invalid_image", "图片文件无法解析或已损坏")
    finally:
        upload.seek(0)

    fmt = (image.format or "").lower()
    if fmt and fmt not in {"jpeg", "png", "gif", "webp", "bmp"}:
        raise ApiError("unsupported_image", f"不支持的图片格式: {fmt}")
    if fmt and fmt not in allowed and fmt != "jpeg":
        raise ApiError("unsupported_image", f"不支持的图片格式: {fmt}")
    width, height = image.size
    if width * height > max_pixels:
        raise ApiError("image_too_large", "图片像素过大，请压缩后重试", 413)
    return image


def _make_thumbnail(image, size=(320, 320)):
    """生成缩略图（JPEG）。失败返回 None，不影响原图上传。"""
    from io import BytesIO

    from django.core.files.base import ContentFile

    try:
        thumb = image.copy()
        thumb.thumbnail(size, Image.LANCZOS)
        if thumb.mode not in ("RGB", "L"):
            thumb = thumb.convert("RGB")
        buffer = BytesIO()
        thumb.save(buffer, format="JPEG", quality=82, optimize=True)
        return ContentFile(buffer.getvalue())
    except Exception:  # pragma: no cover - 缩略图失败可降级
        return None


@api_view()
def attachment_download(request, attachment_id, user=None):
    """GET /api/v1/attachments/<id>/download/?thumb=1

    仅上传者本人或该附件所属会话的成员可下载。
    """
    attachment = Attachment.objects.filter(pk=attachment_id).first()
    if attachment is None:
        raise ApiError("not_found", "附件不存在", 404)

    is_owner = attachment.uploader_id == user.id
    if not is_owner:
        # 附件必须已挂到某条消息上，且请求者是该会话成员
        member_ok = Membership.objects.filter(
            user=user, conversation__messages__attachment=attachment
        ).exists()
        if not member_ok:
            raise ApiError("not_found", "附件不存在", 404)

    want_thumb = request.GET.get("thumb") in {"1", "true", "yes"}
    field = attachment.thumb if (want_thumb and attachment.thumb) else attachment.file
    if not field:
        raise ApiError("not_found", "文件不存在", 404)

    try:
        handle = field.open("rb")
    except (FileNotFoundError, OSError, ValueError):
        raise Http404("文件已丢失")

    filename = f"thumb_{attachment.original_name}.jpg" if want_thumb else attachment.original_name
    response = FileResponse(handle, as_attachment=False, filename=filename)
    response["Content-Type"] = "image/jpeg" if want_thumb else (attachment.mime or "application/octet-stream")
    if not attachment.is_image:
        # 非图片强制下载，避免浏览器直接渲染潜在危险内容
        response["Content-Disposition"] = f'attachment; filename="{attachment.original_name}"'
    return response
