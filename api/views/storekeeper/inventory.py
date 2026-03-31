from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404
from django.utils import timezone
from datetime import timedelta
from django.db.models import Q

from ...models import Medicine, StockBatch, UserTenant
from ...serializers import MedicineSerializer, AddMedicineWithStockSerializer, StockBatchSerializer
from ...utils.subscription_access import authorize_tenant_access
from drf_spectacular.utils import extend_schema


class StorekeeperBaseView(APIView):
    permission_classes = [IsAuthenticated]
    required_subscription_feature = None

    def _authorize(self, request, tenant_id):
        tenant, error_message, error_status = authorize_tenant_access(
            request,
            tenant_id,
            required_feature=self.required_subscription_feature,
        )
        if error_message:
            return None, Response({"detail": error_message}, status=error_status)
        return tenant, None


class InventoryListCreateView(StorekeeperBaseView):
    permission_classes = [IsAuthenticated]
    required_subscription_feature = "inventory_management"

    def _is_wholesale_tenant(self, tenant_id):
        return UserTenant.objects.filter(tenant_id=tenant_id, role="OWNER").exists()

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
        _, auth_error = self._authorize(request, tenant_id)
        if auth_error:
            return auth_error

        medicine = serializer.create(serializer.validated_data)

        return Response(MedicineSerializer(medicine).data, status=status.HTTP_201_CREATED)

    @extend_schema(
        description="List medicines in inventory with search and pagination",
        responses=MedicineSerializer(many=True),
        tags=["storekeeper"]
    )
    def get(self, request):
        tenant_id = request.query_params.get('tenantId')
        _, auth_error = self._authorize(request, tenant_id)
        if auth_error:
            return auth_error

        query = request.query_params.get('query', '').strip()
        page = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', 10))

        qs = Medicine.objects.filter(tenant_id=tenant_id)
        if self._is_wholesale_tenant(tenant_id):
            qs = qs.filter(
                Q(created_by__department="WHOLESALE") | Q(created_by__isnull=True)
            )
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
