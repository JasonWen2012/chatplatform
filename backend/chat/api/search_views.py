"""全局搜索：消息、用户、会话。

**权限要点**：消息搜索强制按「当前用户所属会话」过滤，
绝不能让用户搜到未参与会话的内容 —— 这是本模块最关键的约束，
有专门测试（``test_search``）守住。
"""
from django.db.models import Q

from backend.accounts.api.api import ApiError, api_view, ok, user_brief
from backend.accounts.models import User
from backend.chat.models import Conversation, Message
from backend.chat.serializers import serialize_message

MAX_KEYWORD_LENGTH = 50
MAX_RESULTS = 50
DEFAULT_RESULTS = 20


def _params(request):
    keyword = (request.GET.get("q") or "").strip()
    if not keyword:
        raise ApiError("missing_query", "请提供搜索关键词 q")
    if len(keyword) > MAX_KEYWORD_LENGTH:
        raise ApiError("invalid_query", f"搜索关键词不能超过 {MAX_KEYWORD_LENGTH} 个字符")

    raw_limit = request.GET.get("limit") or str(DEFAULT_RESULTS)
    try:
        limit = int(raw_limit)
    except (TypeError, ValueError):
        raise ApiError("invalid_limit", "limit 必须是整数")
    if limit < 1:
        raise ApiError("invalid_limit", "limit 必须大于 0")
    return keyword, min(limit, MAX_RESULTS)


@api_view()
def search(request, user=None):
    """GET /api/v1/search/?q=&type=all|message|user|conversation&limit="""
    keyword, limit = _params(request)
    scope = (request.GET.get("type") or "all").strip()
    if scope not in {"all", "message", "user", "conversation"}:
        raise ApiError("invalid_type", "type 仅支持 all / message / user / conversation")

    result = {}
    if scope in {"all", "message"}:
        result["messages"] = _search_messages(keyword, limit, user)
    if scope in {"all", "conversation"}:
        result["conversations"] = _search_conversations(keyword, limit, user)
    if scope in {"all", "user"}:
        result["users"] = _search_users(keyword, limit, user)
    return ok({"query": keyword, "type": scope, **result})


def _search_messages(keyword, limit, user):
    """只搜索当前用户参与的会话中的消息。"""
    rows = (
        Message.objects.filter(
            # 关键约束：必须限定在本人所属会话内
            conversation__memberships__user=user,
            body__icontains=keyword,
        )
        .exclude(body="")
        .select_related("sender", "attachment", "conversation", "reply_to")
        .order_by("-created_at")[:limit]
    )

    items = []
    for message in rows:
        data = serialize_message(message)
        conversation = message.conversation
        items.append(
            {
                "message": data,
                "conversation": {
                    "id": conversation.pk,
                    "type": conversation.type,
                    "title": conversation.title or f"会话 {conversation.pk}",
                },
            }
        )
    return items


def _search_conversations(keyword, limit, user):
    rows = (
        Conversation.objects.filter(memberships__user=user)
        .filter(
            Q(title__icontains=keyword)
            | Q(memberships__user__nickname__icontains=keyword)
            | Q(memberships__user__username__icontains=keyword)
        )
        .distinct()
        .order_by("-last_message_at")[:limit]
    )
    items = []
    for conversation in rows:
        items.append(
            {
                "id": conversation.pk,
                "type": conversation.type,
                "title": conversation.title or f"会话 {conversation.pk}",
                "last_message_at": conversation.last_message_at.isoformat(),
            }
        )
    return items


def _search_users(keyword, limit, user):
    rows = (
        User.objects.filter(
            Q(username__icontains=keyword) | Q(nickname__icontains=keyword)
        )
        .exclude(pk=user.pk)
        .exclude(is_active=False)
        .order_by("id")[:limit]
    )
    return [user_brief(row) for row in rows]
