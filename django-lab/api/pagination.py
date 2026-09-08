"""分页：默认 10 条，允许 ?page_size= 客户端自定义（如列表页对齐 SSR 版每页 9 本）。"""

from rest_framework.pagination import PageNumberPagination


class StandardPagination(PageNumberPagination):
    page_size_query_param = "page_size"
    max_page_size = 50
