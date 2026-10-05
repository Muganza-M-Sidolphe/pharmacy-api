from rest_framework_simplejwt.tokens import RefreshToken

from .permission_codes import permissions_for_role

def generate_token(user, tenant=None, role=None):
    refresh = RefreshToken.for_user(user)

    refresh["user_id"] = str(user.id)
    refresh["email"] = user.email
    refresh["is_super_admin"] = user.is_super_admin
    refresh["department"] = getattr(user, "department", None)

    if tenant:
        refresh["tenant_id"] = str(tenant.id)
        refresh["tenant_name"] = tenant.name

    if role:
        refresh["role"] = role
        refresh["permissions"] = permissions_for_role(role)

    return {
        "refresh": str(refresh),
        "access": str(refresh.access_token)
    }
