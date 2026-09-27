"""聊天相关对象的 JSON 序列化。

集中在一处，保证网页端与外部客户端拿到完全一致的结构；
撤回消息的脱敏逻辑只在这里实现一次，避免遗漏导致正文泄露。
"""
from backend.accounts.api.api import user_brief

from backend.chat.models import Conversation, Membership, Message


def serialize_attachment(attachment):
    if attachment is None:
        return None
    return {
        "id": attachment.id,
        "name": attachment.original_name,
        "size": attachment.size,
        "mime": attachment.mime,
        "is_image": attachment.is_image,
        "width": attachment.width,
        "height": attachment.height,
        # 统一走鉴权下载接口，不直接暴露 MEDIA_URL
        "url": f"/api/v1/attachments/{attachment.id}/download/",
        "thumb_url": f"/api/v1/attachments/{attachment.id}/download/?thumb=1" if attachment.thumb else None,
    }


def _reply_preview(message):
    """引用消息摘要。

    被引用消息若已撤回或被管理员移除，**只回标记、不回内容**，
    否则「撤回」这一保护就形同虚设。
    """
    target = message.reply_to
    if target is None:
        return None
    if target.is_hidden:
        return {"id": target.id, "hidden": True, "excerpt": ""}
    if target.kind == Message.KIND_TEXT:
        excerpt = (target.body or "").strip().replace("\n", " ")
        if len(excerpt) > 60:
            excerpt = excerpt[:60] + "…"
    elif target.kind == Message.KIND_IMAGE:
        excerpt = "[图片]"
    elif target.kind == Message.KIND_FILE:
        name = target.attachment.original_name if target.attachment else "文件"
        excerpt = f"[文件] {name}"
    else:
        excerpt = ""
    return {
        "id": target.id,
        "seq": target.seq,
        "sender": user_brief(target.sender) if target.sender_id else None,
        "kind": target.kind,
        "excerpt": excerpt,
        "revoked": target.is_revoked,
        "removed": target.is_removed,
        "hidden": False,
    }


def serialize_message(message, include_reads=False, reader_count=None, viewer_is_admin=False):
    """消息信封。

    内容不可见有两种情况，展示文案区分开，避免用户误解：
    * ``revoked``    —— 用户撤回：「xxx 撤回了一条消息」
    * ``removed``    —— 管理员移除：「消息已被移除」（管理员看到操作人）

    两种情况下 ``body`` 与 ``attachment`` 都必须在服务端已清空，
    这里再兜一层，确保即使数据库残留也不会下发给客户端。
    """
    revoked = message.is_revoked
    removed = message.is_removed
    hidden = revoked or removed
    data = {
        "id": message.id,
        "conversation_id": message.conversation_id,
        "seq": message.seq,
        "kind": message.kind,
        "sender": user_brief(message.sender) if message.sender_id else None,
        "body": "" if hidden else (message.body or ""),
        "attachment": None if hidden else serialize_attachment(message.attachment),
        "reply_to_id": message.reply_to_id,
        "reply_to": _reply_preview(message),
        "client_msg_id": message.client_msg_id or None,
        "created_at": message.created_at.isoformat(),
        "revoked": revoked,
        "revoked_at": message.revoked_at.isoformat() if revoked else None,
        "revoked_by": message.revoked_by_id if revoked else None,
        "removed": removed,
        "removed_at": message.removed_at.isoformat() if removed else None,
        # 仅管理员可见操作人，普通用户无需知道是谁处理的
        "removed_by": message.removed_by_id if (removed and viewer_is_admin) else None,
    }
    if include_reads:
        data["reads"] = [
            {"user_id": row.user_id, "read_at": row.read_at.isoformat()} for row in message.reads.all()
        ]
    if reader_count is not None:
        data["reader_count"] = reader_count
    return data


def serialize_member(membership):
    data = user_brief(membership.user)
    data.update(
        {
            "role": membership.role,
            "joined_at": membership.joined_at.isoformat(),
            "last_read_seq": membership.last_read_seq,
        }
    )
    return data


def direct_peer(conversation, viewer_id, memberships=None):
    """单聊中「对方」是谁；群聊返回 None。"""
    if conversation.type != Conversation.TYPE_DIRECT:
        return None
    rows = memberships if memberships is not None else list(
        conversation.memberships.select_related("user")
    )
    for row in rows:
        if row.user_id != viewer_id:
            return row.user
    return None


def conversation_title(conversation, viewer_id, peer=None):
    """会话展示名：群聊用群名，单聊用对方昵称。"""
    if conversation.type == Conversation.TYPE_GROUP:
        return conversation.title or f"群聊 {conversation.pk}"
    if peer is not None:
        return peer.display_name
    return conversation.title or "私聊"


def serialize_conversation(conversation, membership, last_message=None, memberships=None, extra=None):
    """会话条目，含未读、置顶/免打扰、最后一条消息摘要。"""
    peer = direct_peer(conversation, membership.user_id, memberships)
    from .services import conversation_summary

    data = {
        "id": conversation.pk,
        "type": conversation.type,
        "title": conversation_title(conversation, membership.user_id, peer),
        "owner_id": conversation.owner_id,
        "peer": user_brief(peer) if peer is not None else None,
        "created": conversation.created.isoformat(),
        "last_message_at": conversation.last_message_at.isoformat(),
        "last_seq": conversation.last_seq,
        "unread_count": membership.unread_count,
        "last_read_seq": membership.last_read_seq,
        "my_role": membership.role,
        "is_muted": membership.is_muted,
        "is_pinned": membership.is_pinned,
        "last_message": serialize_message(last_message) if last_message is not None else None,
        "summary": conversation_summary(conversation),
    }
    if extra:
        data.update(extra)
    return data


def membership_role(conversation, user_id):
    row = Membership.objects.filter(conversation=conversation, user_id=user_id).first()
    return row.role if row else None


def admin_observer_membership(conversation, admin_user):
    """为「管理员旁路读取」构造一个不落库的只读成员视图。

    管理员不是会话成员时无法直接序列化，但又不该为此写入 Membership 行
    （会污染成员列表、影响未读统计与群主移交逻辑）。
    因此用一个未保存的实例承载展示所需字段，角色标记为 ``observer``。
    """
    return Membership(
        conversation=conversation,
        user=admin_user,
        role="observer",
        unread_count=0,
        last_read_seq=0,
        is_muted=True,
        is_pinned=False,
    )
