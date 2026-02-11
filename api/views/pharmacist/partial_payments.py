# Pharmacist Partial Payment Approval Views
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework import status
from datetime import datetime, timedelta
from decimal import Decimal
from django.db.models import Q, Sum, Count, F
from drf_spectacular.utils import extend_schema
from api.models import Sale, SaleItem, UserTenant, Notification, Medicine, StockBatch
from api.serializers import (
    PharmacistPartialPaymentListSerializer,
    PharmacistPartialPaymentSummarySerializer,
    PartialPaymentApprovalRequestSerializer,
    PartialPaymentRejectRequestSerializer,
)


class PharmacistPartialPaymentsListView(APIView):
    """Get list of partial payment invoices pending pharmacist approval"""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Get partial payment invoices pending pharmacist approval",
        parameters=[
            {"name": "tenantId", "in": "query", "required": True, "schema": {"type": "string"}},
            {"name": "page", "in": "query", "schema": {"type": "integer", "default": 1}},
            {"name": "pageSize", "in": "query", "schema": {"type": "integer", "default": 10}},
        ],
        responses={200: PharmacistPartialPaymentListSerializer()},
    )
    def get(self, request):
        tenant_id = request.query_params.get("tenantId")
        page = int(request.query_params.get("page", 1))
        page_size = int(request.query_params.get("pageSize", 10))

        if not tenant_id:
            return Response({"error": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            UserTenant.objects.get(user=request.user, tenant_id=tenant_id)
        except UserTenant.DoesNotExist:
            return Response({"error": "Not authorized for this tenant"}, status=status.HTTP_403_FORBIDDEN)

        # Query: Partial payment invoices (status APPROVED, payment_option PARTIAL, due_amount > 0)
        # These are invoices that accountant approved but are pending pharmacist review
        partial_invoices = Sale.objects.filter(
            tenant_id=tenant_id,
            status="APPROVED",
            payment_option="PARTIAL",
            due_amount__gt=0,
            pharmacist_approval_status__in=["PENDING", None]  # Not yet approved by pharmacist
        ).order_by("-created_at")

        # Pagination
        total_count = partial_invoices.count()
        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size
        paginated_invoices = partial_invoices[start_idx:end_idx]

        # Serialize
        results = []
        for invoice in paginated_invoices:
            customer = invoice.customer
            results.append({
                "invoiceId": str(invoice.id),
                "invoiceNumber": invoice.invoice_number,
                "customerName": customer.name if customer else "",
                "customerPhone": customer.phone if customer else "",
                "totalAmount": str(invoice.total_amount),
                "paidAmount": str(invoice.paid_amount),
                "dueAmount": str(invoice.due_amount),
                "paymentMethod": invoice.payment_method or "",
                "paymentOption": invoice.payment_option,
                "invoiceDate": invoice.created_at,
                "dueDate": invoice.due_date,
                "createdAt": invoice.created_at,
            })

        response_data = {
            "count": total_count,
            "next": f"/api/pharmacist/partial-payments/?tenantId={tenant_id}&page={page + 1}&pageSize={page_size}" if end_idx < total_count else None,
            "previous": f"/api/pharmacist/partial-payments/?tenantId={tenant_id}&page={page - 1}&pageSize={page_size}" if page > 1 else None,
            "results": results,
        }

        return Response(response_data, status=status.HTTP_200_OK)


class PharmacistPartialPaymentSummaryView(APIView):
    """Get summary of partial payment metrics"""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Get partial payment summary statistics",
        parameters=[
            {"name": "tenantId", "in": "query", "required": True, "schema": {"type": "string"}},
        ],
        responses={200: PharmacistPartialPaymentSummarySerializer()},
    )
    def get(self, request):
        tenant_id = request.query_params.get("tenantId")

        if not tenant_id:
            return Response({"error": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            UserTenant.objects.get(user=request.user, tenant_id=tenant_id)
        except UserTenant.DoesNotExist:
            return Response({"error": "Not authorized for this tenant"}, status=status.HTTP_403_FORBIDDEN)

        # Partial payments (APPROVED, PARTIAL, due > 0)
        partial_invoices = Sale.objects.filter(
            tenant_id=tenant_id,
            status="APPROVED",
            payment_option="PARTIAL",
            due_amount__gt=0,
            pharmacist_approval_status__in=["PENDING", None]
        )
        partial_count = partial_invoices.count()
        total_partial_due = partial_invoices.aggregate(Sum("due_amount"))["due_amount__sum"] or Decimal("0")

        # Overdue payments (due_date < today)
        today = datetime.now().date()
        overdue_invoices = Sale.objects.filter(
            tenant_id=tenant_id,
            status__in=["APPROVED", "COMPLETED"],
            due_date__lt=today,
            due_amount__gt=0
        )
        overdue_count = overdue_invoices.count()

        # Total paid (approved invoices)
        approved_invoices = Sale.objects.filter(
            tenant_id=tenant_id,
            status__in=["APPROVED", "COMPLETED"],
            pharmacist_approval_status="APPROVED"
        )
        total_paid = approved_invoices.aggregate(Sum("paid_amount"))["paid_amount__sum"] or Decimal("0")

        # Selected for processing (pharmacist_approval_status = APPROVED, waiting for owner)
        selected_invoices = Sale.objects.filter(
            tenant_id=tenant_id,
            status="APPROVED",
            payment_option="PARTIAL",
            pharmacist_approval_status="APPROVED",
            owner_approval_status__in=["PENDING", None]
        )
        selected_count = selected_invoices.count()
        selected_total = selected_invoices.aggregate(Sum("due_amount"))["due_amount__sum"] or Decimal("0")

        return Response({
            "partialPayments": partial_count,
            "totalPartialDue": str(total_partial_due),
            "overduePayments": overdue_count,
            "totalPaidAmount": str(total_paid),
            "selectedForProcessing": selected_count,
            "selectedForProcessingTotal": str(selected_total),
        }, status=status.HTTP_200_OK)


class PharmacistApprovePartialPaymentView(APIView):
    """Pharmacist approves partial payment (selects for owner approval)"""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Pharmacist approves partial payment invoice",
        parameters=[
            {"name": "tenantId", "in": "query", "required": True, "schema": {"type": "string"}},
            {"name": "invoice_id", "in": "path", "required": True, "schema": {"type": "string"}},
        ],
        request=PartialPaymentApprovalRequestSerializer(),
        responses={200: PharmacistPartialPaymentListSerializer()},
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
            invoice = Sale.objects.get(id=invoice_id, tenant_id=tenant_id)
        except Sale.DoesNotExist:
            return Response({"error": "Invoice not found"}, status=status.HTTP_404_NOT_FOUND)

        # Only APPROVED invoices with PARTIAL payment can be approved by pharmacist
        if invoice.status != "APPROVED" or invoice.payment_option != "PARTIAL":
            return Response({"error": "Invoice is not eligible for pharmacist approval"}, status=status.HTTP_400_BAD_REQUEST)

        # Mark as pharmacist approved
        invoice.pharmacist_approval_status = "APPROVED"
        invoice.pharmacist_approved_at = datetime.now()
        invoice.pharmacist_approved_by = request.user
        invoice.save()

        # Create notification for owner
        notes = request.data.get("notes", "")
        Notification.objects.create(
            tenant_id=tenant_id,
            user_id=invoice.owner.id if hasattr(invoice, 'owner') else None,
            title="Partial Payment Approval",
            message=f"Pharmacist approved partial payment for invoice {invoice.invoice_number}. Awaiting your final approval.",
            notification_type="partial_payment_approval",
            related_invoice=invoice,
            created_by=request.user,
            notes=notes
        )

        return Response({
            "success": True,
            "message": "Partial payment approved. Owner notification sent.",
            "invoiceId": str(invoice.id),
        }, status=status.HTTP_200_OK)


class PharmacistRejectPartialPaymentView(APIView):
    """Pharmacist rejects partial payment invoice"""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Pharmacist rejects partial payment invoice",
        parameters=[
            {"name": "tenantId", "in": "query", "required": True, "schema": {"type": "string"}},
            {"name": "invoice_id", "in": "path", "required": True, "schema": {"type": "string"}},
        ],
        request=PartialPaymentRejectRequestSerializer(),
        responses={200: PharmacistPartialPaymentListSerializer()},
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
            invoice = Sale.objects.get(id=invoice_id, tenant_id=tenant_id)
        except Sale.DoesNotExist:
            return Response({"error": "Invoice not found"}, status=status.HTTP_404_NOT_FOUND)

        # Only APPROVED invoices with PARTIAL payment can be rejected
        if invoice.status != "APPROVED" or invoice.payment_option != "PARTIAL":
            return Response({"error": "Invoice is not eligible for pharmacist rejection"}, status=status.HTTP_400_BAD_REQUEST)

        # Mark as pharmacist rejected
        invoice.pharmacist_approval_status = "REJECTED"
        invoice.save()

        # Create notification for accountant
        rejection_reason = request.data.get("rejectionReason", "")
        Notification.objects.create(
            tenant_id=tenant_id,
            user_id=invoice.approved_by.id if hasattr(invoice, 'approved_by') else None,
            title="Partial Payment Rejected",
            message=f"Pharmacist rejected partial payment for invoice {invoice.invoice_number}. Reason: {rejection_reason}",
            notification_type="partial_payment_rejection",
            related_invoice=invoice,
            created_by=request.user,
            notes=rejection_reason
        )

        return Response({
            "success": True,
            "message": "Partial payment rejected. Accountant notification sent.",
            "invoiceId": str(invoice.id),
        }, status=status.HTTP_200_OK)
