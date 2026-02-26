# Pharmacist Dashboard Views
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework import status
from datetime import datetime, date, timedelta
from decimal import Decimal
from django.db.models import Q, Sum
from drf_spectacular.utils import extend_schema
from api.models import Sale, SaleItem, UserTenant, Medicine, StockBatch
from api.serializers import (
    PharmacistDashboardSummarySerializer,
    PharmacistDashboardPendingListSerializer,
    PharmacistDashboardRecentApprovalsSerializer,
)


class PharmacistDashboardSummaryView(APIView):
    """Get pharmacist dashboard summary KPIs"""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Get pharmacist dashboard summary with KPIs and quick stats",
        parameters=[
            {"name": "tenantId", "in": "query", "required": True, "schema": {"type": "string"}},
        ],
        responses={200: PharmacistDashboardSummarySerializer()},
    )
    def get(self, request):
        tenant_id = request.query_params.get("tenantId")

        if not tenant_id:
            return Response({"error": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            UserTenant.objects.get(user=request.user, tenant_id=tenant_id)
        except UserTenant.DoesNotExist:
            return Response({"error": "Not authorized for this tenant"}, status=status.HTTP_403_FORBIDDEN)

        # Pending Review - Invoices awaiting pharmacist approval (status APPROVED, pharmacist_approval_status PENDING)
        pending_review = Sale.objects.filter(
            tenant_id=tenant_id,
            status="APPROVED",
            pharmacist_approval_status__in=["PENDING", None]
        ).count()

        # Approved Today - Invoices approved by pharmacist today
        today = date.today()
        approved_today = Sale.objects.filter(
            tenant_id=tenant_id,
            pharmacist_approval_status="APPROVED",
            status="COMPLETED",
            pharmacist_approved_at__date=today
        ).count()

        # Partial payments currently pending pharmacist action
        partial_payments = Sale.objects.filter(
            tenant_id=tenant_id,
            payment_option="PARTIAL",
            owner_approval_status="APPROVED",
            pharmacist_approval_status__in=["PENDING", None],
            due_amount__gt=0,
            status="APPROVED",
        ).count()

        # Total Processed - Sum of approved payment amounts (completed invoices)
        total_processed = Sale.objects.filter(
            tenant_id=tenant_id,
            pharmacist_approval_status="APPROVED",
            status="COMPLETED"
        ).aggregate(Sum("paid_amount"))["paid_amount__sum"] or Decimal("0")

        # Quick Stats
        # Low Stock - medicines with total remaining stock across batches < 10.
        low_stock = Medicine.objects.filter(
            tenant_id=tenant_id,
        ).annotate(
            total_stock=Sum("batches__quantity")
        ).filter(
            Q(total_stock__lt=10) | Q(total_stock__isnull=True)
        ).count()

        # Expiring Soon - StockBatches expiring within 30 days
        expiry_date = date.today() + timedelta(days=30)
        expiring_soon = StockBatch.objects.filter(
            medicine__tenant_id=tenant_id,
            expiry_date__lte=expiry_date,
            expiry_date__gte=date.today()
        ).count()

        # Pending accountant stage - partial invoices approved by pharmacist.
        pending_approvals = Sale.objects.filter(
            tenant_id=tenant_id,
            payment_option="PARTIAL",
            pharmacist_approval_status="APPROVED",
            owner_approval_status="APPROVED",
            due_amount__gt=0,
            status="APPROVED",
        ).count()

        return Response({
            "pendingReview": pending_review,
            "approvedToday": approved_today,
            "partialPayments": partial_payments,
            "totalProcessed": str(total_processed),
            "lowStock": low_stock,
            "expiringSoon": expiring_soon,
            "pendingApprovals": pending_approvals,
        }, status=status.HTTP_200_OK)


class PharmacistPendingInvoicesView(APIView):
    """Get pending invoices awaiting pharmacist approval"""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Get pending invoices awaiting pharmacist approval",
        parameters=[
            {"name": "tenantId", "in": "query", "required": True, "schema": {"type": "string"}},
            {"name": "limit", "in": "query", "schema": {"type": "integer", "default": 5}},
        ],
        responses={200: PharmacistDashboardPendingListSerializer()},
    )
    def get(self, request):
        tenant_id = request.query_params.get("tenantId")
        limit = int(request.query_params.get("limit", 5))

        if not tenant_id:
            return Response({"error": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            UserTenant.objects.get(user=request.user, tenant_id=tenant_id)
        except UserTenant.DoesNotExist:
            return Response({"error": "Not authorized for this tenant"}, status=status.HTTP_403_FORBIDDEN)

        # Get pending invoices
        pending_invoices = Sale.objects.filter(
            tenant_id=tenant_id,
            status="APPROVED",
            pharmacist_approval_status__in=["PENDING", None]
        ).order_by("-created_at")[:limit]

        results = []
        for invoice in pending_invoices:
            results.append({
                "invoiceId": str(invoice.id),
                "invoiceNumber": invoice.invoice_number,
                "customerName": invoice.customer_name or "",
                "totalAmount": str(invoice.total_amount),
                "invoiceDate": invoice.created_at,
                "status": invoice.status,
            })

        return Response({
            "count": len(results),
            "results": results,
        }, status=status.HTTP_200_OK)


class PharmacistRecentApprovalsView(APIView):
    """Get recently approved invoices"""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Get recently approved invoices",
        parameters=[
            {"name": "tenantId", "in": "query", "required": True, "schema": {"type": "string"}},
            {"name": "limit", "in": "query", "schema": {"type": "integer", "default": 5}},
        ],
        responses={200: PharmacistDashboardRecentApprovalsSerializer()},
    )
    def get(self, request):
        tenant_id = request.query_params.get("tenantId")
        limit = int(request.query_params.get("limit", 5))

        if not tenant_id:
            return Response({"error": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            UserTenant.objects.get(user=request.user, tenant_id=tenant_id)
        except UserTenant.DoesNotExist:
            return Response({"error": "Not authorized for this tenant"}, status=status.HTTP_403_FORBIDDEN)

        # Get recently approved invoices
        recent_approvals = Sale.objects.filter(
            tenant_id=tenant_id,
            pharmacist_approval_status="APPROVED",
            status="COMPLETED"
        ).order_by("-pharmacist_approved_at")[:limit]

        results = []
        for invoice in recent_approvals:
            results.append({
                "invoiceId": str(invoice.id),
                "invoiceNumber": invoice.invoice_number,
                "customerName": invoice.customer_name or "",
                "totalAmount": str(invoice.total_amount),
                "approvedAt": invoice.pharmacist_approved_at,
                "approvalStatus": invoice.pharmacist_approval_status,
            })

        return Response({
            "count": len(results),
            "results": results,
        }, status=status.HTTP_200_OK)
