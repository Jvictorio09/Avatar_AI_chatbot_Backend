from django.contrib import admin
from django.urls import path
from myapp.views import chat, health   # <-- import

urlpatterns = [
    path("admin/", admin.site.urls),
    path("chat", chat),     # <-- EXACT: /chat (no trailing slash)
    path("health", health),
]
