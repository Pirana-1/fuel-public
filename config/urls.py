from django.conf import settings
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path


urlpatterns = [
    path("admin/", admin.site.urls),
]
if settings.DEBUG:
    urlpatterns.append(path("__reload__/", include("django_browser_reload.urls")))
urlpatterns += [
    path(
        "giris/",
        auth_views.LoginView.as_view(template_name="registration/login.html"),
        name="login",
    ),
    path("cikis/", auth_views.LogoutView.as_view(), name="logout"),
    path("", include("fuel.urls")),
]

