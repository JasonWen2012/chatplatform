"""测试公共辅助：构造用户、好友关系与会话。"""
import json

from django.test import Client

from backend.accounts.models import Token, User
from backend.chat.models import Conversation, Membership
from backend.contacts.models import Friendship

# 测试统一密码：刻意与用户名不相似，避免触发 UserAttributeSimilarityValidator
DEFAULT_PASSWORD = "Zq7#md-94kx"


def make_user(username, nickname=None, password=DEFAULT_PASSWORD):
    user = User.objects.create_user(username=username, password=password)
    user.nickname = nickname or username
    user.save(update_fields=["nickname"])
    return user


def make_token(user):
    return Token.objects.create(user=user).key


def make_client(user):
    """返回带 Bearer 令牌的测试客户端。"""
    return Client(HTTP_AUTHORIZATION=f"Bearer {make_token(user)}")


def befriend(a, b):
    low, high = Friendship.pair(a.id, b.id)
    return Friendship.objects.create(user_low_id=low, user_high_id=high)


def make_direct(a, b):
    conversation = Conversation.objects.create(type=Conversation.TYPE_DIRECT)
    Membership.objects.create(conversation=conversation, user=a)
    Membership.objects.create(conversation=conversation, user=b)
    return conversation


def make_group(owner, members, title="测试群"):
    conversation = Conversation.objects.create(
        type=Conversation.TYPE_GROUP, title=title, owner=owner
    )
    Membership.objects.create(
        conversation=conversation, user=owner, role=Membership.ROLE_OWNER
    )
    for member in members:
        Membership.objects.create(conversation=conversation, user=member)
    return conversation


def api_post(client, path, payload=None):
    return client.post(
        path, data=json.dumps(payload or {}), content_type="application/json"
    )


def api_patch(client, path, payload=None):
    return client.patch(
        path, data=json.dumps(payload or {}), content_type="application/json"
    )


def api_delete(client, path):
    return client.delete(path)


def data_of(response):
    return response.json()["data"]


def error_code(response):
    return response.json()["error"]["code"]
