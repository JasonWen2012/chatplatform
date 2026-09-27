"""项目总路由。"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from backend.apiapp.health import healthz
from backend.apiapp.urls import api_urlpatterns, web_urlpatterns
from backend.ops.web_urls import web_urlpatterns as ops_urlpatterns

urlpatterns = [
    path("admin/", admin.site.urls),
    # 健康检查放根路径：路径短、无需鉴权、不暴露细节
    path("healthz", healthz, name="healthz"),
    # 对外协议：/api/v1/...
    path("api/v1/", include(api_urlpatterns)),
    # 管理员界面（仅 is_staff 可见）
    path("", include(ops_urlpatterns)),
    # 网页界面
    path("", include(web_urlpatterns)),
]

# 开发环境下由 Django 直接提供上传文件（下载仍走鉴权接口）
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
