"""Admin：Django 最著名的「电池」—— 声明式配置换来一整套后台。"""

from django.contrib import admin
from django.db.models import Count

from .models import Author, Book, BorrowRecord, Genre, Status

admin.site.site_header = "Django Lab 图书馆后台"
admin.site.site_title = "Django Lab"
admin.site.index_title = "数据管理"


@admin.register(Genre)
class GenreAdmin(admin.ModelAdmin):
    list_display = ("name", "book_count")
    search_fields = ("name",)

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_book_count=Count("books"))

    @admin.display(description="图书数量", ordering="_book_count")
    def book_count(self, obj):
        return obj._book_count


class BookInline(admin.TabularInline):
    model = Book
    extra = 0
    fields = ("title", "slug", "status", "price")
    show_change_link = True


@admin.register(Author)
class AuthorAdmin(admin.ModelAdmin):
    list_display = ("last_name", "first_name", "book_count")
    search_fields = ("first_name", "last_name")
    inlines = (BookInline,)

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_book_count=Count("books"))

    @admin.display(description="图书数量", ordering="_book_count")
    def book_count(self, obj):
        return obj._book_count


class BorrowInline(admin.TabularInline):
    model = BorrowRecord
    extra = 0
    fields = ("borrower", "borrowed_at", "due_date", "returned_at")
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Book)
class BookAdmin(admin.ModelAdmin):
    list_display = ("title", "author", "price", "price_with_tax", "status", "created_at")
    list_filter = ("status", "author", "genres")
    search_fields = ("title", "isbn", "author__first_name", "author__last_name")
    prepopulated_fields = {"slug": ("title",)}
    date_hierarchy = "created_at"
    # 生成列与数据库默认值均为只读事实
    readonly_fields = ("price_with_tax", "created_at")
    list_select_related = ("author",)
    inlines = (BorrowInline,)
    list_per_page = 20

    @admin.action(description="标记为已下架")
    def mark_retired(self, request, queryset):
        queryset.update(status=Status.RETIRED)

    actions = (mark_retired,)


@admin.register(BorrowRecord)
class BorrowRecordAdmin(admin.ModelAdmin):
    list_display = ("book", "borrower", "borrowed_at", "due_date", "returned_at", "is_active")
    list_select_related = ("book", "borrower")
    search_fields = ("book__title", "borrower__username")
    date_hierarchy = "borrowed_at"
    list_filter = ("book__status",)
