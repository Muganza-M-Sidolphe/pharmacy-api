from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import (
    User, Tenant, UserTenant, Notification, Medicine, 
    StockBatch, Sale, SaleItem, ExpenseCategory, Expense
)


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


# ===============================
# Notification Admin
# ===============================
@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("title", "tenant", "recipient", "is_read", "created_at")
    search_fields = ("title", "message", "tenant__name")
    list_filter = ("is_read", "tenant", "created_at")
    ordering = ("-created_at",)


# ===============================
# Medicine Admin
# ===============================
@admin.register(Medicine)
class MedicineAdmin(admin.ModelAdmin):
    list_display = ("brand_name", "generic_name", "manufacturer", "category", "tenant", "created_at")
    search_fields = ("brand_name", "generic_name", "manufacturer", "category")
    list_filter = ("tenant", "category", "created_at")
    ordering = ("brand_name",)


# ===============================
# StockBatch Admin
# ===============================
@admin.register(StockBatch)
class StockBatchAdmin(admin.ModelAdmin):
    list_display = ("medicine", "batch_number", "quantity", "selling_price", "expiry_date", "created_at")
    search_fields = ("medicine__brand_name", "batch_number", "supplier_name")
    list_filter = ("medicine__tenant", "expiry_date", "created_at")
    ordering = ("-created_at",)


# ===============================
# Sale Admin
# ===============================
@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = ("invoice_number", "tenant", "cashier", "customer_name", "status", "total_amount", "created_at")
    search_fields = ("invoice_number", "customer_name", "customer_phone")
    list_filter = ("status", "payment_option", "payment_method", "tenant", "created_at")
    ordering = ("-created_at",)


# ===============================
# SaleItem Admin
# ===============================
@admin.register(SaleItem)
class SaleItemAdmin(admin.ModelAdmin):
    list_display = ("sale", "medicine", "batch", "quantity", "unit_price", "subtotal")
    search_fields = ("sale__invoice_number", "medicine__brand_name")
    list_filter = ("sale__tenant", "created_at")
    ordering = ("-created_at",)


# ===============================
# ExpenseCategory Admin
# ===============================
@admin.register(ExpenseCategory)
class ExpenseCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "tenant", "created_at")
    search_fields = ("name",)
    list_filter = ("tenant",)
    ordering = ("name",)


# ===============================
# Expense Admin
# ===============================
@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ("tenant", "category", "amount", "expense_date", "created_by", "created_at")
    search_fields = ("description", "category__name")
    list_filter = ("tenant", "category", "expense_date", "created_at")
    ordering = ("-expense_date",)


# Register User with custom admin
admin.site.register(User, UserAdmin)
  