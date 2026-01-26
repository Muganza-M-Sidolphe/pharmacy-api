from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import User, Tenant, UserTenant


# ===============================
# Custom User Admin
# ===============================
class UserAdmin(BaseUserAdmin):
    model = User
    list_display = (
        "email",
        "name",
        "user_code",
        "is_staff",
        "is_super_admin",
        "is_active",
    )
    list_filter = ("is_staff", "is_super_admin", "is_active")
    search_fields = ("email", "name", "user_code")
    ordering = ("email",)

    # Fields to show when viewing a user
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Personal Info", {"fields": ("name", "user_code")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_super_admin", "is_superuser", "groups", "user_permissions")}),
        ("Important dates", {"fields": ("last_login", "created_at")}),
    )

    # Fields to show when adding a user via admin
    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("email", "name", "password1", "password2", "is_active", "is_staff", "is_super_admin"),
        }),
    )


# ===============================
# Tenant Admin
# ===============================
@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "phone", "is_active", "created_at")
    search_fields = ("name", "email", "phone")
    list_filter = ("is_active",)
    ordering = ("name",)


# ===============================
# UserTenant Admin
# ===============================
@admin.register(UserTenant)
class UserTenantAdmin(admin.ModelAdmin):
    list_display = ("user", "tenant", "role")
    search_fields = ("user__email", "tenant__name", "role")
    list_filter = ("role", "tenant")
  