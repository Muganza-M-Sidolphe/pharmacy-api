from django.urls import path
from .views import RegisterTenantView,RegisterOwnerView, LoginView, LogoutView,SelectTenantView,CreateUserView, OwnerUserListView,OwnerUpdateUserView,OwnerUserStatusView,OwnerResetUserPasswordView,UsersSummaryView,SearchUsersView,RolesListView 

urlpatterns = [
    path("register-tenant/", RegisterTenantView.as_view()),
    path("register-owner/", RegisterOwnerView.as_view()),
    path("login/", LoginView.as_view(),name="login"),
    path("logout/", LogoutView.as_view()),
    path("select-tenant/", SelectTenantView.as_view(),name="select-tenant"),
    path("owner/create-user/", CreateUserView.as_view(),name="create-user"),
    path("owner/users/", OwnerUserListView.as_view(), name="users"),
    path("owner/users/<user_id>/", OwnerUpdateUserView.as_view(), name="update-user"),
    path("owner/users/<user_id>/status/", OwnerUserStatusView.as_view(), name="user-status"),
    path("owner/users/<user_id>/reset-password/", OwnerResetUserPasswordView.as_view(), name="reset-password"),
    path("tenants/<uuid:tenant_id>/users/summary/", UsersSummaryView.as_view(), name="users-summary"),
    path("roles/", RolesListView.as_view(), name="roles-list"),
    path("users/search/", SearchUsersView.as_view(), name="search-users"),
]
