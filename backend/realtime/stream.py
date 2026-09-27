"""实时事件流（第一期：进程内实现 + HTTP 长轮询）。

统一事件信封::

    {
      "event_id": 123,          # 单调递增，也是客户端游标
      "type": "message.new",
      "conversation_id": 7,     # 会话无关事件为 None
      "payload": {...},
      "ts": "2026-01-01T00:00:00+08:00"
    }

第二期接入 WebSocket 时，只需新增一个 consumer 消费同一个 ``EventStore``，
并把 ``store`` 换成 Redis 实现；``publish()`` 的调用点与信封格式完全不变。
"""
import threading
import time
from collections import deque
from datetime import datetime, timezone as dt_timezone

from django.conf import settings

# 每个用户最多保留的事件条数（与 TTL 双重限制，防止内存无限增长）
MAX_EVENTS_PER_USER = 1000
# 事件类型常量，避免各处手写字符串
EVENT_MESSAGE_NEW = "message.new"
EVENT_MESSAGE_REVOKED = "message.revoked"
EVENT_CONVERSATION_UPDATED = "conversation.updated"
EVENT_READ_RECEIPT = "read.receipt"
EVENT_TYPING = "typing"
EVENT_FRIEND_REQUEST = "friend.request"
EVENT_FRIEND_ACCEPTED = "friend.accepted"
EVENT_PRESENCE = "presence"


def _now_iso():
    return datetime.now(dt_timezone.utc).astimezone().isoformat()


class EventStore:
    """线程安全的内存事件缓冲 + 在线状态 + 输入状态。

    单进程部署下即够用；接口刻意按 Redis 形态设计，便于二期替换。
    """

    def __init__(self):
        self._lock = threading.RLock()
        # user_id -> deque[event dict]
        self._buffers = {}
        # user_id -> 最后活跃时间戳（用于在线判定）
        self._online = {}
        # (conversation_id, user_id) -> 过期时间戳
        self._typing = {}
        self._counter = 0

    # ------------------------------------------------------------ 事件
    def publish(self, user_ids, event_type, payload, conversation_id=None):
        """向若干用户投递同一事件。

        返回信封副本列表；无收件人时返回空列表。
        """
        targets = [uid for uid in dict.fromkeys(user_ids) if uid]
        if not targets:
            return []
        with self._lock:
            self._counter += 1
            event_id = self._counter
            envelope = {
                "event_id": event_id,
                "type": event_type,
                "conversation_id": conversation_id,
                "payload": payload,
                "ts": _now_iso(),
            }
            for uid in targets:
                buffer = self._buffers.setdefault(uid, deque(maxlen=MAX_EVENTS_PER_USER))
                buffer.append(envelope)
            self._trim_locked()
            return [envelope]

    def fetch(self, user_id, cursor):
        """返回 event_id > cursor 的事件（按时间顺序）。"""
        with self._lock:
            buffer = self._buffers.get(user_id)
            if not buffer:
                return []
            return [event for event in buffer if event["event_id"] > cursor]

    def current_cursor(self):
        """全局最新事件号，用于新客户端「只看后续」的初始化。"""
        with self._lock:
            return self._counter

    def _trim_locked(self):
        """按 TTL 裁剪过期事件；必须在持锁状态下调用。"""
        ttl = getattr(settings, "EVENT_BUFFER_TTL_SECONDS", 600)
        if ttl <= 0:
            return
        cutoff = time.time() - ttl
        for uid in list(self._buffers.keys()):
            buffer = self._buffers[uid]
            while buffer:
                try:
                    stamped = datetime.fromisoformat(buffer[0]["ts"]).timestamp()
                except (ValueError, KeyError):
                    buffer.popleft()
                    continue
                if stamped < cutoff:
                    buffer.popleft()
                else:
                    break
            if not buffer:
                self._buffers.pop(uid, None)

    # ------------------------------------------------------------ 在线状态
    def touch(self, user_id):
        """记录用户活跃时间。"""
        with self._lock:
            self._online[user_id] = time.time()

    def set_online(self, user_id, online=True):
        with self._lock:
            if online:
                self._online[user_id] = time.time()
            else:
                self._online.pop(user_id, None)

    def online_ids(self):
        with self._lock:
            return set(self._online.keys())

    def is_online(self, user_id, window=120):
        with self._lock:
            stamp = self._online.get(user_id)
        return bool(stamp and time.time() - stamp <= window)

    # ------------------------------------------------------------ 输入状态
    def set_typing(self, conversation_id, user_id, ttl=None):
        ttl = ttl if ttl is not None else getattr(settings, "TYPING_TTL_SECONDS", 5)
        with self._lock:
            self._typing[(conversation_id, user_id)] = time.time() + ttl

    def typing_users(self, conversation_id, exclude_user_id=None):
        """返回该会话中仍处于输入状态的用户 id（自动剔除过期项）。"""
        now = time.time()
        with self._lock:
            expired = [key for key, deadline in self._typing.items() if deadline <= now]
            for key in expired:
                self._typing.pop(key, None)
            return [
                uid
                for (cid, uid), deadline in self._typing.items()
                if cid == conversation_id and uid != exclude_user_id and deadline > now
            ]

    def clear(self):
        """清空全部状态（测试用）。"""
        with self._lock:
            self._buffers.clear()
            self._online.clear()
            self._typing.clear()
            self._counter = 0


class _StoreProxy:
    """把 ``stream.store`` 的调用转发给工厂返回的实例。

    保留这个属性名是为了让既有调用点（``stream.store.publish(...)`` 等）
    一行都不用改动，同时把「具体用哪个实现」交给 ``registry`` 决定：
    二期把 ``EVENT_STORE_BACKEND`` 改成 ``redis`` 即可整体切换。
    """

    def __getattr__(self, item):
        from backend.realtime.registry import get_event_store

        return getattr(get_event_store(), item)


# 进程级访问入口（实现由 registry 决定）
store = _StoreProxy()


def publish(user_id, event_type, payload, conversation_id=None):
    """向单个用户投递事件。"""
    return store.publish([user_id], event_type, payload, conversation_id)


def publish_many(user_ids, event_type, payload, conversation_id=None):
    """向多个用户投递同一事件。"""
    return store.publish(user_ids, event_type, payload, conversation_id)


def has_pending(user_id, cursor):
    """是否存在比 cursor 更新的事件，供长轮询轮询判断。"""
    return bool(store.fetch(user_id, cursor))
