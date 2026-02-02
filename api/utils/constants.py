ROLE_PERMISSIONS = {
    "OWNER": [
        "manage_users",
        "manage_roles",
        "view_reports",
        "manage_stock",
        "manage_sales",
        "manage_settings",
    ],
    "ADMIN": [
        "manage_users",
        "view_reports",
        "manage_stock",
        "manage_sales",
    ],
    "PHARMACIST": [
        "view_stock",
        "manage_sales",
    ],
    "CASHIER": [
        "manage_sales",
    ],
    "STORE_KEEPER": [
        "manage_stock",
    ],
    "ACCOUNTANT": [
        "view_reports",
    ],
}

ALL_ROLES = list(ROLE_PERMISSIONS.keys())
