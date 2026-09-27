"""分页参数解析与结果封装。

放在这里而不是 ``accounts.api.api``，因为分页只被管理端列表使用；
公共模块保持「响应信封 + 鉴权」两件事，避免职责膨胀。
"""
from backend.accounts.api.api import ApiError

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 50


def paginate(request, queryset, default_size=DEFAULT_PAGE_SIZE, max_size=MAX_PAGE_SIZE):
    """按 ``?page=&page_size=`` 切片，返回 ``(page, page_size, 总数, 当前页对象列表)``。

    非法参数直接报 400，而不是静默回落到默认值 —— 管理端宁可报错也不要给出
    错误的数据视图，否则容易被误判为「数据缺失」。
    """
    raw_page = request.GET.get("page") or "1"
    raw_size = request.GET.get("page_size") or str(default_size)
    try:
        page = int(raw_page)
        page_size = int(raw_size)
    except (TypeError, ValueError):
        raise ApiError("invalid_pagination", "page 与 page_size 必须是整数")

    if page < 1:
        raise ApiError("invalid_pagination", "page 必须大于等于 1")
    if page_size < 1 or page_size > max_size:
        raise ApiError("invalid_pagination", f"page_size 需在 1~{max_size} 之间")

    total = queryset.count()
    offset = (page - 1) * page_size
    rows = list(queryset[offset:offset + page_size])
    return page, page_size, total, rows


def page_meta(page, page_size, total):
    """分页元信息，供前端渲染翻页控件。"""
    total_pages = (total + page_size - 1) // page_size if page_size else 0
    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
        "has_next": page < total_pages,
        "has_prev": page > 1,
    }


def clamp_query(request, max_length=50):
    """读取并校验搜索关键词。"""
    keyword = (request.GET.get("q") or "").strip()
    if len(keyword) > max_length:
        raise ApiError("invalid_query", f"搜索关键词不能超过 {max_length} 个字符")
    return keyword
