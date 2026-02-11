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
from api.models import Sale, UserTenant, Notification
from api.serializers import (
    OwnerPartialPaymentListSerializer,
    OwnerPartialPaymentSummarySerializer,
    PartialPaymentApprovalRequestSerializer,
    PartialPaymentRejectRequestSerializer,
)

User = get_user_model()


class OwnerPartialPaymentsListView(APIView):
    """Get list of partial payment invoices pending owner approval"""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Get partial payment invoices pending owner approval (approved by pharmacist)",
        parameters=[
            {"name": "tenantId", "in": "query", "required": True, "schema": {"type": "string"}},
            {"name": "page", "in": "query", "schema": {"type": "integer", "default": 1}},
            {"name": "pageSize", "in": "query", "schema": {"type": "integer", "default": 10}},
        ],
        responses={200: OwnerPartialPaymentListSerializer()},
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

        # Query: Partial payment invoices approved by pharmacist, pending owner approval
        # status APPROVED, payment_option PARTIAL, pharmacist_approval_status APPROVED, owner_approval_status PENDING
        partial_invoices = Sale.objects.filter(
            tenant_id=tenant_id,
            status="APPROVED",
            payment_option="PARTIAL",
            due_amount__gt=0,
            pharmacist_approval_status="APPROVED",
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
            "next": f"/api/owner/partial-payments/?tenantId={tenant_id}&page={page + 1}&pageSize={page_size}" if end_idx < total_count else None,
            "previous": f"/api/owner/partial-payments/?tenantId={tenant_id}&page={page - 1}&pageSize={page_size}" if page > 1 else None,
            "results": results,
        }

        return Response(response_data, status=status.HTTP_200_OK)


class OwnerPartialPaymentSummaryView(APIView):
    """Get summary of partial payment metrics for owner"""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Get partial payment summary statistics for owner",
        parameters=[
            {"name": "tenantId", "in": "query", "required": True, "schema": {"type": "string"}},
        ],
        responses={200: OwnerPartialPaymentSummarySerializer()},
    )
    def get(self, request):
        tenant_id = request.query_params.get("tenantId")

        if not tenant_id:
            return Response({"error": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            UserTenant.objects.get(user=request.user, tenant_id=tenant_id)
        except UserTenant.DoesNotExist:
            return Response({"error": "Not authorized for this tenant"}, status=status.HTTP_403_FORBIDDEN)

        # Pending approvals (status APPROVED, PARTIAL, pharmacist_approval_status APPROVED, owner_approval_status PENDING)
        pending_invoices = Sale.objects.filter(
            tenant_id=tenant_id,
            status="APPROVED",
            payment_option="PARTIAL",
            due_amount__gt=0,
            pharmacist_approval_status="APPROVED",
            owner_approval_status__in=["PENDING", None]
        )
        pending_count = pending_invoices.count()
        total_pending_due = pending_invoices.aggregate(Sum("due_amount"))["due_amount__sum"] or Decimal("0")

        # Approved by pharmacist, total amount
        approved_by_pharmacist = Sale.objects.filter(
            tenant_id=tenant_id,
            payment_option="PARTIAL",
            pharmacist_approval_status="APPROVED"
        )
        approved_count = approved_by_pharmacist.count()
        total_approved = approved_by_pharmacist.aggregate(Sum("total_amount"))["total_amount__sum"] or Decimal("0")

        return Response({
            "pendingApprovals": pending_count,
            "totalPendingDue": str(total_pending_due),
            "approvedByPharmacist": approved_count,
            "totalApprovedAmount": str(total_approved),
        }, status=status.HTTP_200_OK)


class OwnerApprovePartialPaymentView(APIView):
    """Owner approves partial payment (final approval)"""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Owner approves partial payment invoice (final approval)",
        parameters=[
            {"name": "tenantId", "in": "query", "required": True, "schema": {"type": "string"}},
            {"name": "invoice_id", "in": "path", "required": True, "schema": {"type": "string"}},
        ],
        request=PartialPaymentApprovalRequestSerializer(),
        responses={200: OwnerPartialPaymentListSerializer()},
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

        # Only invoices with pharmacist_approval_status APPROVED can be approved by owner
        if invoice.pharmacist_approval_status != "APPROVED" or invoice.payment_option != "PARTIAL":
            return Response({"error": "Invoice is not eligible for owner approval"}, status=status.HTTP_400_BAD_REQUEST)

        # Mark as owner approved
        invoice.owner_approval_status = "APPROVED"
        invoice.owner_approved_at = datetime.now()
        invoice.owner_approved_by = request.user
        invoice.save()

        # Create notification for pharmacist
        notes = request.data.get("notes", "")
        if invoice.pharmacist_approved_by:
            Notification.objects.create(
                tenant_id=tenant_id,
                user_id=invoice.pharmacist_approved_by.id,
                title="Partial Payment Approved by Owner",
                message=f"Owner approved partial payment for invoice {invoice.invoice_number}. Payment processing complete.",
                notification_type="owner_partial_payment_approval",
                related_invoice=invoice,
                created_by=request.user,
                notes=notes
            )

        # Notify Accountant, Cashier, and Store Keeper
        # Get all users with these roles for the tenant
        user_tenants = UserTenant.objects.filter(
            tenant_id=tenant_id,
            role__in=["ACCOUNTANT", "CASHIER", "STORE_KEEPER"]
        )

        notification_messages = {
            "ACCOUNTANT": f"Partial payment approved for invoice {invoice.invoice_number}. Ready for final processing.",
            "CASHIER": f"Partial payment approved for invoice {invoice.invoice_number}. Ready to dispense products to customer.",
            "STORE_KEEPER": f"Partial payment approved for invoice {invoice.invoice_number}. Prepare products for customer delivery.",
        }

        for user_tenant in user_tenants:
            role = user_tenant.role
            Notification.objects.create(
                tenant_id=tenant_id,
                user_id=user_tenant.user_id,
                title=f"Partial Payment Approved - {role}",
                message=notification_messages.get(role, f"Partial payment approved for invoice {invoice.invoice_number}."),
                notification_type="owner_partial_payment_approval",
                related_invoice=invoice,
                created_by=request.user,
                notes=notes
            )

        return Response({
            "success": True,
            "message": "Partial payment approved by owner. Notifications sent to Pharmacist, Accountant, Cashier, and Store Keeper.",
            "invoiceId": str(invoice.id),
        }, status=status.HTTP_200_OK)


class OwnerRejectPartialPaymentView(APIView):
    """Owner rejects partial payment invoice"""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Owner rejects partial payment invoice",
        parameters=[
            {"name": "tenantId", "in": "query", "required": True, "schema": {"type": "string"}},
            {"name": "invoice_id", "in": "path", "required": True, "schema": {"type": "string"}},
        ],
        request=PartialPaymentRejectRequestSerializer(),
        responses={200: OwnerPartialPaymentListSerializer()},
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

        # Only invoices with pharmacist_approval_status APPROVED can be rejected by owner
        if invoice.pharmacist_approval_status != "APPROVED" or invoice.payment_option != "PARTIAL":
            return Response({"error": "Invoice is not eligible for owner rejection"}, status=status.HTTP_400_BAD_REQUEST)

        # Mark as owner rejected
        invoice.owner_approval_status = "REJECTED"
        invoice.save()

        # Create notification for pharmacist
        rejection_reason = request.data.get("rejectionReason", "")
        if invoice.pharmacist_approved_by:
            Notification.objects.create(
                tenant_id=tenant_id,
                user_id=invoice.pharmacist_approved_by.id,
                title="Partial Payment Rejected by Owner",
                message=f"Owner rejected partial payment for invoice {invoice.invoice_number}. Reason: {rejection_reason}",
                notification_type="owner_partial_payment_rejection",
                related_invoice=invoice,
                created_by=request.user,
                notes=rejection_reason
            )

        # Notify Accountant and Cashier about rejection
        user_tenants = UserTenant.objects.filter(
            tenant_id=tenant_id,
            role__in=["ACCOUNTANT", "CASHIER"]
        )

        notification_message = f"Partial payment rejected for invoice {invoice.invoice_number}. Reason: {rejection_reason}"

        for user_tenant in user_tenants:
            Notification.objects.create(
                tenant_id=tenant_id,
                user_id=user_tenant.user_id,
                title="Partial Payment Rejected by Owner",
                message=notification_message,
                notification_type="owner_partial_payment_rejection",
                related_invoice=invoice,
                created_by=request.user,
                notes=rejection_reason
            )

        return Response({
            "success": True,
            "message": "Partial payment rejected by owner. Notifications sent to Pharmacist, Accountant, and Cashier.",
            "invoiceId": str(invoice.id),
        }, status=status.HTTP_200_OK)
