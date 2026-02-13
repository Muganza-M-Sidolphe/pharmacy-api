from decimal import Decimal

from django.db.models import DecimalField, ExpressionWrapper, F, Q, Sum, Value
from django.db.models.deletion import ProtectedError
from django.db.models.functions import Coalesce
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ...models import Medicine, StockBatch, UserTenant
from ...serializers import AddMedicineWithStockSerializer


LOW_STOCK_THRESHOLD = 10


class OwnerInventoryBaseView(APIView):
    permission_classes = [IsAuthenticated]

    def _get_tenant_id(self, request):
        tenant_id = request.query_params.get("tenantId") or request.data.get("tenantId")
        if not tenant_id:
            return None, Response(
                {"detail": "tenantId is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return tenant_id, None

    def _ensure_owner_access(self, request, tenant_id):
        has_access = UserTenant.objects.filter(
            user=request.user,
            tenant_id=tenant_id,
            role="OWNER",
        ).exists()
        if not has_access:
            return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)
        return None

    def _parse_positive_int(self, raw_value, field_name, default):
        value = raw_value if raw_value is not None else default
        try:
            value = int(value)
        except (TypeError, ValueError):
            return None, Response(
                {"detail": f"{field_name} must be a valid integer"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if value <= 0:
            return None, Response(
                {"detail": f"{field_name} must be greater than 0"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return value, None

    def _build_summary(self, tenant_id):
        medicines = Medicine.objects.filter(tenant_id=tenant_id)

        low_stock_count = (
            medicines.annotate(total_stock=Coalesce(Sum("batches__quantity"), Value(0)))
            .filter(total_stock__lt=LOW_STOCK_THRESHOLD)
            .count()
        )

        inventory_value_expr = ExpressionWrapper(
            F("quantity") * F("selling_price"),
            output_field=DecimalField(max_digits=18, decimal_places=2),
        )
        total_value = (
            StockBatch.objects.filter(medicine__tenant_id=tenant_id)
            .aggregate(total=Coalesce(Sum(inventory_value_expr), Decimal("0.00")))
            .get("total")
            or Decimal("0.00")
        )

        return {
            "totalItems": medicines.count(),
            "lowStock": low_stock_count,
            "totalValue": str(total_value),
            "lowStockThreshold": LOW_STOCK_THRESHOLD,
        }

    def _build_list(self, tenant_id, query, page, page_size):
        medicines = Medicine.objects.filter(tenant_id=tenant_id).annotate(
            total_stock=Coalesce(Sum("batches__quantity"), Value(0))
        )

        if query:
            medicines = medicines.filter(
                Q(brand_name__icontains=query)
                | Q(generic_name__icontains=query)
                | Q(category__icontains=query)
                | Q(manufacturer__icontains=query)
            )

        medicines = medicines.order_by("brand_name", "created_at")

        total_count = medicines.count()
        start = (page - 1) * page_size
        end = start + page_size

        results = []
        for medicine in medicines[start:end]:
            results.append(
                {
                    "medicineId": str(medicine.id),
                    "name": medicine.brand_name,
                    "genericName": medicine.generic_name,
                    "category": medicine.category,
                    "manufacturer": medicine.manufacturer,
                    "unit": medicine.unit,
                    "stock": medicine.total_stock,
                    "isLowStock": medicine.total_stock < LOW_STOCK_THRESHOLD,
                    "actions": {
                        "edit": True,
                        "delete": True,
                    },
                }
            )

        return {
            "count": total_count,
            "next": page + 1 if end < total_count else None,
            "previous": page - 1 if page > 1 else None,
            "results": results,
        }


class OwnerInventoryView(OwnerInventoryBaseView):
    """Owner inventory page endpoint: cards + medicine list, and add medicine."""

    @extend_schema(
        description="Owner inventory page data (summary cards + medicine list)",
        tags=["owner"],
    )
    def get(self, request):
        tenant_id, error = self._get_tenant_id(request)
        if error:
            return error

        access_error = self._ensure_owner_access(request, tenant_id)
        if access_error:
            return access_error

        page, page_error = self._parse_positive_int(
            request.query_params.get("page"), "page", 1
        )
        if page_error:
            return page_error

        page_size, page_size_error = self._parse_positive_int(
            request.query_params.get("pageSize"), "pageSize", 10
        )
        if page_size_error:
            return page_size_error

        query = request.query_params.get("query", "").strip()

        return Response(
            {
                "summary": self._build_summary(tenant_id),
                "medicineList": self._build_list(tenant_id, query, page, page_size),
            },
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        description="Owner add medicine with initial stock batch",
        request=AddMedicineWithStockSerializer,
        tags=["owner"],
    )
    def post(self, request):
        serializer = AddMedicineWithStockSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        tenant_id = str(serializer.validated_data["tenantId"])

        access_error = self._ensure_owner_access(request, tenant_id)
        if access_error:
            return access_error

        medicine = serializer.create(serializer.validated_data)

        return Response(
            {
                "message": "Medicine added successfully",
                "data": {
                    "medicineId": str(medicine.id),
                    "name": medicine.brand_name,
                    "genericName": medicine.generic_name,
                    "category": medicine.category,
                    "manufacturer": medicine.manufacturer,
                    "unit": medicine.unit,
                },
            },
            status=status.HTTP_201_CREATED,
        )


class OwnerInventorySummaryView(OwnerInventoryBaseView):
    """Owner inventory summary cards only."""

    @extend_schema(description="Owner inventory summary cards", tags=["owner"])
    def get(self, request):
        tenant_id, error = self._get_tenant_id(request)
        if error:
            return error

        access_error = self._ensure_owner_access(request, tenant_id)
        if access_error:
            return access_error

        return Response(self._build_summary(tenant_id), status=status.HTTP_200_OK)


class OwnerInventoryMedicineDetailView(OwnerInventoryBaseView):
    """Owner edit/delete single medicine."""

    @extend_schema(description="Get medicine detail for owner", tags=["owner"])
    def get(self, request, medicine_id):
        tenant_id, error = self._get_tenant_id(request)
        if error:
            return error

        access_error = self._ensure_owner_access(request, tenant_id)
        if access_error:
            return access_error

        medicine = Medicine.objects.filter(id=medicine_id, tenant_id=tenant_id).first()
        if not medicine:
            return Response({"detail": "Medicine not found"}, status=status.HTTP_404_NOT_FOUND)

        stock = (
            StockBatch.objects.filter(medicine=medicine)
            .aggregate(total=Coalesce(Sum("quantity"), Value(0)))
            .get("total", 0)
        )

        return Response(
            {
                "medicineId": str(medicine.id),
                "name": medicine.brand_name,
                "genericName": medicine.generic_name,
                "category": medicine.category,
                "manufacturer": medicine.manufacturer,
                "unit": medicine.unit,
                "description": medicine.description,
                "stock": stock,
            },
            status=status.HTTP_200_OK,
        )

    @extend_schema(description="Update medicine basic fields for owner", tags=["owner"])
    def patch(self, request, medicine_id):
        tenant_id, error = self._get_tenant_id(request)
        if error:
            return error

        access_error = self._ensure_owner_access(request, tenant_id)
        if access_error:
            return access_error

        medicine = Medicine.objects.filter(id=medicine_id, tenant_id=tenant_id).first()
        if not medicine:
            return Response({"detail": "Medicine not found"}, status=status.HTTP_404_NOT_FOUND)

        updatable = {
            "name": "brand_name",
            "genericName": "generic_name",
            "manufacturer": "manufacturer",
            "category": "category",
            "unit": "unit",
            "description": "description",
        }

        for request_field, model_field in updatable.items():
            if request_field in request.data:
                setattr(medicine, model_field, request.data.get(request_field))

        medicine.save()

        return Response(
            {
                "message": "Medicine updated successfully",
                "data": {
                    "medicineId": str(medicine.id),
                    "name": medicine.brand_name,
                    "genericName": medicine.generic_name,
                    "category": medicine.category,
                    "manufacturer": medicine.manufacturer,
                    "unit": medicine.unit,
                },
            },
            status=status.HTTP_200_OK,
        )

    @extend_schema(description="Delete medicine for owner", tags=["owner"])
    def delete(self, request, medicine_id):
        tenant_id, error = self._get_tenant_id(request)
        if error:
            return error

        access_error = self._ensure_owner_access(request, tenant_id)
        if access_error:
            return access_error

        medicine = Medicine.objects.filter(id=medicine_id, tenant_id=tenant_id).first()
        if not medicine:
            return Response({"detail": "Medicine not found"}, status=status.HTTP_404_NOT_FOUND)

        try:
            medicine.delete()
        except ProtectedError:
            return Response(
                {
                    "detail": "Medicine cannot be deleted because it is used in existing sales records"
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response({"message": "Medicine deleted successfully"}, status=status.HTTP_200_OK)
