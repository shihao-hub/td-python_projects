"""DRF：Django 思想在 API 层的延伸 —— Serializer ≈ ModelForm，ViewSet ≈ 泛型视图。"""

from rest_framework import serializers

from catalog.models import Author, Book, BorrowRecord


class AuthorSerializer(serializers.ModelSerializer):
    # 属性/注解字段直接读模型，模型仍是唯一事实来源
    full_name = serializers.ReadOnlyField()

    class Meta:
        model = Author
        fields = ("id", "full_name", "about", "book_count")


class BookSerializer(serializers.ModelSerializer):
    # StringRelatedField 走模型的 __str__：显示格式只在一处定义
    author = serializers.StringRelatedField(read_only=True)
    genres = serializers.StringRelatedField(many=True, read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    page_url = serializers.SerializerMethodField()
    is_borrowable = serializers.ReadOnlyField()

    class Meta:
        model = Book
        fields = (
            "id",
            "page_url",
            "title",
            "slug",
            "author",
            "author_id",
            "genres",
            "summary",
            "isbn",
            "price",
            "price_with_tax",
            "status",
            "status_display",
            "is_borrowable",
            "created_at",
        )

    def get_page_url(self, obj: Book) -> str:
        request = self.context.get("request")
        url = obj.get_absolute_url()
        return request.build_absolute_uri(url) if request else url


class BorrowRecordSerializer(serializers.ModelSerializer):
    book = serializers.StringRelatedField(read_only=True)
    borrower = serializers.StringRelatedField(read_only=True)
    is_active = serializers.ReadOnlyField()
    is_overdue = serializers.ReadOnlyField()

    class Meta:
        model = BorrowRecord
        fields = (
            "id",
            "book",
            "book_id",
            "borrower",
            "borrowed_at",
            "due_date",
            "returned_at",
            "is_active",
            "is_overdue",
        )
