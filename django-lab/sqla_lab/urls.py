from django.urls import path

from . import views

app_name = "sqla_lab"

urlpatterns = [
    path("", views.compare, name="compare"),
]
