"""事件存储工厂：为二期 Redis 预留切换点。

一期用进程内内存实现（单进程部署够用）。
二期接 WebSocket / 多 worker 时，只需：
1. 实现一个同样接口的 ``RedisEventStore``；
2. 在 settings 里把 ``EVENT_STORE_BACKEND`` 改成 ``"redis"``。

``backend.realtime.stream.publish()`` 与其调用点都不需要改动 —— 这正是本模块存在的意义。
"""
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

# 需要被具体实现满足的接口（文档用途，同时也是二期实现的对照清单）
EVENT_STORE_INTERFACE = (
    "publish(user_ids, event_type, payload, conversation_id=None)",
    "fetch(user_id, cursor)",
    "current_cursor()",
    "touch(user_id)",
    "set_online(user_id, online=True)",
    "online_ids()",
    "is_online(user_id, window=120)",
    "set_typing(conversation_id, user_id, ttl=None)",
    "typing_users(conversation_id, exclude_user_id=None)",
    "clear()",
)

_instance = None


def get_event_store():
    """按配置返回事件存储单例。"""
    global _instance
    if _instance is not None:
        return _instance

    backend = getattr(settings, "EVENT_STORE_BACKEND", "memory")
    if backend == "memory":
        from backend.realtime.stream import EventStore

        _instance = EventStore()
    elif backend == "redis":
        # 二期实现位置：从 backend.realtime.redis_store 导入 RedisEventStore
        raise ImproperlyConfigured(
            "EVENT_STORE_BACKEND='redis' 尚未实现（二期计划）。"
            "一期请使用 'memory'。"
        )
    else:
        raise ImproperlyConfigured(f"未知的 EVENT_STORE_BACKEND: {backend!r}")
    return _instance


def reset_event_store():
    """释放单例（测试用）。"""
    global _instance
    _instance = None
