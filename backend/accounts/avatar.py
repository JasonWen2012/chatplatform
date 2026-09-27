"""头像处理：EXIF 方向校正 + 居中方形裁剪 + 转 JPEG。

单独成模块的原因：裁剪规则（正方形、尺寸、格式）需要被测试直接验证，
放在视图里会让测试只能通过 HTTP 间接断言。
"""
import secrets
from io import BytesIO

from django.core.files.base import ContentFile
from PIL import Image, ImageOps

from backend.accounts.api.api import ApiError

AVATAR_SIZE = 256
ALLOWED_FORMATS = {"jpeg", "png", "gif", "webp", "bmp"}


def normalize_avatar(upload, size=AVATAR_SIZE):
    """把上传图片转成统一的方形 JPEG，返回 ``(ContentFile, 文件名)``。

    * 先按 EXIF 方向旋转 —— 否则手机竖拍照片会倒着显示；
    * 居中裁剪为正方形再缩放，避免直接拉伸导致人物变形。
    """
    if upload is None:
        raise ApiError("missing_file", "请通过 file 字段上传头像")

    try:
        upload.seek(0)
        image = Image.open(upload)
        image.load()
    except Exception:
        raise ApiError("invalid_image", "图片文件无法解析或已损坏")
    finally:
        upload.seek(0)

    fmt = (image.format or "").lower()
    if fmt and fmt not in ALLOWED_FORMATS:
        raise ApiError("unsupported_image", f"不支持的图片格式: {fmt}")

    try:
        # 依据 EXIF 摆正方向
        image = ImageOps.exif_transpose(image)
        # 统一转 RGB：PNG 的透明通道与调色板模式无法直接存 JPEG
        if image.mode in ("RGBA", "LA", "P"):
            background = Image.new("RGB", image.size, (255, 255, 255))
            converted = image.convert("RGBA")
            background.paste(converted, mask=converted.split()[-1])
            image = background
        elif image.mode != "RGB":
            image = image.convert("RGB")

        side = min(image.size)
        left = (image.width - side) // 2
        top = (image.height - side) // 2
        square = image.crop((left, top, left + side, top + side))
        square = square.resize((size, size), Image.LANCZOS)

        buffer = BytesIO()
        square.save(buffer, format="JPEG", quality=88, optimize=True)
    except ApiError:
        raise
    except Exception:
        raise ApiError("avatar_process_failed", "头像处理失败，请换一张图片试试")

    filename = f"avatar_{secrets.token_hex(8)}.jpg"
    return ContentFile(buffer.getvalue()), filename
