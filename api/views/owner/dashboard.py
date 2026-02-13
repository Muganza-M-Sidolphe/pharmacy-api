from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum, Value
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ...models import Medicine, Sale, StockBatch, UserTenant


LOW_STOCK_THRESHOLD = 10


class OwnerDashboardBaseView(APIView):
    permission_classes = [IsAuthenticated]

    def _get_tenant_id(self, request):
        tenant_id = request.query_params.get("tenantId")
        if not tenant_id:
            return None, Response(
                {"detail": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST
            )
        return tenant_id, None

    def _ensure_owner_access(self, request, tenant_id):
        has_access = UserTenant.objects.filter(
            user=request.user,
            tenant_id=tenant_id,
            role="OWNER",
        ).exists()

        if not has_access:
            return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)
        return None

    def _parse_positive_int(self, value, field_name):
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None, Response(
                {"detail": f"{field_name} must be a valid integer"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if parsed <= 0:
            return None, Response(
                {"detail": f"{field_name} must be greater than 0"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return parsed, None


class OwnerDashboardSummaryView(OwnerDashboardBaseView):
    @extend_schema(
        description="Owner dashboard KPI cards: total medicines, low stock items, total sales, expiring soon",
        tags=["owner"],
    )
    def get(self, request):
        tenant_id, error = self._get_tenant_id(request)
        if error:
            return error

        access_error = self._ensure_owner_access(request, tenant_id)
        if access_error:
            return access_error

        today = timezone.now().date()
        expiring_cutoff = today + timedelta(days=30)

        total_medicines = Medicine.objects.filter(tenant_id=tenant_id).count()

        low_stock_items = (
            Medicine.objects.filter(tenant_id=tenant_id)
            .annotate(total_stock=Coalesce(Sum("batches__quantity"), Value(0)))
            .filter(total_stock__lt=LOW_STOCK_THRESHOLD)
            .count()
        )

        total_sales = (
            Sale.objects.filter(tenant_id=tenant_id, status__in=["APPROVED", "COMPLETED"])
            .aggregate(total=Coalesce(Sum("total_amount"), Decimal("0.00")))
            .get("total")
            or Decimal("0.00")
        )

        expiring_soon = (
            StockBatch.objects.filter(
                medicine__tenant_id=tenant_id,
                quantity__gt=0,
                expiry_date__isnull=False,
                expiry_date__gte=today,
                expiry_date__lte=expiring_cutoff,
            )
            .values("medicine_id")
            .distinct()
            .count()
        )

        return Response(
            {
                "totalMedicines": total_medicines,
                "lowStockItems": low_stock_items,
                "totalSales": str(total_sales),
                "expiringSoon": expiring_soon,
                "currency": request.query_params.get("currency", "RWF"),
            }
        )


class OwnerDashboardSalesTrendView(OwnerDashboardBaseView):
    @extend_schema(
        description="Owner dashboard sales trend for the last N days (default 7)",
        tags=["owner"],
    )
    def get(self, request):
        tenant_id, error = self._get_tenant_id(request)
        if error:
            return error

        access_error = self._ensure_owner_access(request, tenant_id)
        if access_error:
            return access_error

        days, parse_error = self._parse_positive_int(
            request.query_params.get("days", 7), "days"
        )
        if parse_error:
            return parse_error

        today = timezone.now().date()
        start_date = today - timedelta(days=days - 1)

        daily_sales = (
            Sale.objects.filter(
                tenant_id=tenant_id,
                status__in=["APPROVED", "COMPLETED"],
                created_at__date__gte=start_date,
                created_at__date__lte=today,
            )
            .annotate(day=TruncDate("created_at"))
            .values("day")
            .annotate(amount=Coalesce(Sum("total_amount"), Decimal("0.00")))
            .order_by("day")
        )

        amount_by_day = {item["day"]: item["amount"] for item in daily_sales}

        labels = []
        data = []
        points = []
        for offset in range(days):
            day = start_date + timedelta(days=offset)
            amount = amount_by_day.get(day, Decimal("0.00"))
            day_str = day.isoformat()
            labels.append(day_str)
            data.append(float(amount))
            points.append({"date": day_str, "sales": str(amount)})

        return Response({
            "period": f"Last {days} days",
            "labels": labels,
            "data": data,
            "points": points,
        })


class OwnerDashboardPartialInvoicesView(OwnerDashboardBaseView):
    @extend_schema(
        description="Owner dashboard table for partially paid invoices",
        tags=["owner"],
    )
    def get(self, request):
        tenant_id, error = self._get_tenant_id(request)
        if error:
            return error

        access_error = self._ensure_owner_access(request, tenant_id)
        if access_error:
            return access_error

        page, page_error = self._parse_positive_int(
            request.query_params.get("page", 1), "page"
        )
        if page_error:
            return page_error

        page_size, page_size_error = self._parse_positive_int(
            request.query_params.get("pageSize", 10), "pageSize"
        )
        if page_size_error:
            return page_size_error

        invoices_qs = Sale.objects.filter(
            tenant_id=tenant_id,
            payment_option="PARTIAL",
            due_amount__gt=0,
            status__in=["APPROVED", "COMPLETED"],
        ).order_by("-created_at")

        total_count = invoices_qs.count()
        start = (page - 1) * page_size
        end = start + page_size

        results = []
        for sale in invoices_qs[start:end]:
            results.append(
                {
                    "invoiceId": str(sale.id),
                    "invoiceNumber": sale.invoice_number,
                    "client": sale.customer_name or "Walk-in Customer",
                    "clientPhone": sale.customer_phone or "",
                    "totalAmount": str(sale.total_amount),
                    "paidAmount": str(sale.paid_amount),
                    "remaining": str(sale.due_amount),
                    "status": "Partially Paid",
                    "createdAt": sale.created_at,
                }
            )

        return Response(
            {
                "count": total_count,
                "next": page + 1 if end < total_count else None,
                "previous": page - 1 if page > 1 else None,
                "results": results,
            }
        )


class OwnerDashboardView(OwnerDashboardBaseView):
    @extend_schema(
        description="Combined owner dashboard payload (summary, sales trend, partially paid invoices)",
        tags=["owner"],
    )
    def get(self, request):
        summary_response = OwnerDashboardSummaryView().get(request)
        if summary_response.status_code != status.HTTP_200_OK:
            return summary_response

        trend_response = OwnerDashboardSalesTrendView().get(request)
        if trend_response.status_code != status.HTTP_200_OK:
            return trend_response

        partial_invoices_response = OwnerDashboardPartialInvoicesView().get(request)
        if partial_invoices_response.status_code != status.HTTP_200_OK:
            return partial_invoices_response

        return Response(
            {
                "ownerName": request.user.name,
                "summary": summary_response.data,
                "salesTrend": trend_response.data,
                "partiallyPaidInvoices": partial_invoices_response.data,
            }
        )
