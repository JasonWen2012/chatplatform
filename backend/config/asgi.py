"""ASGI 入口。

第一期只使用 HTTP 长轮询，ASGI 仅作为标准入口保留。
第二期引入 Django Channels 时，在此处挂载 WebSocket 路由，
可直接复用 backend.realtime.stream 中同一套事件信封与游标协议，
业务代码无需改动。
"""
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backend.config.settings")

application = get_asgi_application()

# ------- 第二期（WebSocket）在此扩展，示意如下 -------
# from channels.routing import ProtocolTypeRouter, URLRouter
# from channels.auth import AuthMiddlewareStack
# from backend.realtime.routing import websocket_urlpatterns
# application = ProtocolTypeRouter({
#     "http": get_asgi_application(),
#     "websocket": AuthMiddlewareStack(URLRouter(websocket_urlpatterns)),
# })
