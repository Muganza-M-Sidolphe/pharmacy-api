from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from ...models import UserTenant
from ...utils.jwt import generate_token


def _tenant_business_type(tenant):
    tenant_roles = UserTenant.objects.filter(tenant=tenant).values_list("role", flat=True)
    if "OWNER" in tenant_roles:
        return "WHOLESALE"
    if "PHARMACIST" in tenant_roles:
        return "RETAIL"
    return None


class SelectTenantView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        tenant_id = request.data.get("tenantId")

        if not tenant_id:
            return Response(
                {"message": "tenantId is required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            user_tenant = UserTenant.objects.select_related("tenant").get(
                user=request.user,
                tenant_id=tenant_id
            )
        except UserTenant.DoesNotExist:
            return Response(
                {"message": "You do not have access to this pharmacy"},
                status=status.HTTP_403_FORBIDDEN
            )

        tenant = user_tenant.tenant

        token = generate_token(
            user=request.user,
            tenant=tenant,
            role=user_tenant.role
        )

        return Response({
            "message": "Tenant selected successfully",
            "data": {
                "user": {
                    "id": str(request.user.id),
                    "name": request.user.name,
                    "email": request.user.email,
                    "department": request.user.department,
                    "isCollaborativeRetail": (
                        request.user.department == "RETAIL"
                        and _tenant_business_type(tenant) == "WHOLESALE"
                    ),
                    "role": user_tenant.role,
                    "tenant_id": str(tenant.id),
                    "tenant_name": tenant.name
                },
                "token": token
            }
        })
