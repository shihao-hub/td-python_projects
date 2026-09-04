from django.urls import path
from django.views.generic import RedirectView

from . import views

app_name = "catalog"

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="catalog:books", permanent=False), name="home"),
    path("books/", views.BookListView.as_view(), name="books"),
    path("books/<slug:slug>/", views.BookDetailView.as_view(), name="book-detail"),
    path("books/<slug:slug>/borrow/", views.borrow_book, name="book-borrow"),
    path("authors/", views.AuthorListView.as_view(), name="authors"),
    path("authors/<int:pk>/", views.AuthorDetailView.as_view(), name="author-detail"),
    path("search/", views.book_search, name="search"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("my/loans/", views.my_loans, name="my-loans"),
    path("loans/<int:pk>/return/", views.return_book, name="loan-return"),
]
