from datetime import datetime, timedelta
from decimal import Decimal
import csv

from django.http import HttpResponse
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ...models import Sale, UserTenant
from ...serializers import (
    DailySalesTrendSerializer,
    PaymentMethodsDistributionSerializer,
    SalesSummarySerializer,
)


class OwnerSalesBaseView(APIView):
    permission_classes = [IsAuthenticated]

    def _validate_owner_access(self, request):
        tenant_id = request.query_params.get("tenantId")
        if not tenant_id:
            return None, Response(
                {"detail": "tenantId is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        is_owner = UserTenant.objects.filter(
            user=request.user,
            tenant_id=tenant_id,
            role="OWNER",
        ).exists()
        if not is_owner:
            return None, Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        return tenant_id, None

    def _parse_date_range(self, request):
        start_date = request.query_params.get("startDate")
        end_date = request.query_params.get("endDate")

        try:
            if start_date:
                start = datetime.strptime(start_date, "%Y-%m-%d").date()
            else:
                start = (timezone.now() - timedelta(days=6)).date()

            if end_date:
                end = datetime.strptime(end_date, "%Y-%m-%d").date()
            else:
                end = timezone.now().date()
        except ValueError:
            return None, None, Response(
                {"detail": "Invalid date format. Use YYYY-MM-DD."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if start > end:
            return None, None, Response(
                {"detail": "startDate cannot be greater than endDate"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return start, end, None


class OwnerSalesSummaryView(OwnerSalesBaseView):
    """Summary metrics for owner sales dashboard."""

    @extend_schema(
        description="Get owner sales summary: total sales, revenue, average order, unique customers",
        responses=SalesSummarySerializer,
        tags=["owner"],
    )
    def get(self, request):
        tenant_id, error = self._validate_owner_access(request)
        if error:
            return error

        qs = Sale.objects.filter(tenant_id=tenant_id, status__in=["APPROVED", "COMPLETED"])

        total_sales = qs.count()
        total_revenue = sum((sale.total_amount for sale in qs), Decimal("0.00"))
        average_order = (total_revenue / total_sales) if total_sales else Decimal("0.00")

        unique_keys = set()
        for sale in qs:
            key = sale.customer_phone or sale.customer_name
            if key:
                unique_keys.add(key)

        return Response(
            {
                "totalSales": total_sales,
                "totalRevenue": str(total_revenue),
                "averageOrderValue": str(average_order.quantize(Decimal("0.01"))),
                "uniqueCustomers": len(unique_keys),
            }
        )


class OwnerSalesDashboardView(OwnerSalesBaseView):
    """Single endpoint for owner sales page (cards + charts + table)."""

    @extend_schema(
        description=(
            "Owner sales consolidated payload (summary cards, trend chart, payment "
            "distribution chart, recent sales table)"
        ),
        tags=["owner"],
    )
    def get(self, request):
        tenant_id, error = self._validate_owner_access(request)
        if error:
            return error

        start, end, date_error = self._parse_date_range(request)
        if date_error:
            return date_error

        qs = Sale.objects.filter(
            tenant_id=tenant_id,
            status__in=["APPROVED", "COMPLETED"],
            created_at__date__gte=start,
            created_at__date__lte=end,
        ).order_by("-created_at")
        sales = list(qs)

        total_sales = len(sales)
        total_revenue = sum((sale.total_amount for sale in sales), Decimal("0.00"))
        average_order = (total_revenue / total_sales) if total_sales else Decimal("0.00")

        unique_keys = set()
        payment_dist = {}
        recent_sales = []

        for sale in sales:
            key = sale.customer_phone or sale.customer_name
            if key:
                unique_keys.add(key)

            method = sale.payment_method or "UNKNOWN"
            if method not in payment_dist:
                payment_dist[method] = {"count": 0, "amount": Decimal("0.00")}
            payment_dist[method]["count"] += 1
            payment_dist[method]["amount"] += sale.paid_amount

        for sale in sales[:10]:
            recent_sales.append(
                {
                    "saleId": str(sale.id),
                    "invoiceNumber": sale.invoice_number,
                    "customerName": sale.customer_name or "Walk-in Customer",
                    "paymentMethod": sale.payment_method,
                    "paymentOption": sale.payment_option,
                    "status": sale.status,
                    "paidAmount": str(sale.paid_amount),
                    "totalAmount": str(sale.total_amount),
                    "createdAt": sale.created_at,
                }
            )

        labels = []
        trend_data = []
        for i in range((end - start).days + 1):
            day = start + timedelta(days=i)
            day_total = sum(
                (
                    sale.total_amount
                    for sale in sales
                    if sale.created_at.date() == day
                ),
                Decimal("0.00"),
            )
            labels.append(day.strftime("%b %d"))
            trend_data.append(float(day_total))

        distribution = []
        for method, values in payment_dist.items():
            percentage = (values["count"] / total_sales * 100) if total_sales else 0
            distribution.append(
                {
                    "method": method,
                    "count": values["count"],
                    "amount": str(values["amount"]),
                    "percentage": round(percentage, 2),
                }
            )

        return Response(
            {
                "filters": {
                    "startDate": start.isoformat(),
                    "endDate": end.isoformat(),
                },
                "summary": {
                    "totalSales": total_sales,
                    "totalRevenue": str(total_revenue),
                    "averageOrderValue": str(average_order.quantize(Decimal("0.01"))),
                    "uniqueCustomers": len(unique_keys),
                },
                "salesTrend": {
                    "labels": labels,
                    "data": trend_data,
                },
                "paymentDistribution": {
                    "total": total_sales,
                    "distribution": distribution,
                },
                "recentSales": {
                    "count": total_sales,
                    "results": recent_sales,
                },
            }
        )


class OwnerDailySalesTrendView(OwnerSalesBaseView):
    """Daily sales totals for a date range (defaults to last 7 days)."""

    @extend_schema(
        description="Get owner daily sales totals for charting",
        responses=DailySalesTrendSerializer,
        tags=["owner"],
    )
    def get(self, request):
        tenant_id, error = self._validate_owner_access(request)
        if error:
            return error

        start, end, date_error = self._parse_date_range(request)
        if date_error:
            return date_error

        labels = []
        data = []
        for i in range((end - start).days + 1):
            day = start + timedelta(days=i)
            day_sales = Sale.objects.filter(
                tenant_id=tenant_id,
                status__in=["APPROVED", "COMPLETED"],
                created_at__date=day,
            )
            day_total = sum((sale.total_amount for sale in day_sales), Decimal("0.00"))
            labels.append(day.strftime("%a"))
            data.append(float(day_total))

        return Response({"labels": labels, "data": data})


class OwnerPaymentMethodsDistributionView(OwnerSalesBaseView):
    """Distribution of payment methods for owner sales."""

    @extend_schema(
        description="Get owner payment methods distribution",
        responses=PaymentMethodsDistributionSerializer,
        tags=["owner"],
    )
    def get(self, request):
        tenant_id, error = self._validate_owner_access(request)
        if error:
            return error

        start_date = request.query_params.get("startDate")
        end_date = request.query_params.get("endDate")

        qs = Sale.objects.filter(tenant_id=tenant_id, status__in=["APPROVED", "COMPLETED"])

        if start_date:
            try:
                start = datetime.strptime(start_date, "%Y-%m-%d").date()
                qs = qs.filter(created_at__date__gte=start)
            except ValueError:
                return Response(
                    {"detail": "Invalid startDate format. Use YYYY-MM-DD."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        if end_date:
            try:
                end = datetime.strptime(end_date, "%Y-%m-%d").date()
                qs = qs.filter(created_at__date__lte=end)
            except ValueError:
                return Response(
                    {"detail": "Invalid endDate format. Use YYYY-MM-DD."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        total_count = qs.count()
        by_method = {}
        for sale in qs:
            method = sale.payment_method or "UNKNOWN"
            if method not in by_method:
                by_method[method] = {"count": 0, "amount": Decimal("0.00")}
            by_method[method]["count"] += 1
            by_method[method]["amount"] += sale.paid_amount

        distribution = []
        for method, values in by_method.items():
            percentage = (values["count"] / total_count * 100) if total_count else 0
            distribution.append(
                {
                    "method": method,
                    "count": values["count"],
                    "amount": str(values["amount"]),
                    "percentage": round(percentage, 1),
                }
            )

        return Response({"total": total_count, "distribution": distribution})


class OwnerExportSalesView(OwnerSalesBaseView):
    """Export owner sales as CSV for a date range."""

    @extend_schema(description="Export owner sales CSV", tags=["owner"], responses=None)
    def get(self, request):
        tenant_id, error = self._validate_owner_access(request)
        if error:
            return error

        start_date = request.query_params.get("startDate")
        end_date = request.query_params.get("endDate")

        qs = Sale.objects.filter(
            tenant_id=tenant_id,
            status__in=["APPROVED", "COMPLETED"],
        ).order_by("created_at")

        if start_date:
            try:
                start = datetime.strptime(start_date, "%Y-%m-%d").date()
                qs = qs.filter(created_at__date__gte=start)
            except ValueError:
                return Response(
                    {"detail": "Invalid startDate format. Use YYYY-MM-DD."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        if end_date:
            try:
                end = datetime.strptime(end_date, "%Y-%m-%d").date()
                qs = qs.filter(created_at__date__lte=end)
            except ValueError:
                return Response(
                    {"detail": "Invalid endDate format. Use YYYY-MM-DD."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        response = HttpResponse(content_type="text/csv")
        filename = f"owner_sales_{tenant_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}.csv"
        response["Content-Disposition"] = f'attachment; filename="{filename}"'

        writer = csv.writer(response)
        writer.writerow(
            [
                "InvoiceNumber",
                "CustomerName",
                "CustomerPhone",
                "TotalAmount",
                "PaidAmount",
                "DueAmount",
                "PaymentMethod",
                "PaymentOption",
                "Status",
                "CreatedAt",
            ]
        )

        for sale in qs:
            writer.writerow(
                [
                    sale.invoice_number,
                    sale.customer_name or "",
                    sale.customer_phone or "",
                    str(sale.total_amount),
                    str(sale.paid_amount),
                    str(sale.due_amount),
                    sale.payment_method,
                    sale.payment_option,
                    sale.status,
                    sale.created_at.isoformat(),
                ]
            )

        return response
