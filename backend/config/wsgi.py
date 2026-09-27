"""WSGI 入口（同步部署，一期长轮询默认走此通道）。"""
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backend.config.settings")

application = get_wsgi_application()
