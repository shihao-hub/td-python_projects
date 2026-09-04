"""表单层：Django 哲学「校验靠近数据，但给 HTML 一等公民待遇」。

同一个 BorrowForm 服务三处：页面 POST、API 的 borrow 动作、（必要时）admin，
校验逻辑只写一遍 —— ModelForm 直接从模型定义生成字段。
"""

from datetime import date, timedelta

from django import forms
from django.core.exceptions import ValidationError

from .models import BorrowRecord, Status


class BorrowForm(forms.ModelForm):
    due_date = forms.DateField(
        label="应还日期",
        initial=lambda: date.today() + timedelta(days=14),
        widget=forms.DateInput(attrs={"type": "date"}),
        help_text="默认借阅 14 天",
    )

    class Meta:
        model = BorrowRecord
        fields = ["due_date"]

    def __init__(self, *args, book=None, borrower=None, **kwargs):
        self.book = book
        self.borrower = borrower
        super().__init__(*args, **kwargs)

    def clean(self) -> dict:
        cleaned_data = super().clean() or {}
        if self.book is None or self.borrower is None:
            raise ValidationError("缺少借阅上下文（book / borrower）。")
        if self.book.status != Status.AVAILABLE:
            raise ValidationError(
                f"《{self.book.title}》当前状态为 {self.book.get_status_display()}，不可借阅。"
            )
        due_date = cleaned_data.get("due_date")
        if due_date and due_date < date.today():
            raise ValidationError("应还日期不能早于今天。")
        return cleaned_data

    def save(self, commit: bool = True) -> BorrowRecord:
        record = super().save(commit=False)
        record.book = self.book
        record.borrower = self.borrower
        if commit:
            record.save()
        return record
