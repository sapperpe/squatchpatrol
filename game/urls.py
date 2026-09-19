from django.urls import path
from . import views

app_name = "game"

urlpatterns = [
    path("", views.terminal, name="terminal"),
    path("cmd/", views.command, name="command"),
    path("restart/", views.restart, name="restart"),
]
