"""Default permission codes per tenant role.

The codes match the frontend permission catalogue (src/services/permissions.service.js).
They are sent to the frontend in the JWT and login responses so it can show or hide
actions, and the backend checks them again on the endpoints that use them.
"""

from ..models import UserTenant

ALL_PERMISSION_CODES = [
    "medicine.view", "medicine.add", "medicine.edit", "medicine.delete", "medicine.import",
    "inventory.view", "inventory.view_batches", "inventory.receive", "inventory.adjust",
    "inventory.transfer", "inventory.view_movements",
    "orders.view", "orders.approve", "orders.prepare", "orders.deliver",
    "payments.view", "payments.confirm",
    "sales.view", "sales.create",
    "reports.inventory", "reports.expiry", "reports.finance",
    "users.view", "roles.view", "permissions.view",
    "settings.view",
]

ROLE_PERMISSION_CODES = {
    "OWNER": ALL_PERMISSION_CODES,
    "ADMIN": ALL_PERMISSION_CODES,
    "PHARMACIST": [
        "medicine.view", "medicine.add", "medicine.edit", "medicine.import",
        "inventory.view", "inventory.view_batches", "inventory.receive",
        "orders.view", "orders.approve", "orders.prepare",
        "sales.view", "reports.inventory", "reports.expiry",
    ],
    "ACCOUNTANT": [
        "sales.view", "payments.view", "payments.confirm",
        "reports.finance", "reports.inventory",
    ],
    "STORE_KEEPER": [
        "medicine.view", "medicine.add", "medicine.edit", "medicine.import",
        "inventory.view", "inventory.view_batches", "inventory.receive",
        "inventory.adjust", "inventory.transfer", "inventory.view_movements",
        "reports.inventory", "reports.expiry",
    ],
    "CASHIER": [
        "medicine.view", "inventory.view", "sales.view", "sales.create", "payments.view",
    ],
}


def permissions_for_role(role):
    return list(ROLE_PERMISSION_CODES.get(str(role or "").upper(), []))


def user_has_tenant_permission(user, tenant_id, code):
    """True when the user's role in this tenant grants the permission code."""
    if getattr(user, "is_super_admin", False):
        return True
    roles = UserTenant.objects.filter(user=user, tenant_id=tenant_id).values_list("role", flat=True)
    return any(code in ROLE_PERMISSION_CODES.get(role, []) for role in roles)
