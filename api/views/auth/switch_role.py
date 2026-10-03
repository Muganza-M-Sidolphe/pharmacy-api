from django.db import transaction
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ...models import UserTenant
from ...utils.demo import is_demo_user
from ...utils.jwt import generate_token
from .login import _is_collaborative_retail, _tenant_business_type, _tenant_pharmacy_type


# Roles a demo account can switch between. RETAIL is not a tenant role: it acts as
# a PHARMACIST in the RETAIL department (collaborative retail inside a wholesale tenant),
# which is what the frontend routes to the retail dashboard.
DEMO_ROLES = ["OWNER", "CASHIER", "STORE_KEEPER", "ACCOUNTANT", "PHARMACIST", "RETAIL"]
RETAIL_MODE_ROLE = "PHARMACIST"


def _current_demo_role(user, membership):
    if user.department == "RETAIL":
        return "RETAIL"
    return membership.role


class DemoRolesView(APIView):
    """List the roles a demo account can switch to (for the role dropdown)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not is_demo_user(request.user):
            return Response({"canSwitchRole": False, "roles": []})

        tenant_id = request.query_params.get("tenantId")
        if not tenant_id:
            return Response({"message": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        membership = UserTenant.objects.filter(user=request.user, tenant_id=tenant_id).first()
        if not membership:
            return Response({"message": "You do not have access to this pharmacy"}, status=status.HTTP_403_FORBIDDEN)

        return Response({
            "canSwitchRole": True,
            "currentRole": _current_demo_role(request.user, membership),
            "roles": DEMO_ROLES,
        })


class SwitchRoleView(APIView):
    """Switch a demo account to another role in the same pharmacy and return a new token."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        if not is_demo_user(user):
            return Response({"message": "Role switching is only available for demo accounts"}, status=status.HTTP_403_FORBIDDEN)

        tenant_id = request.data.get("tenantId")
        new_role = (request.data.get("role") or "").upper().strip()
        if not tenant_id:
            return Response({"message": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)
        if new_role not in DEMO_ROLES:
            return Response(
                {"message": f"role must be one of: {', '.join(DEMO_ROLES)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        memberships = UserTenant.objects.filter(user=user, tenant_id=tenant_id).select_related("tenant")
        membership = memberships.first()
        if not membership:
            return Response({"message": "You do not have access to this pharmacy"}, status=status.HTTP_403_FORBIDDEN)
        tenant = membership.tenant

        tenant_role = RETAIL_MODE_ROLE if new_role == "RETAIL" else new_role
        # The tenant is WHOLESALE only while it has an OWNER, so another owner must stay.
        if tenant_role != "OWNER":
            other_owner_exists = (
                UserTenant.objects.filter(tenant=tenant, role="OWNER").exclude(user=user).exists()
            )
            if not other_owner_exists:
                return Response(
                    {"message": "This pharmacy needs another OWNER account before the demo account can switch away from OWNER"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        with transaction.atomic():
            memberships.exclude(id=membership.id).delete()
            membership.role = tenant_role
            membership.save(update_fields=["role"])
            user.department = "RETAIL" if new_role == "RETAIL" else "WHOLESALE"
            user.save(update_fields=["department"])

        business_type = _tenant_business_type(tenant)
        token = generate_token(user=user, tenant=tenant, role=tenant_role)

        return Response({
            "status": "OK",
            "mode": "AUTO",
            "data": {
                "token": token,
                "name": user.name,
                "tenant": {
                    "id": str(tenant.id),
                    "name": tenant.name,
                    "currency": tenant.currency,
                    "businessType": business_type,
                    "pharmacyType": _tenant_pharmacy_type(tenant, business_type=business_type),
                },
                "role": tenant_role,
                "demoRole": new_role,
                "department": user.department,
                "isCollaborativeRetail": _is_collaborative_retail(user, tenant, business_type=business_type),
                "canSwitchRole": True,
            },
        })
