from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from django.core.paginator import Paginator
from decimal import Decimal

from ...models import Medicine
from ...serializers import StockBatchSerializer
from ...utils.subscription_access import authorize_tenant_access
from drf_spectacular.utils import extend_schema


class CashierInventoryListView(APIView):
    """List medicines in inventory for cashier (read-only)."""
    permission_classes = [IsAuthenticated]
    required_subscription_feature = "inventory_management"

    @extend_schema(
        description="List medicines in inventory for cashier with stock and pricing",
        tags=["cashier"]
    )
    def get(self, request):
        tenant_id = request.query_params.get('tenantId')
        _, error_message, error_status = authorize_tenant_access(
            request,
            tenant_id,
            required_feature=self.required_subscription_feature,
        )
        if error_message:
            return Response({"detail": error_message}, status=error_status)

        page = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', 10))
        query = request.query_params.get('query', '').strip()

        qs = Medicine.objects.filter(tenant_id=tenant_id)
        if query:
            qs = qs.filter(brand_name__icontains=query) | qs.filter(generic_name__icontains=query)

        qs = qs.prefetch_related('batches').order_by('-created_at')

        paginator = Paginator(qs, page_size)
        page_obj = paginator.get_page(page)

        medicines_data = []
        for medicine in page_obj:
            batches = StockBatchSerializer(medicine.batches.all(), many=True).data
            total_qty = sum(b['quantity'] for b in batches)
            avg_price = Decimal('0.00')
            if batches and total_qty > 0:
                prices_total = sum(Decimal(b.get('sellingPrice') or b.get('selling_price') or '0') * b.get('quantity', 0) for b in batches)
                avg_price = prices_total / total_qty

            medicines_data.append({
                'id': str(medicine.id),
                'brandName': medicine.brand_name,
                'genericName': medicine.generic_name,
                'manufacturer': medicine.manufacturer,
                'category': medicine.category,
                'unit': medicine.unit,
                'description': medicine.description,
                'totalStock': total_qty,
                'averagePrice': str(avg_price),
                'batches': batches
            })

        return Response({
            'results': medicines_data,
            'pagination': {
                'page': page,
                'page_size': page_size,
                'total_pages': paginator.num_pages,
                'total_items': paginator.count
            }
        })
