# api/utils/permissions.py

ROLE_PERMISSIONS = {
    "OWNER": [
        "manage_users",
        "manage_roles",
        "manage_stock",
        "view_reports",
        "create_pharmacy",
    ],
    "ADMIN": [
        "manage_users",
        "manage_stock",
        "view_reports",
    ],
    "CASHIER": [
        "create_sales",
        "view_own_sales",
    ],
    "STORE_KEEPER": [
        "manage_stock",
    ],
    "ACCOUNTANT": [
        "view_reports",
    ],
    "PHARMACIST": [
        "view_stock",
    ],
}


def has_permission(user, tenant, permission):
    if user.is_super_admin:
        return True

    membership = user.user_tenants.filter(
        tenant=tenant,
        is_active=True
    ).first()

    if not membership:
        return False

    return permission in ROLE_PERMISSIONS.get(membership.role, [])
