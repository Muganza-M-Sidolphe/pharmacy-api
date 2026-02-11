from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404
from django.utils import timezone
from datetime import timedelta

from ...models import Medicine, StockBatch, UserTenant
from ...serializers import MedicineSerializer, AddMedicineWithStockSerializer, StockBatchSerializer
from drf_spectacular.utils import extend_schema


class InventoryListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Add a new medicine to inventory with initial stock batch",
        request=AddMedicineWithStockSerializer, 
        responses=MedicineSerializer,
        tags=["storekeeper"]
    )
    def post(self, request):
        serializer = AddMedicineWithStockSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        tenant_id = serializer.validated_data['tenantId']
        # check user belongs to tenant
        if not UserTenant.objects.filter(user=request.user, tenant_id=tenant_id).exists():
            return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        medicine = serializer.create(serializer.validated_data)

        return Response(MedicineSerializer(medicine).data, status=status.HTTP_201_CREATED)

    @extend_schema(
        description="List medicines in inventory with search and pagination",
        responses=MedicineSerializer(many=True),
        tags=["storekeeper"]
    )
    def get(self, request):
        tenant_id = request.query_params.get('tenantId')
        if not tenant_id:
            return Response({"detail": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        if not UserTenant.objects.filter(user=request.user, tenant_id=tenant_id).exists():
            return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        query = request.query_params.get('query', '').strip()
        page = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', 10))

        qs = Medicine.objects.filter(tenant_id=tenant_id)
        if query:
            qs = qs.filter(brand_name__icontains=query) | qs.filter(generic_name__icontains=query)

        paginator = Paginator(qs.order_by('-created_at'), page_size)
        page_obj = paginator.get_page(page)

        data = [MedicineSerializer(m).data for m in page_obj]

        return Response({
            'results': data,
            'pagination': {
                'page': page,
                'page_size': page_size,
                'total_pages': paginator.num_pages,
                'total_items': paginator.count
            }
        })
# Storekeeper inventory views