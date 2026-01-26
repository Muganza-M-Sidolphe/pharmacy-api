from rest_framework_simplejwt.tokens import RefreshToken

def generate_token(*, user, tenant_id=None, role=None, is_super_admin=False):
    refresh = RefreshToken.for_user(user)

    # 🔥 ALWAYS stringify UUIDs
    refresh["user_id"] = str(user.id)
    refresh["tenant_id"] = str(tenant_id) if tenant_id else None
    refresh["role"] = role
    refresh["is_super_admin"] = is_super_admin

    return {
        "refresh": str(refresh),
        "access": str(refresh.access_token),
    }
