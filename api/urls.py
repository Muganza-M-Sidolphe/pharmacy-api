from django.urls import path
from .views import RegisterTenantView, LoginView, LogoutView

urlpatterns = [
    path("register-tenant/", RegisterTenantView.as_view()),
    path("login/", LoginView.as_view()),
    path("logout/", LogoutView.as_view()),
]
