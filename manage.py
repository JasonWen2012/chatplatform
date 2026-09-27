#!/usr/bin/env python
"""Django 命令行入口。

用法示例（Windows）::

    .venv\\Scripts\\python.exe manage.py migrate
    .venv\\Scripts\\python.exe manage.py runserver

运行测试时会自动切换到 backend.config.settings_test，
以缩短长轮询超时并使用快速密码哈希，让用例秒级完成。
"""
import os
import sys


def main():
    # 测试命令使用专用配置：长轮询超时归零、密码哈希换 MD5
    if "test" in sys.argv[1:2]:
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backend.config.settings_test")
    else:
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backend.config.settings")

    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "无法导入 Django，请确认已安装依赖："
            " .venv\\Scripts\\python.exe -m pip install -r requirements.txt"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
