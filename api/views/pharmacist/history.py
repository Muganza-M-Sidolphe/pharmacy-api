# Pharmacist Approval History Views
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework import status
from datetime import datetime, date, timedelta
from decimal import Decimal
from django.db.models import Q, Sum, Count, F
from django.http import HttpResponse
import csv
from drf_spectacular.utils import extend_schema
from api.models import Sale, SaleItem, UserTenant, Notification
from api.serializers import (
    PharmacistApprovalHistoryListSerializer,
    PharmacistHistorySummarySerializer,
)


class PharmacistApprovalHistoryListView(APIView):
    """Get approval history of all invoices reviewed by pharmacist"""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Get pharmacist approval history with filters",
        parameters=[
            {"name": "tenantId", "in": "query", "required": True, "schema": {"type": "string"}},
            {"name": "status", "in": "query", "schema": {"type": "string", "enum": ["COMPLETED", "REJECTED"]}},
            {"name": "paymentMethod", "in": "query", "schema": {"type": "string"}},
            {"name": "startDate", "in": "query", "schema": {"type": "string", "format": "date"}},
            {"name": "endDate", "in": "query", "schema": {"type": "string", "format": "date"}},
            {"name": "search", "in": "query", "schema": {"type": "string"}},
            {"name": "page", "in": "query", "schema": {"type": "integer", "default": 1}},
            {"name": "pageSize", "in": "query", "schema": {"type": "integer", "default": 10}},
        ],
        responses={200: PharmacistApprovalHistoryListSerializer()},
    )
    def get(self, request):
        tenant_id = request.query_params.get("tenantId")
        status_filter = request.query_params.get("status")
        payment_method = request.query_params.get("paymentMethod")
        start_date = request.query_params.get("startDate")
        end_date = request.query_params.get("endDate")
        search_query = request.query_params.get("search", "").strip()
        page = int(request.query_params.get("page", 1))
        page_size = int(request.query_params.get("pageSize", 10))

        if not tenant_id:
            return Response({"error": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            UserTenant.objects.get(user=request.user, tenant_id=tenant_id)
        except UserTenant.DoesNotExist:
            return Response({"error": "Not authorized for this tenant"}, status=status.HTTP_403_FORBIDDEN)

        # Query invoices approved by pharmacist (status COMPLETED or REJECTED)
        approvals = Sale.objects.filter(
            tenant_id=tenant_id,
            pharmacist_approval_status__in=["APPROVED", "REJECTED"]
        ).order_by("-pharmacist_approved_at")

        # Filter by status (approved/rejected)
        if status_filter:
            if status_filter == "COMPLETED":
                approvals = approvals.filter(status="COMPLETED")
            elif status_filter == "REJECTED":
                approvals = approvals.filter(pharmacist_approval_status="REJECTED")

        # Filter by payment method
        if payment_method:
            approvals = approvals.filter(payment_method=payment_method)

        # Filter by date range
        if start_date:
            try:
                start_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
                approvals = approvals.filter(pharmacist_approved_at__date__gte=start_dt)
            except ValueError:
                pass

        if end_date:
            try:
                end_dt = datetime.strptime(end_date, "%Y-%m-%d").date()
                approvals = approvals.filter(pharmacist_approved_at__date__lte=end_dt)
            except ValueError:
                pass

        # Search by invoice number, customer name, or phone
        if search_query:
            approvals = approvals.filter(
                Q(invoice_number__icontains=search_query) |
                Q(customer__name__icontains=search_query) |
                Q(customer__phone__icontains=search_query)
            )

        # Pagination
        total_count = approvals.count()
        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size
        paginated_approvals = approvals[start_idx:end_idx]

        # Serialize
        results = []
        for invoice in paginated_approvals:
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
                "status": invoice.status,
                "approvalStatus": invoice.pharmacist_approval_status,
                "invoiceDate": invoice.created_at,
                "approvedAt": invoice.pharmacist_approved_at,
                "approvedBy": invoice.pharmacist_approved_by.get_full_name() if invoice.pharmacist_approved_by else "",
                "notes": "",
            })

        response_data = {
            "count": total_count,
            "next": f"/api/pharmacist/approval-history/?tenantId={tenant_id}&page={page + 1}&pageSize={page_size}" if end_idx < total_count else None,
            "previous": f"/api/pharmacist/approval-history/?tenantId={tenant_id}&page={page - 1}&pageSize={page_size}" if page > 1 else None,
            "results": results,
        }

        return Response(response_data, status=status.HTTP_200_OK)


class PharmacistHistorySummaryView(APIView):
    """Get summary KPIs for pharmacist approval history"""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Get pharmacist approval history summary statistics",
        parameters=[
            {"name": "tenantId", "in": "query", "required": True, "schema": {"type": "string"}},
        ],
        responses={200: PharmacistHistorySummarySerializer()},
    )
    def get(self, request):
        tenant_id = request.query_params.get("tenantId")

        if not tenant_id:
            return Response({"error": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            UserTenant.objects.get(user=request.user, tenant_id=tenant_id)
        except UserTenant.DoesNotExist:
            return Response({"error": "Not authorized for this tenant"}, status=status.HTTP_403_FORBIDDEN)

        # Total approvals (pharmacist_approval_status = APPROVED, status = COMPLETED)
        approved_invoices = Sale.objects.filter(
            tenant_id=tenant_id,
            pharmacist_approval_status="APPROVED",
            status="COMPLETED"
        )
        total_approvals = approved_invoices.count()
        total_approved_value = approved_invoices.aggregate(Sum("total_amount"))["total_amount__sum"] or Decimal("0")

        # Pending approval (status APPROVED, pharmacist_approval_status PENDING)
        pending_approvals = Sale.objects.filter(
            tenant_id=tenant_id,
            status="APPROVED",
            pharmacist_approval_status__in=["PENDING", None]
        ).count()

        # Partial payments (payment_option PARTIAL, pharmacist_approval_status APPROVED)
        partial_payments = Sale.objects.filter(
            tenant_id=tenant_id,
            payment_option="PARTIAL",
            pharmacist_approval_status="APPROVED"
        ).count()

        # Total value (all invoices processed by pharmacist)
        all_processed = Sale.objects.filter(
            tenant_id=tenant_id,
            pharmacist_approval_status__in=["APPROVED", "REJECTED"]
        )
        total_value = all_processed.aggregate(Sum("total_amount"))["total_amount__sum"] or Decimal("0")

        # Total rejected
        total_rejected = Sale.objects.filter(
            tenant_id=tenant_id,
            pharmacist_approval_status="REJECTED"
        ).count()

        return Response({
            "totalApprovals": total_approvals,
            "pendingApproval": pending_approvals,
            "partialPayments": partial_payments,
            "totalValue": str(total_value),
            "totalApprovedValue": str(total_approved_value),
            "totalRejected": total_rejected,
        }, status=status.HTTP_200_OK)


class PharmacistHistoryExportView(APIView):
    """Export pharmacist approval history as CSV"""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Export pharmacist approval history as CSV",
        parameters=[
            {"name": "tenantId", "in": "query", "required": True, "schema": {"type": "string"}},
            {"name": "status", "in": "query", "schema": {"type": "string"}},
            {"name": "paymentMethod", "in": "query", "schema": {"type": "string"}},
            {"name": "startDate", "in": "query", "schema": {"type": "string", "format": "date"}},
            {"name": "endDate", "in": "query", "schema": {"type": "string", "format": "date"}},
        ],
        responses={200: {"type": "string", "format": "binary"}},
    )
    def get(self, request):
        tenant_id = request.query_params.get("tenantId")
        status_filter = request.query_params.get("status")
        payment_method = request.query_params.get("paymentMethod")
        start_date = request.query_params.get("startDate")
        end_date = request.query_params.get("endDate")

        if not tenant_id:
            return Response({"error": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            UserTenant.objects.get(user=request.user, tenant_id=tenant_id)
        except UserTenant.DoesNotExist:
            return Response({"error": "Not authorized for this tenant"}, status=status.HTTP_403_FORBIDDEN)

        # Query invoices
        approvals = Sale.objects.filter(
            tenant_id=tenant_id,
            pharmacist_approval_status__in=["APPROVED", "REJECTED"]
        ).order_by("-pharmacist_approved_at")

        # Apply filters
        if status_filter:
            if status_filter == "COMPLETED":
                approvals = approvals.filter(status="COMPLETED")
            elif status_filter == "REJECTED":
                approvals = approvals.filter(pharmacist_approval_status="REJECTED")

        if payment_method:
            approvals = approvals.filter(payment_method=payment_method)

        if start_date:
            try:
                start_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
                approvals = approvals.filter(pharmacist_approved_at__date__gte=start_dt)
            except ValueError:
                pass

        if end_date:
            try:
                end_dt = datetime.strptime(end_date, "%Y-%m-%d").date()
                approvals = approvals.filter(pharmacist_approved_at__date__lte=end_dt)
            except ValueError:
                pass

        # Create CSV response
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="pharmacist_approval_history.csv"'

        writer = csv.writer(response)
        writer.writerow([
            "Invoice Number", "Customer", "Phone", "Total Amount", "Paid Amount",
            "Due Amount", "Payment Method", "Payment Option", "Status", "Approval Status",
            "Invoice Date", "Approved At", "Approved By"
        ])

        for invoice in approvals:
            customer = invoice.customer
            writer.writerow([
                invoice.invoice_number,
                customer.name if customer else "",
                customer.phone if customer else "",
                str(invoice.total_amount),
                str(invoice.paid_amount),
                str(invoice.due_amount),
                invoice.payment_method or "",
                invoice.payment_option,
                invoice.status,
                invoice.pharmacist_approval_status,
                invoice.created_at.strftime("%Y-%m-%d %H:%M"),
                invoice.pharmacist_approved_at.strftime("%Y-%m-%d %H:%M") if invoice.pharmacist_approved_at else "",
                invoice.pharmacist_approved_by.get_full_name() if invoice.pharmacist_approved_by else "",
            ])

        return response


class PharmacistHistorySearchView(APIView):
    """Search pharmacist approval history"""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Search pharmacist approval history",
        parameters=[
            {"name": "tenantId", "in": "query", "required": True, "schema": {"type": "string"}},
            {"name": "query", "in": "query", "required": True, "schema": {"type": "string"}},
        ],
        responses={200: PharmacistApprovalHistoryListSerializer()},
    )
    def get(self, request):
        tenant_id = request.query_params.get("tenantId")
        search_query = request.query_params.get("query", "").strip()

        if not tenant_id:
            return Response({"error": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        if not search_query:
            return Response({"error": "query is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            UserTenant.objects.get(user=request.user, tenant_id=tenant_id)
        except UserTenant.DoesNotExist:
            return Response({"error": "Not authorized for this tenant"}, status=status.HTTP_403_FORBIDDEN)

        # Search in invoice number, customer name, phone
        approvals = Sale.objects.filter(
            Q(invoice_number__icontains=search_query) |
            Q(customer__name__icontains=search_query) |
            Q(customer__phone__icontains=search_query),
            tenant_id=tenant_id,
            pharmacist_approval_status__in=["APPROVED", "REJECTED"]
        ).order_by("-pharmacist_approved_at")[:20]

        results = []
        for invoice in approvals:
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
                "status": invoice.status,
                "approvalStatus": invoice.pharmacist_approval_status,
                "invoiceDate": invoice.created_at,
                "approvedAt": invoice.pharmacist_approved_at,
                "approvedBy": invoice.pharmacist_approved_by.get_full_name() if invoice.pharmacist_approved_by else "",
                "notes": "",
            })

        return Response({
            "count": len(results),
            "results": results,
        }, status=status.HTTP_200_OK)
