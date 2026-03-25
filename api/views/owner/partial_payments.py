# Owner Partial Payment Approval Views
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework import status
from datetime import datetime
from decimal import Decimal
from django.db.models import Q, Sum, Count
from django.contrib.auth import get_user_model
from drf_spectacular.utils import extend_schema
from api.models import Notification, Sale, Tenant, UserTenant
from api.serializers import (
    OwnerPartialPaymentListSerializer,
    OwnerPartialPaymentSummarySerializer,
    PartialPaymentApprovalRequestSerializer,
    PartialPaymentRejectRequestSerializer,
)
from api.utils.subscription_access import authorize_tenant_access

User = get_user_model()


class OwnerPartialPaymentsBaseView(APIView):
    permission_classes = [IsAuthenticated]
    required_subscription_feature = "sales_management"

    def _authorize(self, request):
        tenant_id = request.query_params.get("tenantId")
        tenant, error_message, error_status = authorize_tenant_access(
            request,
            tenant_id,
            required_role="OWNER",
            required_feature=self.required_subscription_feature,
        )
        if error_message:
            return None, tenant_id, Response({"error": error_message}, status=error_status)
        return tenant, tenant_id, None


class OwnerPartialPaymentsListView(OwnerPartialPaymentsBaseView):
    """Get list of partial payment invoices pending owner approval"""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Stage 1 (after storekeeper): list PARTIAL invoices awaiting OWNER approval before PHARMACIST and ACCOUNTANT.",
        parameters=[
            {"name": "tenantId", "in": "query", "required": True, "schema": {"type": "string"}},
            {"name": "page", "in": "query", "schema": {"type": "integer", "default": 1}},
            {"name": "pageSize", "in": "query", "schema": {"type": "integer", "default": 10}},
        ],
        responses={200: OwnerPartialPaymentListSerializer()},
    )
    def get(self, request):
        _, tenant_id, auth_error = self._authorize(request)
        if auth_error:
            return auth_error

        page = int(request.query_params.get("page", 1))
        page_size = int(request.query_params.get("pageSize", 10))

        # Query: Partial payment invoices pending owner approval.
        partial_invoices = Sale.objects.filter(
            tenant_id=tenant_id,
            status="APPROVED",
            payment_option="PARTIAL",
            due_amount__gt=0,
            pharmacist_approval_status__in=["PENDING", None],
            owner_approval_status__in=["PENDING", None]
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
            "next": f"/api/owner/partial-payments/?tenantId={tenant_id}&page={page + 1}&pageSize={page_size}" if end_idx < total_count else None,
            "previous": f"/api/owner/partial-payments/?tenantId={tenant_id}&page={page - 1}&pageSize={page_size}" if page > 1 else None,
            "currency": (tenant.currency if tenant else "USD"),
            "results": results,
        }

        return Response(response_data, status=status.HTTP_200_OK)


class OwnerPartialPaymentSummaryView(OwnerPartialPaymentsBaseView):
    """Get summary of partial payment metrics for owner"""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Owner-stage summary for PARTIAL invoices in the chain: Storekeeper -> Owner -> Pharmacist -> Accountant.",
        parameters=[
            {"name": "tenantId", "in": "query", "required": True, "schema": {"type": "string"}},
        ],
        responses={200: OwnerPartialPaymentSummarySerializer()},
    )
    def get(self, request):
        _, tenant_id, auth_error = self._authorize(request)
        if auth_error:
            return auth_error

        # Pending approvals (status APPROVED, PARTIAL, owner approval pending)
        pending_invoices = Sale.objects.filter(
            tenant_id=tenant_id,
            status="APPROVED",
            payment_option="PARTIAL",
            due_amount__gt=0,
            pharmacist_approval_status__in=["PENDING", None],
            owner_approval_status__in=["PENDING", None]
        )
        pending_count = pending_invoices.count()
        total_pending_due = pending_invoices.aggregate(Sum("due_amount"))["due_amount__sum"] or Decimal("0")

        # Owner-approved invoices, waiting for pharmacist.
        approved_by_owner = Sale.objects.filter(
            tenant_id=tenant_id,
            status="APPROVED",
            payment_option="PARTIAL",
            owner_approval_status="APPROVED",
            pharmacist_approval_status__in=["PENDING", None]
        )
        approved_count = approved_by_owner.count()
        total_approved = approved_by_owner.aggregate(Sum("total_amount"))["total_amount__sum"] or Decimal("0")

        tenant = Tenant.objects.only("id", "currency").filter(id=tenant_id).first()
        return Response({
            "pendingApprovals": pending_count,
            "totalPendingDue": str(total_pending_due),
            "approvedByPharmacist": approved_count,
            "totalApprovedAmount": str(total_approved),
            "currency": (tenant.currency if tenant else "USD"),
        }, status=status.HTTP_200_OK)


class OwnerApprovePartialPaymentView(OwnerPartialPaymentsBaseView):
    """Owner approves partial payment (first approval after storekeeper)"""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Stage 1 approval: OWNER approves PARTIAL invoice and routes it to PHARMACIST.",
        parameters=[
            {"name": "tenantId", "in": "query", "required": True, "schema": {"type": "string"}},
            {"name": "invoice_id", "in": "path", "required": True, "schema": {"type": "string"}},
        ],
        request=PartialPaymentApprovalRequestSerializer(),
        responses={200: OwnerPartialPaymentListSerializer()},
    )
    def post(self, request, invoice_id):
        _, tenant_id, auth_error = self._authorize(request)
        if auth_error:
            return auth_error

        try:
            invoice = Sale.objects.get(id=invoice_id, tenant_id=tenant_id)
        except Sale.DoesNotExist:
            return Response({"error": "Invoice not found"}, status=status.HTTP_404_NOT_FOUND)

        # Owner is the first approver after storekeeper for partial invoices.
        if (
            invoice.payment_option != "PARTIAL"
            or invoice.status != "APPROVED"
            or invoice.owner_approval_status not in ["PENDING", None]
        ):
            return Response({"error": "Invoice is not eligible for owner approval"}, status=status.HTTP_400_BAD_REQUEST)

        # Mark as owner approved
        invoice.owner_approval_status = "APPROVED"
        invoice.owner_approved_at = datetime.now()
        invoice.owner_approved_by = request.user
        invoice.save()

        # Notify pharmacists (next approver in chain).
        notes = request.data.get("notes", "")
        pharmacists = UserTenant.objects.filter(
            tenant_id=tenant_id,
            role="PHARMACIST"
        )
        for pharmacist in pharmacists:
            Notification.objects.create(
                tenant_id=tenant_id,
                recipient_id=pharmacist.user_id,
                title="Partial Invoice Approved by Owner",
                message=f"Owner approved partial invoice {invoice.invoice_number}. Pharmacist approval is required next.",
            )

        return Response({
            "success": True,
            "message": "Partial payment approved by owner. Notification sent to pharmacist.",
            "invoiceId": str(invoice.id),
        }, status=status.HTTP_200_OK)


class OwnerRejectPartialPaymentView(OwnerPartialPaymentsBaseView):
    """Owner rejects partial payment invoice"""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Stage 1 rejection: OWNER rejects PARTIAL invoice before pharmacist/accountant stages.",
        parameters=[
            {"name": "tenantId", "in": "query", "required": True, "schema": {"type": "string"}},
            {"name": "invoice_id", "in": "path", "required": True, "schema": {"type": "string"}},
        ],
        request=PartialPaymentRejectRequestSerializer(),
        responses={200: OwnerPartialPaymentListSerializer()},
    )
    def post(self, request, invoice_id):
        _, tenant_id, auth_error = self._authorize(request)
        if auth_error:
            return auth_error

        try:
            invoice = Sale.objects.get(id=invoice_id, tenant_id=tenant_id)
        except Sale.DoesNotExist:
            return Response({"error": "Invoice not found"}, status=status.HTTP_404_NOT_FOUND)

        # Owner can reject while invoice is waiting for owner approval.
        if invoice.payment_option != "PARTIAL" or invoice.owner_approval_status not in ["PENDING", None]:
            return Response({"error": "Invoice is not eligible for owner rejection"}, status=status.HTTP_400_BAD_REQUEST)

        # Mark as owner rejected
        invoice.owner_approval_status = "REJECTED"
        invoice.save()

        # Notify cashier and accountant about owner rejection.
        rejection_reason = request.data.get("rejectionReason", "")
        user_tenants = UserTenant.objects.filter(
            tenant_id=tenant_id,
            role__in=["ACCOUNTANT", "CASHIER"]
        )
        for user_tenant in user_tenants:
            Notification.objects.create(
                tenant_id=tenant_id,
                recipient_id=user_tenant.user_id,
                title="Partial Payment Rejected by Owner",
                message=f"Owner rejected partial payment for invoice {invoice.invoice_number}. Reason: {rejection_reason}",
            )

        return Response({
            "success": True,
            "message": "Partial payment rejected by owner. Notifications sent to accountant and cashier.",
            "invoiceId": str(invoice.id),
        }, status=status.HTTP_200_OK)
