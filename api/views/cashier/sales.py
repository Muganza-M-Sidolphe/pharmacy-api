from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from django.core.paginator import Paginator
from django.utils import timezone

from ...models import UserTenant, Sale, StockBatch, Notification
from ...serializers import CreateSaleSerializer, SaleSerializer
from ...utils.subscription_access import authorize_tenant_access
from drf_spectacular.utils import extend_schema


class CashierSalesBaseView(APIView):
    permission_classes = [IsAuthenticated]
    required_subscription_feature = "sales_management"

    def _authorize(self, request, tenant_id):
        tenant, error_message, error_status = authorize_tenant_access(
            request,
            tenant_id,
            required_feature=self.required_subscription_feature,
        )
        if error_message:
            return None, Response({"detail": error_message}, status=error_status)
        return tenant, None


class CashierCreateSaleView(CashierSalesBaseView):
    """Create a new sale/invoice."""

    @extend_schema(
        description="Create a new sale with medicines and payment details",
        request=CreateSaleSerializer,
        responses=SaleSerializer,
        tags=["cashier"]
    )
    def post(self, request):
        """
        Create a sale with multiple medicines.
        
        Request body:
        {
            "tenantId": "uuid",
            "customerName": "John Doe",
            "customerPhone": "123456789",
            "notes": "Additional notes",
            "paymentOption": "FULL|PARTIAL|CREDIT",
            "paymentMethod": "CASH|CARD|UPI|MOBILE_MONEY|BANK_TRANSFER",
            "discountAmount": 10.50,
            "paidAmount": 100.00,
            "items": [
                {"medicineId": "uuid", "batchId": "uuid", "quantity": 5},
                ...
            ]
        }
        """
        serializer = CreateSaleSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        
        tenant_id = serializer.validated_data['tenantId']
        
        # Check user belongs to tenant
        _, auth_error = self._authorize(request, tenant_id)
        if auth_error:
            return auth_error
        
        sale = serializer.create(serializer.validated_data)
        
        # Notify storekeeper of new sale request
        from ...models import Tenant
        tenant = Tenant.objects.get(id=tenant_id)
        storekeepers = UserTenant.objects.filter(
            tenant_id=tenant_id,
            role__in=['STAFF', 'ADMIN']  # Assuming storekeeper is STAFF or ADMIN
        ).values_list('user_id', flat=True)
        
        for user_id in storekeepers:
            Notification.objects.create(
                tenant_id=tenant_id,
                title="New Sale Request",
                message=f"New sale invoice {sale.invoice_number} created by cashier. Amount: {sale.total_amount} {tenant.currency}",
                recipient_id=user_id
            )
        
        return Response(SaleSerializer(sale).data, status=status.HTTP_201_CREATED)


class CashierSalesListView(CashierSalesBaseView):
    """List cashier's sales with pagination and filters."""

    @extend_schema(
        description="List cashier's sales with filters and pagination",
        responses=SaleSerializer(many=True),
        tags=["cashier"]
    )
    def get(self, request):
        """
        List sales for cashier.
        Query params:
        - tenantId (required)
        - status: PENDING|APPROVED|REJECTED|COMPLETED|CANCELLED
        - page: page number (default 1)
        - page_size: items per page (default 10)
        """
        tenant_id = request.query_params.get('tenantId')
        _, auth_error = self._authorize(request, tenant_id)
        if auth_error:
            return auth_error
        
        page = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', 10))
        status_filter = request.query_params.get('status', '')
        
        qs = Sale.objects.filter(tenant_id=tenant_id, cashier=request.user).order_by('-created_at')
        
        if status_filter:
            qs = qs.filter(status=status_filter)
        
        paginator = Paginator(qs, page_size)
        page_obj = paginator.get_page(page)
        
        data = SaleSerializer(page_obj, many=True).data
        return Response({
            'results': data,
            'pagination': {
                'page': page,
                'page_size': page_size,
                'total_pages': paginator.num_pages,
                'total_items': paginator.count
            }
        })


class CashierSalesDetailView(CashierSalesBaseView):
    """Get sale details."""

    @extend_schema(
        description="Get sale details by ID",
        responses=SaleSerializer,
        tags=["cashier"]
    )
    def get(self, request, sale_id):
        """Get single sale details."""
        tenant_id = request.query_params.get('tenantId')
        _, auth_error = self._authorize(request, tenant_id)
        if auth_error:
            return auth_error
        
        try:
            sale = Sale.objects.get(id=sale_id, tenant_id=tenant_id)
        except Sale.DoesNotExist:
            return Response({"detail": "Sale not found"}, status=status.HTTP_404_NOT_FOUND)
        
        return Response(SaleSerializer(sale).data)


class StorekeeperApproveSaleView(CashierSalesBaseView):
    """Storekeeper approves or rejects a sale."""

    @extend_schema(
        description="Approve a sale (storekeeper only). For PARTIAL invoices with due balance, next approver is OWNER.",
        tags=["storekeeper"]
    )
    def post(self, request, sale_id):
        """
        Approve a sale and deduct stock.
        """
        tenant_id = request.query_params.get('tenantId')
        _, auth_error = self._authorize(request, tenant_id)
        if auth_error:
            return auth_error
        
        try:
            sale = Sale.objects.get(id=sale_id, tenant_id=tenant_id)
        except Sale.DoesNotExist:
            return Response({"detail": "Sale not found"}, status=status.HTTP_404_NOT_FOUND)
        
        if sale.status != 'PENDING':
            return Response(
                {"detail": f"Cannot approve sale with status {sale.status}"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Deduct stock for each item
        for item in sale.items.all():
            batch = item.batch
            if batch.quantity < item.quantity:
                return Response(
                    {"detail": f"Insufficient stock for {item.medicine.brand_name}"},
                    status=status.HTTP_400_BAD_REQUEST
                )
            batch.quantity -= item.quantity
            batch.save()
        
        # Mark as approved
        sale.status = 'APPROVED'
        sale.approved_at = timezone.now()
        sale.approved_by = request.user
        if sale.payment_option == 'PARTIAL' and sale.due_amount > 0:
            sale.owner_approval_status = 'PENDING'
            sale.pharmacist_approval_status = 'PENDING'
        sale.save()
        
        # For partial invoices, owner is next approver in the chain.
        if sale.payment_option == 'PARTIAL' and sale.due_amount > 0:
            owners = UserTenant.objects.filter(tenant_id=tenant_id, role='OWNER')
            for owner in owners:
                Notification.objects.create(
                    tenant_id=tenant_id,
                    title="Partial Invoice Needs Owner Approval",
                    message=f"Invoice {sale.invoice_number} was approved by storekeeper and now requires owner approval.",
                    recipient_id=owner.user_id
                )
        else:
            # Non-partial invoices continue normal flow.
            Notification.objects.create(
                tenant_id=tenant_id,
                title="Sale Approved",
                message=f"Sale invoice {sale.invoice_number} has been approved",
                recipient=sale.cashier
            )
        
        return Response(SaleSerializer(sale).data)


class StorekeeperRejectSaleView(CashierSalesBaseView):
    """Storekeeper rejects a sale."""

    @extend_schema(
        description="Reject a sale (storekeeper only)",
        tags=["storekeeper"]
    )
    def post(self, request, sale_id):
        """Reject a sale."""
        tenant_id = request.query_params.get('tenantId')
        _, auth_error = self._authorize(request, tenant_id)
        if auth_error:
            return auth_error
        
        try:
            sale = Sale.objects.get(id=sale_id, tenant_id=tenant_id)
        except Sale.DoesNotExist:
            return Response({"detail": "Sale not found"}, status=status.HTTP_404_NOT_FOUND)
        
        if sale.status != 'PENDING':
            return Response(
                {"detail": f"Cannot reject sale with status {sale.status}"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Mark as rejected
        sale.status = 'REJECTED'
        sale.save()
        
        # Notify cashier of rejection
        Notification.objects.create(
            tenant_id=tenant_id,
            title="Sale Rejected",
            message=f"Sale invoice {sale.invoice_number} has been rejected",
            recipient=sale.cashier
        )
        
        return Response(SaleSerializer(sale).data)


class StorekeeperPendingSalesView(CashierSalesBaseView):
    """List pending sales for storekeeper approval."""

    @extend_schema(
        description="List pending sales awaiting approval (storekeeper only)",
        responses=SaleSerializer(many=True),
        tags=["storekeeper"]
    )
    def get(self, request):
        """List pending sales."""
        tenant_id = request.query_params.get('tenantId')
        _, auth_error = self._authorize(request, tenant_id)
        if auth_error:
            return auth_error
        
        page = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', 10))
        
        qs = Sale.objects.filter(tenant_id=tenant_id, status='PENDING').order_by('-created_at')
        
        paginator = Paginator(qs, page_size)
        page_obj = paginator.get_page(page)
        
        data = SaleSerializer(page_obj, many=True).data
        return Response({
            'results': data,
            'pagination': {
                'page': page,
                'page_size': page_size,
                'total_pages': paginator.num_pages,
                'total_items': paginator.count
            }
        })
