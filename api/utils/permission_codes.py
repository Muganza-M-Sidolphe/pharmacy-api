"""Permission codes, default role permissions, and each user's effective permissions.

The codes match the frontend permission catalogue (src/services/permissions.service.js).
A user's permissions in a pharmacy come from, in order:
  1. OWNER: always every permission.
  2. Permissions set for that user only (UserTenant.permission_overrides).
  3. Their custom role (UserTenant.custom_role).
  4. The pharmacy's customized version of their system role (TenantRole, is_system=True).
  5. The defaults below.
They are sent to the frontend in the JWT and login responses so it can show or hide
actions, and the backend checks them again on the endpoints that use them.
"""

from ..models import TenantRole, UserTenant

PERMISSION_GROUPS = [
    ("Medicine", [
        ("medicine.view", "View medicine"), ("medicine.add", "Add medicine"),
        ("medicine.edit", "Edit medicine"), ("medicine.delete", "Delete medicine"),
        ("medicine.import", "Import medicine"),
    ]),
    ("Inventory", [
        ("inventory.view", "View inventory"), ("inventory.view_batches", "View batches"),
        ("inventory.receive", "Receive stock"), ("inventory.adjust", "Adjust stock"),
        ("inventory.transfer", "Transfer stock"), ("inventory.view_movements", "View stock movements"),
    ]),
    ("Orders", [
        ("orders.view", "View orders"), ("orders.approve", "Approve orders"),
        ("orders.prepare", "Prepare orders"), ("orders.deliver", "Deliver orders"),
    ]),
    ("Payments", [("payments.view", "View payments"), ("payments.confirm", "Confirm payments")]),
    ("Sales", [("sales.view", "View sales"), ("sales.create", "Create sales"), ("sales.refund", "Process refunds")]),
    ("Reports", [
        ("reports.inventory", "Inventory reports"), ("reports.expiry", "Expiry reports"),
        ("reports.finance", "Finance reports"),
    ]),
    ("Users", [
        ("users.view", "View users"), ("users.create", "Create users"), ("users.edit", "Edit users"),
        ("users.assign_role", "Assign roles"), ("users.manage_permissions", "Manage user permissions"),
    ]),
    ("Settings", [
        ("settings.view", "View settings"), ("settings.manage", "Manage settings"),
        ("roles.view", "View roles"), ("roles.manage", "Manage roles"),
        ("permissions.view", "View permissions"),
    ]),
]

ALL_PERMISSION_CODES = [code for _, permissions in PERMISSION_GROUPS for code, _ in permissions]

# Selecting a permission also selects what it depends on (mirrors the frontend).
PERMISSION_DEPENDENCIES = {
    "inventory.adjust": ["inventory.view"],
    "inventory.receive": ["inventory.view"],
    "inventory.transfer": ["inventory.view"],
    "orders.approve": ["orders.view"],
    "orders.prepare": ["orders.view"],
    "orders.deliver": ["orders.view"],
    "payments.confirm": ["payments.view"],
}

# System roles: display name, description and default permissions.
SYSTEM_ROLES = {
    "OWNER": ("Owner", "Full pharmacy ownership and administration.", ALL_PERMISSION_CODES),
    "PHARMACIST": ("Pharmacist", "Manages medicines and patient orders.", [
        "medicine.view", "medicine.add", "medicine.edit", "medicine.import",
        "inventory.view", "inventory.view_batches", "inventory.receive",
        "orders.view", "orders.approve", "orders.prepare",
        "sales.view", "reports.inventory", "reports.expiry",
    ]),
    "ACCOUNTANT": ("Accountant", "Manages payments, sales, and financial reports.", [
        "sales.view", "payments.view", "payments.confirm",
        "reports.finance", "reports.inventory",
    ]),
    "STORE_KEEPER": ("Storekeeper", "Manages inventory and stock movements.", [
        "medicine.view", "medicine.add", "medicine.edit", "medicine.import",
        "inventory.view", "inventory.view_batches", "inventory.receive",
        "inventory.adjust", "inventory.transfer", "inventory.view_movements",
        "reports.inventory", "reports.expiry",
    ]),
    "CASHIER": ("Cashier", "Processes sales and payments.", [
        "medicine.view", "inventory.view", "sales.view", "sales.create", "payments.view",
    ]),
}

ROLE_PERMISSION_CODES = {role: codes for role, (_, _, codes) in SYSTEM_ROLES.items()}
ROLE_PERMISSION_CODES["ADMIN"] = ALL_PERMISSION_CODES


def clean_permission_codes(codes):
    """Known codes only, with their dependencies, in catalogue order."""
    selected = {code for code in codes or [] if code in ALL_PERMISSION_CODES}
    for code in list(selected):
        selected.update(PERMISSION_DEPENDENCIES.get(code, []))
    return [code for code in ALL_PERMISSION_CODES if code in selected]


def permissions_for_role(role):
    return list(ROLE_PERMISSION_CODES.get(str(role or "").upper(), []))


def role_permissions(tenant_id, role):
    """A system role's permissions in this pharmacy (its customized version, else the defaults)."""
    if role in ("OWNER", "ADMIN"):
        return list(ALL_PERMISSION_CODES)
    customized = TenantRole.objects.filter(tenant_id=tenant_id, is_system=True, base_role=role).first()
    return list(customized.permissions) if customized else permissions_for_role(role)


def membership_permissions(membership):
    if membership.role in ("OWNER", "ADMIN"):
        return list(ALL_PERMISSION_CODES)
    if membership.permission_overrides is not None:
        return clean_permission_codes(membership.permission_overrides)
    if membership.custom_role_id:
        return clean_permission_codes(membership.custom_role.permissions)
    return role_permissions(membership.tenant_id, membership.role)


def user_permissions(user, tenant_id):
    membership = UserTenant.objects.filter(user=user, tenant_id=tenant_id).select_related("custom_role").first()
    return membership_permissions(membership) if membership else []


def user_has_tenant_permission(user, tenant_id, code):
    """True when the user's access in this tenant includes the permission code."""
    if getattr(user, "is_super_admin", False):
        return True
    memberships = UserTenant.objects.filter(user=user, tenant_id=tenant_id).select_related("custom_role")
    return any(code in membership_permissions(m) for m in memberships)
