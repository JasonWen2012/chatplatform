"""SQLite 连接级 PRAGMA 设置。

SQLite 后端没有 ``init_command`` 选项，因此改用 ``connection_created``
信号在每次建立连接后设置：

* ``journal_mode=WAL`` —— 读写并发友好，长轮询只读不会阻塞消息写入；
* ``synchronous=NORMAL`` —— WAL 下的推荐值，兼顾安全与吞吐；
* ``foreign_keys=ON`` —— SQLite 默认关闭外键约束，必须显式打开，
  否则 ``on_delete=CASCADE`` 不会生效；
* ``busy_timeout`` —— 与 DATABASES 的 timeout 配合，进一步降低锁冲突。
"""
from django.db.backends.signals import connection_created
from django.dispatch import receiver

PRAGMAS = (
    "PRAGMA journal_mode=WAL;",
    "PRAGMA synchronous=NORMAL;",
    "PRAGMA foreign_keys=ON;",
    "PRAGMA busy_timeout=20000;",
)


@receiver(connection_created)
def configure_sqlite(sender, connection, **kwargs):
    if connection.vendor != "sqlite":
        return
    with connection.cursor() as cursor:
        for pragma in PRAGMAS:
            cursor.execute(pragma)
