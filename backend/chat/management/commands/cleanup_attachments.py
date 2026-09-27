"""回收孤儿附件与磁盘残留文件。

为什么需要：会话被删除时，``Attachment`` 记录会随消息级联清理，
但 ``media/`` 下的实际文件不会自动删除（在事务里同步删文件有风险：
一旦事务回滚，文件已经没了）。因此改为由本命令定期兜底回收。

用法::

    manage.py cleanup_attachments --dry-run          # 先看会删什么（建议先跑）
    manage.py cleanup_attachments                    # 清理 24 小时前的孤儿附件
    manage.py cleanup_attachments --older-than 1     # 只清理 1 小时前的
    manage.py cleanup_attachments --orphan-files     # 额外清理磁盘上无数据库记录的文件
"""
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from backend.chat.models import Attachment

# 处于「已上传但尚未发出消息」状态的新附件不能被当成孤儿，
# 否则会误删用户刚选好正要发送的文件。
DEFAULT_OLDER_THAN_HOURS = 24


class Command(BaseCommand):
    help = "回收未被消息引用的附件（以及可选的磁盘孤儿文件）"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="只列出，不实际删除")
        parser.add_argument(
            "--older-than",
            type=float,
            default=DEFAULT_OLDER_THAN_HOURS,
            help=f"只处理创建超过 N 小时的附件（默认 {DEFAULT_OLDER_THAN_HOURS}）",
        )
        parser.add_argument(
            "--orphan-files", action="store_true", help="同时清理磁盘上无数据库记录的孤儿文件"
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        cutoff = timezone.now() - timedelta(hours=options["older_than"])

        queryset = Attachment.objects.filter(created__lt=cutoff)
        # 未被任何消息引用的附件即孤儿
        orphans = [a for a in queryset if not a.messages.exists()]

        self.stdout.write(
            f"扫描条件：创建早于 {cutoff:%Y-%m-%d %H:%M}，"
            f"候选附件 {queryset.count()} 个，其中无引用 {len(orphans)} 个"
        )

        total_bytes = 0
        for attachment in orphans:
            total_bytes += attachment.size or 0
            label = f"#{attachment.id} {attachment.original_name} ({attachment.size}B)"
            if dry_run:
                self.stdout.write(f"  [将删除] {label}")
                continue
            # 先删文件再删记录：文件删除失败时记录仍在，下次还能重试
            for field in (attachment.file, attachment.thumb):
                if field:
                    try:
                        field.delete(save=False)
                    except Exception as exc:  # pragma: no cover
                        self.stderr.write(f"  文件删除失败 {label}: {exc}")
            attachment.delete()
            self.stdout.write(f"  [已删除] {label}")

        orphan_file_count = 0
        if options["orphan_files"]:
            orphan_file_count = self._sweep_disk_orphans(cutoff, dry_run)

        prefix = "（dry-run，未实际删除）" if dry_run else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"完成{prefix}：附件记录 {len(orphans)} 个，"
                f"释放约 {total_bytes / 1024:.1f} KB，磁盘孤儿文件 {orphan_file_count} 个"
            )
        )
        if dry_run:
            self.stdout.write("确认无误后去掉 --dry-run 再执行一次")

    def _sweep_disk_orphans(self, cutoff, dry_run):
        """清理 media/uploads 下在数据库里已无记录的文件。"""
        import os

        media_root = str(getattr(settings, "MEDIA_ROOT", ""))
        uploads_dir = os.path.join(media_root, "uploads")
        if not media_root or not os.path.isdir(uploads_dir):
            return 0

        known = set()
        for row in Attachment.objects.values_list("file", "thumb"):
            for name in row:
                if name:
                    known.add(os.path.basename(name))

        removed = 0
        cutoff_ts = cutoff.timestamp()
        for base, _dirs, files in os.walk(uploads_dir):
            for name in files:
                path = os.path.join(base, name)
                if name in known:
                    continue
                try:
                    if os.path.getmtime(path) >= cutoff_ts:
                        continue  # 太新，可能正在上传
                except OSError:
                    continue
                if dry_run:
                    self.stdout.write(f"  [将删除文件] {path}")
                else:
                    try:
                        os.remove(path)
                    except OSError as exc:  # pragma: no cover
                        self.stderr.write(f"  删除失败 {path}: {exc}")
                        continue
                    self.stdout.write(f"  [已删除文件] {path}")
                removed += 1
        return removed
