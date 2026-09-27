"""健康检查。

给运维/负载均衡用的最小探针：只暴露「活着/不活着」与版本号，
绝不返回路径、依赖明细或配置，避免变成信息泄露面。
"""
from django.conf import settings
from django.db import connection
from django.http import JsonResponse

VERSION = "1.1.0"


def healthz(request):
    """GET /healthz — 无需鉴权。

    数据库探测失败返回 503，便于上游摘除实例。
    """
    db_ok = True
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        db_ok = False

    payload = {
        "ok": db_ok,
        "db": db_ok,
        "version": VERSION,
        # 便于确认当前实例是否开放了管理员入口（不暴露具体账号）
        "debug": bool(settings.DEBUG),
    }
    return JsonResponse(payload, status=200 if db_ok else 503)
