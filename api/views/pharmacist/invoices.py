# Pharmacist Invoice Views
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework import status
from datetime import datetime, date
from decimal import Decimal
from django.db.models import Q, Sum, Count, F
from django.db.models.functions import TruncDate
from drf_spectacular.utils import extend_schema
from api.models import Sale, SaleItem, UserTenant, Notification
from api.serializers import (
    PharmacistInvoicesListSerializer,
    PharmacistInvoiceDetailSerializer,
    PharmacistInvoicesSummarySerializer,
    PharmacistApproveInvoiceSerializer,
    PharmacistPaymentApprovalsListSerializer,
    PharmacistApprovePaymentSerializer,
    PharmacistInvoiceQuickStatsSerializer,
)


class PharmacistInvoicesListView(APIView):
    """Get all invoices with filtering"""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Get list of invoices with filters for pharmacist",
        parameters=[
            {"name": "tenantId", "in": "query", "required": True, "schema": {"type": "string"}},
            {"name": "status", "in": "query", "schema": {"type": "string", "enum": ["PENDING", "APPROVED", "COMPLETED", "CANCELLED"]}},
            {"name": "paymentStatus", "in": "query", "schema": {"type": "string", "enum": ["PAID", "PARTIAL", "UNPAID"]}},
            {"name": "approvalStatus", "in": "query", "schema": {"type": "string", "enum": ["PENDING", "APPROVED", "REJECTED"]}},
            {"name": "search", "in": "query", "schema": {"type": "string"}},
            {"name": "startDate", "in": "query", "schema": {"type": "string", "format": "date"}},
            {"name": "endDate", "in": "query", "schema": {"type": "string", "format": "date"}},
            {"name": "page", "in": "query", "schema": {"type": "integer", "default": 1}},
            {"name": "pageSize", "in": "query", "schema": {"type": "integer", "default": 10}},
        ],
        responses={200: PharmacistInvoicesListSerializer()},
    )
    def get(self, request):
        tenant_id = request.query_params.get("tenantId")
        status_filter = request.query_params.get("status")
        payment_status = request.query_params.get("paymentStatus")
        approval_status = request.query_params.get("approvalStatus")
        search = request.query_params.get("search", "").strip()
        start_date = request.query_params.get("startDate")
        end_date = request.query_params.get("endDate")
        page = int(request.query_params.get("page", 1))
        page_size = int(request.query_params.get("pageSize", 10))

        if not tenant_id:
            return Response({"error": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            UserTenant.objects.get(user=request.user, tenant_id=tenant_id)
        except UserTenant.DoesNotExist:
            return Response({"error": "Not authorized for this tenant"}, status=status.HTTP_403_FORBIDDEN)

        # Base query - invoices that are approved by accountant and visible to pharmacist
        invoices = Sale.objects.filter(
            tenant_id=tenant_id, status__in=["APPROVED", "COMPLETED"]
        ).order_by('-created_at')

        # Apply filters
        if status_filter:
            invoices = invoices.filter(status=status_filter)

        if payment_status:
            if payment_status == "PAID":
                invoices = invoices.filter(due_amount=0)
            elif payment_status == "PARTIAL":
                invoices = invoices.filter(paid_amount__gt=0, due_amount__gt=0)
            elif payment_status == "UNPAID":
                invoices = invoices.filter(paid_amount=0, due_amount__gt=0)

        if search:
            invoices = invoices.filter(
                Q(invoice_number__icontains=search) |
                Q(customer_name__icontains=search) |
                Q(customer_phone__icontains=search)
            )

        if start_date:
            invoices = invoices.filter(created_at__date__gte=start_date)
        if end_date:
            invoices = invoices.filter(created_at__date__lte=end_date)

        # Pagination
        total_count = invoices.count()
        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size
        paginated = invoices[start_idx:end_idx]

        items = []
        for sale in paginated:
            item_count = sale.items.count()
            items.append({
                'id': sale.id,
                'invoiceNumber': sale.invoice_number,
                'customerName': sale.customer_name,
                'customerPhone': sale.customer_phone,
                'totalAmount': sale.total_amount,
                'paidAmount': sale.paid_amount,
                'dueAmount': sale.due_amount,
                'paymentMethod': sale.payment_method,
                'paymentOption': sale.payment_option,
                'status': sale.status,
                'approvalStatus': 'APPROVED' if sale.status in ['APPROVED', 'COMPLETED'] else 'PENDING',
                'itemCount': item_count,
                'createdAt': sale.created_at,
            })

        response_data = {
            'count': total_count,
            'next': f"/api/pharmacist/invoices/?page={page + 1}&tenantId={tenant_id}" if end_idx < total_count else None,
            'previous': f"/api/pharmacist/invoices/?page={page - 1}&tenantId={tenant_id}" if page > 1 else None,
            'results': items,
        }

        serializer = PharmacistInvoicesListSerializer(response_data)
        return Response(serializer.data)


class PharmacistInvoiceDetailView(APIView):
    """Get invoice detail with items"""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Get invoice detail with all items",
        parameters=[
            {"name": "tenantId", "in": "query", "required": True, "schema": {"type": "string"}},
            {"name": "invoice_id", "in": "path", "required": True, "schema": {"type": "string"}},
        ],
        responses={200: PharmacistInvoiceDetailSerializer()},
    )
    def get(self, request, invoice_id):
        tenant_id = request.query_params.get("tenantId")

        if not tenant_id:
            return Response({"error": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            UserTenant.objects.get(user=request.user, tenant_id=tenant_id)
        except UserTenant.DoesNotExist:
            return Response({"error": "Not authorized for this tenant"}, status=status.HTTP_403_FORBIDDEN)

        try:
            sale = Sale.objects.get(id=invoice_id, tenant_id=tenant_id)
        except Sale.DoesNotExist:
            return Response({"error": "Invoice not found"}, status=status.HTTP_404_NOT_FOUND)

        items = []
        for item in sale.items.all():
            items.append({
                'id': item.id,
                'medicineName': item.medicine.brand_name,
                'medicineCode': item.medicine.code,
                'quantity': item.quantity,
                'unitPrice': item.unit_price,
                'subtotal': item.subtotal,
            })

        invoice_data = {
            'id': sale.id,
            'invoiceNumber': sale.invoice_number,
            'customerName': sale.customer_name,
            'customerPhone': sale.customer_phone,
            'totalAmount': sale.total_amount,
            'paidAmount': sale.paid_amount,
            'dueAmount': sale.due_amount,
            'discountAmount': sale.discount_amount,
            'paymentMethod': sale.payment_method,
            'paymentOption': sale.payment_option,
            'status': sale.status,
            'approvalStatus': 'APPROVED' if sale.status in ['APPROVED', 'COMPLETED'] else 'PENDING',
            'items': items,
            'createdAt': sale.created_at,
            'approvedAt': sale.approved_at,
        }

        serializer = PharmacistInvoiceDetailSerializer(invoice_data)
        return Response(serializer.data)


class PharmacistInvoicesSummaryView(APIView):
    """Get invoices summary statistics"""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Get invoices summary: totals, collected, outstanding",
        parameters=[{"name": "tenantId", "in": "query", "required": True, "schema": {"type": "string"}}],
        responses={200: PharmacistInvoicesSummarySerializer()},
    )
    def get(self, request):
        tenant_id = request.query_params.get("tenantId")

        if not tenant_id:
            return Response({"error": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            UserTenant.objects.get(user=request.user, tenant_id=tenant_id)
        except UserTenant.DoesNotExist:
            return Response({"error": "Not authorized for this tenant"}, status=status.HTTP_403_FORBIDDEN)

        today = date.today()

        # All invoices approved by accountant
        invoices = Sale.objects.filter(tenant_id=tenant_id, status__in=["APPROVED", "COMPLETED"])
        todays_invoices = invoices.filter(created_at__date=today)

        total_invoices = invoices.count()
        invoices_today = todays_invoices.count()

        total_revenue = invoices.aggregate(Sum('total_amount'))['total_amount__sum'] or Decimal('0')
        total_collected = invoices.aggregate(Sum('paid_amount'))['paid_amount__sum'] or Decimal('0')
        outstanding = invoices.aggregate(Sum('due_amount'))['due_amount__sum'] or Decimal('0')

        unpaid_invoices = invoices.filter(due_amount__gt=0).count()
        pending_approval = Sale.objects.filter(tenant_id=tenant_id, status="PENDING").count()
        approved_invoices = invoices.count()

        summary_data = {
            'totalInvoices': total_invoices,
            'invoicesToday': invoices_today,
            'totalRevenue': total_revenue,
            'totalCollected': total_collected,
            'outstanding': outstanding,
            'unpaidInvoices': unpaid_invoices,
            'pendingApproval': pending_approval,
            'approvedInvoices': approved_invoices,
        }

        serializer = PharmacistInvoicesSummarySerializer(summary_data)
        return Response(serializer.data)


class PharmacistApproveInvoiceView(APIView):
    """Pharmacist approves invoice (records approval)"""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Pharmacist approves invoice after accountant approval",
        parameters=[
            {"name": "tenantId", "in": "query", "required": True, "schema": {"type": "string"}},
            {"name": "invoice_id", "in": "path", "required": True, "schema": {"type": "string"}},
        ],
        request=PharmacistApproveInvoiceSerializer(),
        responses={200: PharmacistInvoiceDetailSerializer()},
    )
    def post(self, request, invoice_id):
        tenant_id = request.query_params.get("tenantId")

        if not tenant_id:
            return Response({"error": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            UserTenant.objects.get(user=request.user, tenant_id=tenant_id)
        except UserTenant.DoesNotExist:
            return Response({"error": "Not authorized for this tenant"}, status=status.HTTP_403_FORBIDDEN)

        try:
            sale = Sale.objects.get(id=invoice_id, tenant_id=tenant_id)
        except Sale.DoesNotExist:
            return Response({"error": "Invoice not found"}, status=status.HTTP_404_NOT_FOUND)

        # Only allow approval if invoice is approved by accountant and not already completed
        if sale.status not in ["APPROVED", "COMPLETED"]:
            return Response({"error": "Invoice must be approved by accountant first"}, status=status.HTTP_400_BAD_REQUEST)

        # Mark as completed (final approval)
        sale.status = "COMPLETED"
        sale.save()

        # Create notification for accountant
        Notification.objects.create(
            tenant_id=tenant_id,
            title="Invoice Approved",
            message=f"Pharmacist approved invoice {sale.invoice_number}",
            recipient=sale.approved_by,
        )

        # Get updated invoice
        items = []
        for item in sale.items.all():
            items.append({
                'id': item.id,
                'medicineName': item.medicine.brand_name,
                'medicineCode': item.medicine.code,
                'quantity': item.quantity,
                'unitPrice': item.unit_price,
                'subtotal': item.subtotal,
            })

        invoice_data = {
            'id': sale.id,
            'invoiceNumber': sale.invoice_number,
            'customerName': sale.customer_name,
            'customerPhone': sale.customer_phone,
            'totalAmount': sale.total_amount,
            'paidAmount': sale.paid_amount,
            'dueAmount': sale.due_amount,
            'discountAmount': sale.discount_amount,
            'paymentMethod': sale.payment_method,
            'paymentOption': sale.payment_option,
            'status': sale.status,
            'approvalStatus': 'APPROVED',
            'items': items,
            'createdAt': sale.created_at,
            'approvedAt': sale.approved_at,
        }

        serializer = PharmacistInvoiceDetailSerializer(invoice_data)
        return Response(serializer.data)


class PharmacistPaymentApprovalsListView(APIView):
    """Get payment approvals pending pharmacist review"""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Get payment approval requests pending pharmacist action",
        parameters=[
            {"name": "tenantId", "in": "query", "required": True, "schema": {"type": "string"}},
            {"name": "status", "in": "query", "schema": {"type": "string", "enum": ["PENDING", "APPROVED", "REJECTED"]}},
            {"name": "page", "in": "query", "schema": {"type": "integer", "default": 1}},
            {"name": "pageSize", "in": "query", "schema": {"type": "integer", "default": 10}},
        ],
        responses={200: PharmacistPaymentApprovalsListSerializer()},
    )
    def get(self, request):
        tenant_id = request.query_params.get("tenantId")
        status_filter = request.query_params.get("status", "PENDING")
        page = int(request.query_params.get("page", 1))
        page_size = int(request.query_params.get("pageSize", 10))

        if not tenant_id:
            return Response({"error": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            UserTenant.objects.get(user=request.user, tenant_id=tenant_id)
        except UserTenant.DoesNotExist:
            return Response({"error": "Not authorized for this tenant"}, status=status.HTTP_403_FORBIDDEN)

        # Get invoices with partial payments pending pharmacist approval
        sales = Sale.objects.filter(
            tenant_id=tenant_id,
            payment_option="PARTIAL",
            due_amount__gt=0,
            status="APPROVED"
        ).order_by('-created_at')

        if status_filter:
            # Filter by approval status logic
            if status_filter == "PENDING":
                sales = sales.filter(paid_amount__gt=0, due_amount__gt=0)

        # Pagination
        total_count = sales.count()
        pending_count = sales.filter(paid_amount__gt=0, due_amount__gt=0).count()
        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size
        paginated = sales[start_idx:end_idx]

        items = []
        for sale in paginated:
            items.append({
                'id': sale.id,
                'invoiceNumber': sale.invoice_number,
                'customerName': sale.customer_name,
                'requestedAmount': sale.due_amount,
                'paymentMethod': sale.payment_method,
                'status': 'PENDING' if sale.due_amount > 0 else 'APPROVED',
                'requestedAt': sale.approved_at or sale.created_at,
                'createdAt': sale.created_at,
            })

        response_data = {
            'count': total_count,
            'pending': pending_count,
            'results': items,
        }

        serializer = PharmacistPaymentApprovalsListSerializer(response_data)
        return Response(serializer.data)


class PharmacistApprovePaymentView(APIView):
    """Pharmacist approves payment/records payment received"""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Pharmacist approves or rejects payment for partial payment invoices",
        parameters=[
            {"name": "tenantId", "in": "query", "required": True, "schema": {"type": "string"}},
            {"name": "invoice_id", "in": "path", "required": True, "schema": {"type": "string"}},
        ],
        request=PharmacistApprovePaymentSerializer(),
        responses={200: PharmacistInvoiceDetailSerializer()},
    )
    def post(self, request, invoice_id):
        tenant_id = request.query_params.get("tenantId")

        if not tenant_id:
            return Response({"error": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            UserTenant.objects.get(user=request.user, tenant_id=tenant_id)
        except UserTenant.DoesNotExist:
            return Response({"error": "Not authorized for this tenant"}, status=status.HTTP_403_FORBIDDEN)

        try:
            sale = Sale.objects.get(id=invoice_id, tenant_id=tenant_id)
        except Sale.DoesNotExist:
            return Response({"error": "Invoice not found"}, status=status.HTTP_404_NOT_FOUND)

        approval_status = request.data.get("approvalStatus")

        if approval_status == "APPROVED":
            # Record payment as received - mark as completed
            sale.status = "COMPLETED"
            sale.save()

            Notification.objects.create(
                tenant_id=tenant_id,
                title="Payment Approved",
                message=f"Pharmacist approved payment for invoice {sale.invoice_number}",
                recipient=sale.approved_by,
            )
        elif approval_status == "REJECTED":
            # Keep status as APPROVED but payment still pending
            pass

        # Return updated invoice
        items = []
        for item in sale.items.all():
            items.append({
                'id': item.id,
                'medicineName': item.medicine.brand_name,
                'medicineCode': item.medicine.code,
                'quantity': item.quantity,
                'unitPrice': item.unit_price,
                'subtotal': item.subtotal,
            })

        invoice_data = {
            'id': sale.id,
            'invoiceNumber': sale.invoice_number,
            'customerName': sale.customer_name,
            'customerPhone': sale.customer_phone,
            'totalAmount': sale.total_amount,
            'paidAmount': sale.paid_amount,
            'dueAmount': sale.due_amount,
            'discountAmount': sale.discount_amount,
            'paymentMethod': sale.payment_method,
            'paymentOption': sale.payment_option,
            'status': sale.status,
            'approvalStatus': approval_status,
            'items': items,
            'createdAt': sale.created_at,
            'approvedAt': sale.approved_at,
        }

        serializer = PharmacistInvoiceDetailSerializer(invoice_data)
        return Response(serializer.data)


class PharmacistInvoiceQuickStatsView(APIView):
    """Get quick statistics for pharmacist dashboard"""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Get quick stats: low stock, expiring soon, pending approvals",
        parameters=[{"name": "tenantId", "in": "query", "required": True, "schema": {"type": "string"}}],
        responses={200: PharmacistInvoiceQuickStatsSerializer()},
    )
    def get(self, request):
        tenant_id = request.query_params.get("tenantId")

        if not tenant_id:
            return Response({"error": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            UserTenant.objects.get(user=request.user, tenant_id=tenant_id)
        except UserTenant.DoesNotExist:
            return Response({"error": "Not authorized for this tenant"}, status=status.HTTP_403_FORBIDDEN)

        # Low stock - medicines with quantity < 10
        from api.models import Medicine
        low_stock = Medicine.objects.filter(tenant_id=tenant_id, quantity_in_stock__lt=10).count()

        # Expiring soon - stock batches expiring within 30 days
        from api.models import StockBatch
        from datetime import timedelta
        thirty_days_from_now = date.today() + timedelta(days=30)
        expiring_soon = StockBatch.objects.filter(
            medicine__tenant_id=tenant_id,
            expiry_date__lte=thirty_days_from_now,
            expiry_date__gte=date.today()
        ).count()

        # Pending approvals - invoices pending pharmacist approval
        pending_approvals = Sale.objects.filter(
            tenant_id=tenant_id,
            status="APPROVED",
            paid_amount=0,
            due_amount__gt=0
        ).count()

        stats_data = {
            'lowStock': low_stock,
            'expiringSoon': expiring_soon,
            'pendingApprovals': pending_approvals,
        }

        serializer = PharmacistInvoiceQuickStatsSerializer(stats_data)
        return Response(serializer.data)
