# Pharmacist Partial Payment Approval Views
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework import status
from datetime import datetime, timedelta
from decimal import Decimal
from django.db.models import Q, Sum, Count, F
from drf_spectacular.utils import extend_schema
from api.models import Medicine, Notification, Sale, SaleItem, StockBatch, Tenant, UserTenant
from api.utils.subscription_access import authorize_tenant_access
from api.serializers import (
    PharmacistPartialPaymentListSerializer,
    PharmacistPartialPaymentSummarySerializer,
    PartialPaymentApprovalRequestSerializer,
    PartialPaymentRejectRequestSerializer,
)


class PharmacistPartialPaymentsBaseView(APIView):
    permission_classes = [IsAuthenticated]
    required_subscription_feature = "sales_management"

    def _authorize(self, request):
        tenant_id = request.query_params.get("tenantId")
        tenant, error_message, error_status = authorize_tenant_access(
            request,
            tenant_id,
            required_feature=self.required_subscription_feature,
        )
        if error_message:
            return None, tenant_id, Response({"error": error_message}, status=error_status)
        return tenant, tenant_id, None


class PharmacistPartialPaymentsListView(PharmacistPartialPaymentsBaseView):
    """Get list of partial payment invoices pending pharmacist approval"""

    @extend_schema(
        description="Stage 2: list PARTIAL invoices approved by OWNER and awaiting PHARMACIST approval.",
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

        _, tenant_id, auth_error = self._authorize(request)
        if auth_error:
            return auth_error

        # Query: partial invoices approved by owner and pending pharmacist review.
        partial_invoices = Sale.objects.filter(
            tenant_id=tenant_id,
            status="APPROVED",
            payment_option="PARTIAL",
            due_amount__gt=0,
            owner_approval_status="APPROVED",
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
            results.append({
                "invoiceId": str(invoice.id),
                "invoiceNumber": invoice.invoice_number,
                "customerName": invoice.customer_name or "",
                "customerPhone": invoice.customer_phone or "",
                "totalAmount": str(invoice.total_amount),
                "paidAmount": str(invoice.paid_amount),
                "dueAmount": str(invoice.due_amount),
                "currency": invoice.currency,
                "paymentMethod": invoice.payment_method or "",
                "paymentOption": invoice.payment_option,
                "invoiceDate": invoice.created_at,
                "dueDate": invoice.due_date,
                "createdAt": invoice.created_at,
            })

        tenant = Tenant.objects.only("id", "currency").filter(id=tenant_id).first()
        response_data = {
            "count": total_count,
            "next": f"/api/pharmacist/partial-payments/?tenantId={tenant_id}&page={page + 1}&pageSize={page_size}" if end_idx < total_count else None,
            "previous": f"/api/pharmacist/partial-payments/?tenantId={tenant_id}&page={page - 1}&pageSize={page_size}" if page > 1 else None,
            "currency": (tenant.currency if tenant else "USD"),
            "results": results,
        }

        return Response(response_data, status=status.HTTP_200_OK)


class PharmacistPartialPaymentSummaryView(PharmacistPartialPaymentsBaseView):
    """Get summary of partial payment metrics"""

    @extend_schema(
        description="Pharmacist-stage summary for chain: Storekeeper -> Owner -> Pharmacist -> Accountant.",
        parameters=[
            {"name": "tenantId", "in": "query", "required": True, "schema": {"type": "string"}},
        ],
        responses={200: PharmacistPartialPaymentSummarySerializer()},
    )
    def get(self, request):
        tenant_id = request.query_params.get("tenantId")

        _, tenant_id, auth_error = self._authorize(request)
        if auth_error:
            return auth_error

        # Partial payments pending pharmacist after owner approval.
        partial_invoices = Sale.objects.filter(
            tenant_id=tenant_id,
            status="APPROVED",
            payment_option="PARTIAL",
            due_amount__gt=0,
            owner_approval_status="APPROVED",
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

        # Total paid (invoices that passed pharmacist stage)
        approved_invoices = Sale.objects.filter(
            tenant_id=tenant_id,
            status__in=["APPROVED", "COMPLETED"],
            owner_approval_status="APPROVED",
            pharmacist_approval_status="APPROVED"
        )
        total_paid = approved_invoices.aggregate(Sum("paid_amount"))["paid_amount__sum"] or Decimal("0")

        # Selected for processing (owner + pharmacist approved, waiting for accountant delivery release)
        selected_invoices = Sale.objects.filter(
            tenant_id=tenant_id,
            status="APPROVED",
            payment_option="PARTIAL",
            owner_approval_status="APPROVED",
            pharmacist_approval_status="APPROVED",
            due_amount__gt=0
        )
        selected_count = selected_invoices.count()
        selected_total = selected_invoices.aggregate(Sum("due_amount"))["due_amount__sum"] or Decimal("0")

        tenant = Tenant.objects.only("id", "currency").filter(id=tenant_id).first()
        return Response({
            "partialPayments": partial_count,
            "totalPartialDue": str(total_partial_due),
            "overduePayments": overdue_count,
            "totalPaidAmount": str(total_paid),
            "selectedForProcessing": selected_count,
            "selectedForProcessingTotal": str(selected_total),
            "currency": (tenant.currency if tenant else "USD"),
        }, status=status.HTTP_200_OK)


class PharmacistApprovePartialPaymentView(PharmacistPartialPaymentsBaseView):
    """Pharmacist approves partial payment (routes to accountant)"""

    @extend_schema(
        description="Stage 2 approval: PHARMACIST approves PARTIAL invoice and routes it to ACCOUNTANT.",
        parameters=[
            {"name": "tenantId", "in": "query", "required": True, "schema": {"type": "string"}},
            {"name": "invoice_id", "in": "path", "required": True, "schema": {"type": "string"}},
        ],
        request=PartialPaymentApprovalRequestSerializer(),
        responses={200: PharmacistPartialPaymentListSerializer()},
    )
    def post(self, request, invoice_id):
        tenant_id = request.query_params.get("tenantId")

        _, tenant_id, auth_error = self._authorize(request)
        if auth_error:
            return auth_error

        try:
            invoice = Sale.objects.get(id=invoice_id, tenant_id=tenant_id)
        except Sale.DoesNotExist:
            return Response({"error": "Invoice not found"}, status=status.HTTP_404_NOT_FOUND)

        # Pharmacist approves after owner approval.
        if (
            invoice.status != "APPROVED"
            or invoice.payment_option != "PARTIAL"
            or invoice.owner_approval_status != "APPROVED"
        ):
            return Response({"error": "Invoice is not eligible for pharmacist approval"}, status=status.HTTP_400_BAD_REQUEST)

        # Mark as pharmacist approved
        invoice.pharmacist_approval_status = "APPROVED"
        invoice.pharmacist_approved_at = datetime.now()
        invoice.pharmacist_approved_by = request.user
        invoice.save()

        # Notify accountants (next approver in chain).
        notes = request.data.get("notes", "")
        accountants = UserTenant.objects.filter(tenant_id=tenant_id, role="ACCOUNTANT")
        for accountant in accountants:
            Notification.objects.create(
                tenant_id=tenant_id,
                recipient_id=accountant.user_id,
                title="Partial Invoice Approved by Pharmacist",
                message=f"Pharmacist approved partial invoice {invoice.invoice_number}. Accountant approval is required next.",
            )

        return Response({
            "success": True,
            "message": "Partial payment approved. Accountant notification sent.",
            "invoiceId": str(invoice.id),
        }, status=status.HTTP_200_OK)


class PharmacistRejectPartialPaymentView(PharmacistPartialPaymentsBaseView):
    """Pharmacist rejects partial payment invoice"""

    @extend_schema(
        description="Stage 2 rejection: PHARMACIST rejects PARTIAL invoice and notifies OWNER.",
        parameters=[
            {"name": "tenantId", "in": "query", "required": True, "schema": {"type": "string"}},
            {"name": "invoice_id", "in": "path", "required": True, "schema": {"type": "string"}},
        ],
        request=PartialPaymentRejectRequestSerializer(),
        responses={200: PharmacistPartialPaymentListSerializer()},
    )
    def post(self, request, invoice_id):
        tenant_id = request.query_params.get("tenantId")

        _, tenant_id, auth_error = self._authorize(request)
        if auth_error:
            return auth_error

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

        # Notify owner on pharmacist rejection.
        rejection_reason = request.data.get("rejectionReason", "")
        owner_relations = UserTenant.objects.filter(tenant_id=tenant_id, role="OWNER")
        for owner in owner_relations:
            Notification.objects.create(
                tenant_id=tenant_id,
                recipient_id=owner.user_id,
                title="Partial Payment Rejected by Pharmacist",
                message=f"Pharmacist rejected partial payment for invoice {invoice.invoice_number}. Reason: {rejection_reason}",
            )

        return Response({
            "success": True,
            "message": "Partial payment rejected. Owner notification sent.",
            "invoiceId": str(invoice.id),
        }, status=status.HTTP_200_OK)
