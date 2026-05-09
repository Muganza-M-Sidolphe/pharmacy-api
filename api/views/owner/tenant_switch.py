from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum
from django.db.models.functions import Coalesce
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ...models import Medicine, Sale, StockBatch, UserTenant
from ...utils.jwt import generate_token


LOW_STOCK_THRESHOLD = 10


class OwnerTenantsListView(APIView):
    """List institutions(pharmacies) where logged user is OWNER."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="List all owner tenants and lightweight stats for switcher dropdown/cards",
        tags=["owner"],
    )
    def get(self, request):
        owner_tenants = (
            UserTenant.objects.filter(user=request.user, role="OWNER", tenant__is_active=True)
            .select_related("tenant")
            .order_by("tenant__name")
        )

        results = []
        for owner_tenant in owner_tenants:
            tenant = owner_tenant.tenant
            results.append(
                {
                    "tenantId": str(tenant.id),
                    "tenantName": tenant.name,
                    "currency": tenant.currency,
                    "email": tenant.email,
                    "phone": tenant.phone,
                    "isActive": tenant.is_active,
                    "stats": self._build_tenant_stats(tenant.id),
                }
            )

        return Response(
            {
                "count": len(results),
                "results": results,
            },
            status=status.HTTP_200_OK,
        )

    def _build_tenant_stats(self, tenant_id):
        today = timezone.now().date()
        expiring_cutoff = today + timedelta(days=30)

        total_medicines = Medicine.objects.filter(tenant_id=tenant_id).count()
        low_stock_items = (
            Medicine.objects.filter(tenant_id=tenant_id)
            .annotate(total_stock=Coalesce(Sum("batches__quantity"), 0))
            .filter(total_stock__lt=LOW_STOCK_THRESHOLD)
            .count()
        )
        total_sales = (
            Sale.objects.filter(tenant_id=tenant_id, status__in=["APPROVED", "COMPLETED"])
            .exclude(cashier__department="RETAIL")
            .aggregate(total=Coalesce(Sum("total_amount"), Decimal("0.00")))
            .get("total")
            or Decimal("0.00")
        )
        expiring_soon = (
            StockBatch.objects.filter(
                medicine__tenant_id=tenant_id,
                quantity__gt=0,
                expiry_date__isnull=False,
                expiry_date__gte=today,
                expiry_date__lte=expiring_cutoff,
            )
            .values("medicine_id")
            .distinct()
            .count()
        )

        return {
            "totalMedicines": total_medicines,
            "lowStockItems": low_stock_items,
            "totalSales": str(total_sales),
            "expiringSoon": expiring_soon,
        }


class OwnerSwitchTenantView(APIView):
    """Switch current owner context to another owned tenant and return fresh token."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Switch owner to selected tenant and return a fresh token scoped to that tenant",
        tags=["owner"],
    )
    def post(self, request):
        tenant_id = request.data.get("tenantId")
        if not tenant_id:
            return Response(
                {"detail": "tenantId is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        owner_tenant = (
            UserTenant.objects.filter(
                user=request.user,
                tenant_id=tenant_id,
                role="OWNER",
                tenant__is_active=True,
            )
            .select_related("tenant")
            .first()
        )

        if not owner_tenant:
            return Response(
                {"detail": "You do not own this institution"},
                status=status.HTTP_403_FORBIDDEN,
            )

        tenant = owner_tenant.tenant
        token = generate_token(user=request.user, tenant=tenant, role="OWNER")

        return Response(
            {
                "message": "Tenant switched successfully",
                "data": {
                    "tenant": {
                        "id": str(tenant.id),
                        "name": tenant.name,
                        "currency": tenant.currency,
                    },
                    "role": "OWNER",
                    "token": token,
                    "stats": OwnerTenantsListView()._build_tenant_stats(tenant.id),
                },
            },
            status=status.HTTP_200_OK,
        )
