# Accountant Dashboard Views
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework import status
from datetime import datetime, timedelta, date
from decimal import Decimal
from django.db.models import Q, Sum, Count, F
from django.db.models.functions import TruncDate
from drf_spectacular.utils import extend_schema
from api.models import Sale, UserTenant, Notification
from api.serializers import (
    AccountantDashboardPaymentsSerializer,
    PendingPartialPaymentsSerializer,
    OverduePaymentsSerializer,
    TotalPaidAmountSerializer,
    SelectedForInvoiceListSerializer,
    PartialPaymentRequestsSerializer,
    QuickStatsSerializer,
)


class AccountantDashboardPaymentsView(APIView):
    """Get complete accountant dashboard with all payment-related metrics"""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Get accountant dashboard with pending payments, overdue payments, and payment requests",
        parameters=[{"name": "tenantId", "in": "query", "required": True, "schema": {"type": "string"}}],
        responses={200: AccountantDashboardPaymentsSerializer()},
    )
    def get(self, request):
        tenant_id = request.query_params.get("tenantId")

        if not tenant_id:
            return Response({"error": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        # Authorization check
        try:
            UserTenant.objects.get(user=request.user, tenant_id=tenant_id)
        except UserTenant.DoesNotExist:
            return Response({"error": "Not authorized for this tenant"}, status=status.HTTP_403_FORBIDDEN)

        pending_partial = self._get_pending_partial_payments(tenant_id)
        overdue = self._get_overdue_payments(tenant_id)
        total_paid = self._get_total_paid_amount(tenant_id)
        selected_for_invoice = self._get_selected_for_invoice(tenant_id)
        payment_requests = self._get_partial_payment_requests(tenant_id)
        quick_stats = self._get_quick_stats(tenant_id)

        dashboard_data = {
            "pendingPartialPayments": pending_partial,
            "overduePayments": overdue,
            "totalPaidAmount": total_paid,
            "selectedForInvoice": selected_for_invoice,
            "partialPaymentRequests": payment_requests,
            "quickStats": quick_stats,
        }

        serializer = AccountantDashboardPaymentsSerializer(dashboard_data)
        return Response(serializer.data)

    def _get_pending_partial_payments(self, tenant_id):
        """Get pending partial payments"""
        sales = Sale.objects.filter(
            tenant_id=tenant_id, status="APPROVED", payment_option="PARTIAL", due_amount__gt=0
        ).order_by('-created_at')[:10]

        total_due = sales.aggregate(Sum('due_amount'))['due_amount__sum'] or Decimal('0')

        items = []
        for sale in sales:
            items.append({
                'id': sale.id,
                'invoiceNumber': sale.invoice_number,
                'customerName': sale.customer_name,
                'customerPhone': sale.customer_phone,
                'totalAmount': sale.total_amount,
                'paidAmount': sale.paid_amount,
                'dueAmount': sale.due_amount,
                'createdAt': sale.created_at,
            })

        return {
            'count': len(items),
            'totalDue': total_due,
            'items': items,
        }

    def _get_overdue_payments(self, tenant_id):
        """Get overdue payments (more than 30 days past approved date)"""
        thirty_days_ago = datetime.now() - timedelta(days=30)
        sales = Sale.objects.filter(
            tenant_id=tenant_id, status="APPROVED", due_amount__gt=0, approved_at__lt=thirty_days_ago
        ).order_by('-approved_at')[:10]

        items = []
        now = datetime.now()
        for sale in sales:
            days_overdue = (now - sale.approved_at).days if sale.approved_at else 0
            items.append({
                'id': sale.id,
                'invoiceNumber': sale.invoice_number,
                'customerName': sale.customer_name,
                'totalAmount': sale.total_amount,
                'dueAmount': sale.due_amount,
                'daysOverdue': days_overdue,
                'createdAt': sale.created_at,
            })

        return {
            'count': len(items),
            'items': items,
        }

    def _get_total_paid_amount(self, tenant_id):
        """Get total paid amount from all approved sales"""
        today = date.today()
        period_start = today - timedelta(days=30)

        paid_sales = Sale.objects.filter(
            tenant_id=tenant_id, status="COMPLETED", created_at__date__gte=period_start
        )
        total_paid = paid_sales.aggregate(Sum('paid_amount'))['paid_amount__sum'] or Decimal('0')
        total_approved = paid_sales.count()

        return {
            'amount': total_paid,
            'totalApprovedSales': total_approved,
            'period': 'Last 30 days',
        }

    def _get_selected_for_invoice(self, tenant_id):
        """Get sales selected for invoicing (pending approval by accountant)"""
        sales = Sale.objects.filter(
            tenant_id=tenant_id, status="PENDING"
        ).order_by('-created_at')[:10]

        total_amount = sales.aggregate(Sum('total_amount'))['total_amount__sum'] or Decimal('0')

        items = []
        for sale in sales:
            items.append({
                'id': sale.id,
                'invoiceNumber': sale.invoice_number,
                'customerName': sale.customer_name,
                'totalAmount': sale.total_amount,
                'createdAt': sale.created_at,
            })

        return {
            'count': len(items),
            'totalAmount': total_amount,
            'items': items,
        }

    def _get_partial_payment_requests(self, tenant_id):
        """Get partial payment requests"""
        # Get all partial payment sales
        partial_sales = Sale.objects.filter(
            tenant_id=tenant_id, payment_option="PARTIAL", due_amount__gt=0
        )

        # Categorize by status
        pending_count = partial_sales.filter(status="PENDING").count()
        total_count = partial_sales.count()

        items = []
        for sale in partial_sales[:10]:
            items.append({
                'id': sale.id,
                'invoiceNumber': sale.invoice_number,
                'customerName': sale.customer_name,
                'requestedAmount': sale.due_amount,
                'status': sale.status,
                'createdAt': sale.created_at,
            })

        return {
            'count': total_count,
            'pending': pending_count,
            'items': items,
        }

    def _get_quick_stats(self, tenant_id):
        """Get quick statistics"""
        today = date.today()

        # Today's sales
        todays_sales = Sale.objects.filter(
            tenant_id=tenant_id, created_at__date=today
        ).count()

        # Pending invoices
        pending_invoices = Sale.objects.filter(
            tenant_id=tenant_id, status="PENDING"
        ).count()

        # Pending partial payments
        pending_partial = Sale.objects.filter(
            tenant_id=tenant_id, payment_option="PARTIAL", due_amount__gt=0, status__in=["PENDING", "APPROVED"]
        ).count()

        # Overdue payments
        thirty_days_ago = datetime.now() - timedelta(days=30)
        overdue_payments = Sale.objects.filter(
            tenant_id=tenant_id, status="APPROVED", due_amount__gt=0, approved_at__lt=thirty_days_ago
        ).count()

        # Total due
        total_due = Sale.objects.filter(
            tenant_id=tenant_id, status__in=["PENDING", "APPROVED"], due_amount__gt=0
        ).aggregate(Sum('due_amount'))['due_amount__sum'] or Decimal('0')

        # Total paid
        total_paid = Sale.objects.filter(
            tenant_id=tenant_id, status="COMPLETED"
        ).aggregate(Sum('paid_amount'))['paid_amount__sum'] or Decimal('0')

        return {
            'todaysSales': todays_sales,
            'pendingInvoices': pending_invoices,
            'pendingPartialPayments': pending_partial,
            'overduePayments': overdue_payments,
            'totalDue': total_due,
            'totalPaid': total_paid,
        }


class AccountantPendingPartialPaymentsView(APIView):
    """Get pending partial payments only"""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Get pending partial payments with total due",
        parameters=[{"name": "tenantId", "in": "query", "required": True, "schema": {"type": "string"}}],
        responses={200: PendingPartialPaymentsSerializer()},
    )
    def get(self, request):
        tenant_id = request.query_params.get("tenantId")

        if not tenant_id:
            return Response({"error": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            UserTenant.objects.get(user=request.user, tenant_id=tenant_id)
        except UserTenant.DoesNotExist:
            return Response({"error": "Not authorized for this tenant"}, status=status.HTTP_403_FORBIDDEN)

        dashboard_view = AccountantDashboardPaymentsView()
        data = dashboard_view._get_pending_partial_payments(tenant_id)

        serializer = PendingPartialPaymentsSerializer(data)
        return Response(serializer.data)


class AccountantOverduePaymentsView(APIView):
    """Get overdue payments only"""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Get overdue payments (>30 days past approval)",
        parameters=[{"name": "tenantId", "in": "query", "required": True, "schema": {"type": "string"}}],
        responses={200: OverduePaymentsSerializer()},
    )
    def get(self, request):
        tenant_id = request.query_params.get("tenantId")

        if not tenant_id:
            return Response({"error": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            UserTenant.objects.get(user=request.user, tenant_id=tenant_id)
        except UserTenant.DoesNotExist:
            return Response({"error": "Not authorized for this tenant"}, status=status.HTTP_403_FORBIDDEN)

        dashboard_view = AccountantDashboardPaymentsView()
        data = dashboard_view._get_overdue_payments(tenant_id)

        serializer = OverduePaymentsSerializer(data)
        return Response(serializer.data)


class AccountantTotalPaidAmountView(APIView):
    """Get total paid amount statistics"""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Get total paid amount from approved sales",
        parameters=[{"name": "tenantId", "in": "query", "required": True, "schema": {"type": "string"}}],
        responses={200: TotalPaidAmountSerializer()},
    )
    def get(self, request):
        tenant_id = request.query_params.get("tenantId")

        if not tenant_id:
            return Response({"error": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            UserTenant.objects.get(user=request.user, tenant_id=tenant_id)
        except UserTenant.DoesNotExist:
            return Response({"error": "Not authorized for this tenant"}, status=status.HTTP_403_FORBIDDEN)

        dashboard_view = AccountantDashboardPaymentsView()
        data = dashboard_view._get_total_paid_amount(tenant_id)

        serializer = TotalPaidAmountSerializer(data)
        return Response(serializer.data)


class AccountantSelectedForInvoiceView(APIView):
    """Get sales selected for invoicing"""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Get sales pending invoicing approval",
        parameters=[{"name": "tenantId", "in": "query", "required": True, "schema": {"type": "string"}}],
        responses={200: SelectedForInvoiceListSerializer()},
    )
    def get(self, request):
        tenant_id = request.query_params.get("tenantId")

        if not tenant_id:
            return Response({"error": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            UserTenant.objects.get(user=request.user, tenant_id=tenant_id)
        except UserTenant.DoesNotExist:
            return Response({"error": "Not authorized for this tenant"}, status=status.HTTP_403_FORBIDDEN)

        dashboard_view = AccountantDashboardPaymentsView()
        data = dashboard_view._get_selected_for_invoice(tenant_id)

        serializer = SelectedForInvoiceListSerializer(data)
        return Response(serializer.data)


class AccountantPartialPaymentRequestsView(APIView):
    """Get partial payment requests"""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Get partial payment requests with pending count",
        parameters=[{"name": "tenantId", "in": "query", "required": True, "schema": {"type": "string"}}],
        responses={200: PartialPaymentRequestsSerializer()},
    )
    def get(self, request):
        tenant_id = request.query_params.get("tenantId")

        if not tenant_id:
            return Response({"error": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            UserTenant.objects.get(user=request.user, tenant_id=tenant_id)
        except UserTenant.DoesNotExist:
            return Response({"error": "Not authorized for this tenant"}, status=status.HTTP_403_FORBIDDEN)

        dashboard_view = AccountantDashboardPaymentsView()
        data = dashboard_view._get_partial_payment_requests(tenant_id)

        serializer = PartialPaymentRequestsSerializer(data)
        return Response(serializer.data)


class AccountantQuickStatsView(APIView):
    """Get quick statistics dashboard"""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Get quick statistics: today's sales, pending invoices, overdue payments",
        parameters=[{"name": "tenantId", "in": "query", "required": True, "schema": {"type": "string"}}],
        responses={200: QuickStatsSerializer()},
    )
    def get(self, request):
        tenant_id = request.query_params.get("tenantId")

        if not tenant_id:
            return Response({"error": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            UserTenant.objects.get(user=request.user, tenant_id=tenant_id)
        except UserTenant.DoesNotExist:
            return Response({"error": "Not authorized for this tenant"}, status=status.HTTP_403_FORBIDDEN)

        dashboard_view = AccountantDashboardPaymentsView()
        data = dashboard_view._get_quick_stats(tenant_id)

        serializer = QuickStatsSerializer(data)
        return Response(serializer.data)
