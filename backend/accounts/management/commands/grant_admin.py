"""授予 / 撤销服务器管理员权限。

用法::

    manage.py grant_admin --username Jason_Wen
    manage.py grant_admin --nickname WJ_RushB
    manage.py grant_admin --username Jason_Wen --revoke
    manage.py grant_admin --list

说明：``is_staff`` 决定能否进入管理界面，``is_superuser`` 决定是否自动拥有
全部模型权限。单人运维场景下两者一并授予，否则需要逐条配置权限表；
代价是可在 Django 原生 /admin/ 删除任意数据。
"""
from django.core.management.base import BaseCommand, CommandError

from backend.accounts.models import User


class Command(BaseCommand):
    help = "授予或撤销服务器管理员权限（is_staff / is_superuser）"

    def add_arguments(self, parser):
        parser.add_argument("--username", help="按账号名精确定位")
        parser.add_argument("--nickname", help="按昵称定位（可能匹配到多个）")
        parser.add_argument("--revoke", action="store_true", help="撤销管理员权限")
        parser.add_argument("--list", action="store_true", help="列出当前全部管理员")

    def handle(self, *args, **options):
        if options["list"]:
            return self._list_admins()

        username = options.get("username")
        nickname = options.get("nickname")
        if not username and not nickname:
            raise CommandError("请用 --username 或 --nickname 指定账号，或用 --list 查看现有管理员")

        target = self._locate(username, nickname)
        if options["revoke"]:
            return self._revoke(target)

        changed = []
        if not target.is_staff:
            target.is_staff = True
            changed.append("is_staff")
        if not target.is_superuser:
            target.is_superuser = True
            changed.append("is_superuser")
        if changed:
            target.save(update_fields=changed)
            self.stdout.write(
                self.style.SUCCESS(
                    f"已将 {target.username}（{target.display_name}）设为管理员，"
                    f"更新字段: {', '.join(changed)}"
                )
            )
        else:
            self.stdout.write(
                self.style.WARNING(f"{target.username}（{target.display_name}）已经是管理员，无需变更")
            )
        self.stdout.write(f"  账号 id : {target.id}")
        self.stdout.write(f"  管理入口: /manage/")
        self.stdout.write(f"  原生后台: /admin/")
        self.stdout.write(
            self.style.WARNING(
                "  提示：管理员仍可使用完整聊天界面；关闭管理权限用 --revoke"
            )
        )

    def _locate(self, username, nickname):
        if username:
            target = User.objects.filter(username=username).first()
            if target is None:
                # 容错：用户可能把昵称当成账号名传进来
                by_nickname = User.objects.filter(nickname=username)
                if by_nickname.count() == 1:
                    found = by_nickname.first()
                    self.stdout.write(
                        self.style.WARNING(
                            f"账号 {username!r} 不存在；已按昵称匹配到 {found.username!r}"
                        )
                    )
                    return found
                raise CommandError(
                    f"账号 {username!r} 不存在。可用 --nickname 指定昵称，或用 --list 查看现有账号"
                )
            return target

        matches = User.objects.filter(nickname=nickname)
        count = matches.count()
        if count == 0:
            raise CommandError(f"没有昵称为 {nickname!r} 的账号")
        if count > 1:
            names = ", ".join(matches.values_list("username", flat=True))
            raise CommandError(
                f"昵称 {nickname!r} 匹配到 {count} 个账号（{names}），请改用 --username 精确指定"
            )
        return matches.first()

    def _revoke(self, target):
        changed = []
        if target.is_staff:
            target.is_staff = False
            changed.append("is_staff")
        if target.is_superuser:
            target.is_superuser = False
            changed.append("is_superuser")
        if changed:
            target.save(update_fields=changed)
            self.stdout.write(
                self.style.SUCCESS(
                    f"已撤销 {target.username} 的管理员权限，更新字段: {', '.join(changed)}"
                )
            )
        else:
            self.stdout.write(
                self.style.WARNING(f"{target.username} 本来就不是管理员，无需变更")
            )

    def _list_admins(self):
        admins = User.objects.filter(is_staff=True).order_by("id")
        if not admins.exists():
            self.stdout.write(self.style.WARNING("当前没有任何管理员"))
            return
        self.stdout.write(f"当前管理员 {admins.count()} 个：")
        for user in admins:
            flag = "superuser" if user.is_superuser else "staff"
            state = "" if user.is_active else "（已禁用）"
            self.stdout.write(
                f"  id={user.id:<4} {user.username:<20} {user.display_name:<16} [{flag}]{state}"
            )
