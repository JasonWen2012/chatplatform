"""聊天领域服务：消息落库、未读维护、已读推进、输入状态。

把「序号分配 + 消息写入 + 未读自增 + 事件投递」收拢在一处，
视图层只负责鉴权与参数校验，避免同一套规则在多处重复。
"""
from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone

from backend.realtime import stream

from backend.chat.models import Conversation, Membership, Message, MessageRead

# 系统消息正文上限（群创建、成员变动等）
SYSTEM_BODY_MAX = 200


def next_seq(conversation):
    """在事务内对会话行加锁并返回下一个消息序号。

    调用方必须处于 ``transaction.atomic()`` 块中，否则锁在函数返回时即释放，
    起不到串行化作用。
    """
    locked = Conversation.objects.select_for_update().get(pk=conversation.pk)
    locked.last_seq = (locked.last_seq or 0) + 1
    locked.last_message_at = timezone.now()
    locked.save(update_fields=["last_seq", "last_message_at"])
    # 让调用方持有的实例保持同步，避免返回旧值
    conversation.last_seq = locked.last_seq
    conversation.last_message_at = locked.last_message_at
    return locked.last_seq


def create_message(conversation, sender, kind=Message.KIND_TEXT, body="", attachment=None,
                   reply_to=None, client_msg_id=""):
    """写入一条消息，维护未读计数，并向会话成员投递 ``message.new``。

    返回 ``(message, created)``；``client_msg_id`` 命中已有消息时 created=False，
    保证网络重试不产生重复。
    """
    if client_msg_id:
        existing = Message.objects.filter(
            conversation=conversation, sender=sender, client_msg_id=client_msg_id
        ).select_related("sender", "attachment").first()
        if existing is not None:
            return existing, False

    with transaction.atomic():
        seq = next_seq(conversation)
        message = Message.objects.create(
            conversation=conversation,
            seq=seq,
            sender=sender,
            kind=kind,
            body=body,
            attachment=attachment,
            reply_to=reply_to,
            client_msg_id=client_msg_id or "",
        )
        # 发送者自身未读归零；其他成员未读 +1
        Membership.objects.filter(conversation=conversation, user=sender).update(unread_count=0)
        Membership.objects.filter(conversation=conversation).exclude(user=sender).update(
            unread_count=F("unread_count") + 1
        )

    _broadcast_new_message(message, conversation)
    return message, True


def create_system_message(conversation, body, actor=None):
    """写入系统消息（不计入他人未读，避免「入群」产生红点）。"""
    body = (body or "")[:SYSTEM_BODY_MAX]
    with transaction.atomic():
        seq = next_seq(conversation)
        message = Message.objects.create(
            conversation=conversation,
            seq=seq,
            sender=actor,
            kind=Message.KIND_SYSTEM,
            body=body,
        )
    _broadcast_new_message(message, conversation, bump_unread=False)
    return message


def _member_ids(conversation, exclude_user_id=None):
    queryset = Membership.objects.filter(conversation=conversation)
    if exclude_user_id is not None:
        queryset = queryset.exclude(user_id=exclude_user_id)
    return list(queryset.values_list("user_id", flat=True))


def _broadcast_new_message(message, conversation, bump_unread=True):
    """把新消息推给除发送者外的成员，同时通知发送者的其他设备。"""
    from backend.chat.serializers import serialize_message

    payload = serialize_message(message)
    payload["unread_bump"] = bump_unread
    recipients = _member_ids(conversation)
    if recipients:
        stream.publish_many(recipients, stream.EVENT_MESSAGE_NEW, payload, conversation.pk)


def mark_read(conversation, membership, up_to_seq):
    """推进已读水位（单调不后退）并清零未读，必要时产生已读回执事件。

    返回实际推进后的水位。
    """
    up_to_seq = max(int(up_to_seq or 0), 0)
    if up_to_seq <= (membership.last_read_seq or 0):
        return membership.last_read_seq

    with transaction.atomic():
        Membership.objects.filter(pk=membership.pk).update(
            last_read_seq=up_to_seq, unread_count=0
        )
        membership.last_read_seq = up_to_seq
        membership.unread_count = 0

        if conversation.type == Conversation.TYPE_DIRECT:
            _emit_direct_read_receipt(conversation, membership, up_to_seq)
        else:
            _emit_group_read_receipt(conversation, membership, up_to_seq)
    return up_to_seq


def _emit_direct_read_receipt(conversation, membership, up_to_seq):
    """单聊：为对方的消息落已读回执行，并通知双方。"""
    peer_message = (
        Message.objects.filter(conversation=conversation, seq__lte=up_to_seq)
        .exclude(sender=membership.user)
        .order_by("-seq")
        .first()
    )
    if peer_message is None:
        return
    created = MessageRead.objects.get_or_create(message=peer_message, user=membership.user)
    if not created[1]:
        # 已经给这条消息发过回执，无需重复通知
        return
    recipients = [peer_message.sender_id, membership.user_id]
    stream.publish_many(
        [rid for rid in recipients if rid],
        stream.EVENT_READ_RECEIPT,
        {
            "conversation_id": conversation.pk,
            "reader_id": membership.user_id,
            "up_to_seq": up_to_seq,
            "message_id": peer_message.pk,
        },
        conversation.pk,
    )


def _emit_group_read_receipt(conversation, membership, up_to_seq):
    """群聊：按成员已读水位推算「N 人已读」，水位变化才广播。"""
    member_ids = _member_ids(conversation)
    readers = Membership.objects.filter(
        conversation=conversation, last_read_seq__gte=up_to_seq
    ).count()
    stream.publish_many(
        member_ids,
        stream.EVENT_READ_RECEIPT,
        {
            "conversation_id": conversation.pk,
            "reader_id": membership.user_id,
            "up_to_seq": up_to_seq,
            "reader_count": readers,
        },
        conversation.pk,
    )


def can_revoke(message, actor, membership):
    """是否有资格撤回：**先看时限**，再看身份（发送者本人或群管理员）。

    时限优先的原因：
    * 超时后连发送者本人也不能撤回，必须返回 403；
    * 但对权限合法者而言，「已撤回」不算失去资格，
      这样重复撤回才能幂等返回 200 而不是 403。
    """
    if actor is None:
        return False
    if timezone.now() > message.revoke_deadline:
        return False
    if message.sender_id == actor.id:
        return True
    return bool(membership and membership.is_manager)


def revoke_message(message, actor, membership):
    """撤回消息。

    返回值语义：
    * ``(message, False)`` —— 已撤回且调用者有资格，重复调用幂等
    * ``(message, True)``  —— 本次真正执行了撤回
    * ``(None, False)``    —— 调用者无权撤回
    """
    if not can_revoke(message, actor, membership):
        return None, False
    if message.is_revoked:
        return message, False

    with transaction.atomic():
        Message.objects.filter(pk=message.pk).update(
            revoked_at=timezone.now(),
            revoked_by=actor,
            body="",
            attachment=None,
        )
        message.refresh_from_db(fields=["revoked_at", "revoked_by", "body", "attachment"])

    from backend.chat.serializers import serialize_message

    recipients = _member_ids(message.conversation)
    stream.publish_many(
        recipients,
        stream.EVENT_MESSAGE_REVOKED,
        {
            "message": serialize_message(message),
            "conversation_id": message.conversation_id,
            "revoked_by": actor.id,
        },
        message.conversation_id,
    )
    return message, True


def conversation_summary(conversation):
    """会话的简要统计：成员数、在线成员数。"""
    memberships = list(Membership.objects.filter(conversation=conversation).select_related("user"))
    online_ids = stream.store.online_ids()
    return {
        "member_count": len(memberships),
        "online_count": sum(1 for m in memberships if m.user_id in online_ids),
    }


def unread_total(user):
    """全部会话的未读总数，用于角标。"""
    return Membership.objects.filter(user=user).aggregate(total=Sum("unread_count"))["total"] or 0
