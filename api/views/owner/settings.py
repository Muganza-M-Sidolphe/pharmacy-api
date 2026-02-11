from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from django.db import transaction
from ...models import Tenant, UserTenant
from ...permissions import IsOwner


class PharmacySettingsView(APIView):
    permission_classes = [IsAuthenticated, IsOwner]

    def get(self, request):
        """Get pharmacy settings"""
        tenant_id = request.query_params.get("tenantId")

        if not tenant_id:
            return Response(
                {"detail": "tenantId is required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Verify owner owns this pharmacy
        try:
            user_tenant = UserTenant.objects.get(
                user=request.user,
                tenant_id=tenant_id,
                role="OWNER"
            )
        except UserTenant.DoesNotExist:
            return Response(
                {"detail": "Unauthorized access to this pharmacy"},
                status=status.HTTP_403_FORBIDDEN
            )

        # Get tenant details
        tenant = user_tenant.tenant
        
        return Response({
            "id": str(tenant.id),
            "name": tenant.name,
            "email": tenant.email,
            "phone": tenant.phone,
            "address": tenant.address,
            "licenseNumber": tenant.license_number,
            "country": tenant.country,
            "currency": tenant.currency,
            "isActive": tenant.is_active,
            "createdAt": tenant.created_at
        }, status=status.HTTP_200_OK)

    @transaction.atomic
    def patch(self, request):
        """Update pharmacy settings"""
        tenant_id = request.query_params.get("tenantId")

        if not tenant_id:
            return Response(
                {"detail": "tenantId is required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Verify owner owns this pharmacy
        try:
            user_tenant = UserTenant.objects.get(
                user=request.user,
                tenant_id=tenant_id,
                role="OWNER"
            )
        except UserTenant.DoesNotExist:
            return Response(
                {"detail": "Unauthorized access to this pharmacy"},
                status=status.HTTP_403_FORBIDDEN
            )

        tenant = user_tenant.tenant

        # Update allowed fields
        updatable_fields = {
            "name": "name",
            "email": "email",
            "phone": "phone",
            "address": "address",
            "licenseNumber": "license_number",
            "country": "country",
            "currency": "currency",
            "taxRate": None,  # Can be stored separately if needed
        }

        for request_field, model_field in updatable_fields.items():
            if request_field in request.data and model_field:
                setattr(tenant, model_field, request.data[request_field])

        try:
            tenant.save()
        except Exception as e:
            return Response(
                {"detail": f"Error updating settings: {str(e)}"},
                status=status.HTTP_400_BAD_REQUEST
            )

        return Response({
            "message": "Pharmacy settings updated successfully",
            "data": {
                "id": str(tenant.id),
                "name": tenant.name,
                "email": tenant.email,
                "phone": tenant.phone,
                "address": tenant.address,
                "licenseNumber": tenant.license_number,
                "country": tenant.country,
                "currency": tenant.currency,
                "isActive": tenant.is_active,
            }
        }, status=status.HTTP_200_OK)