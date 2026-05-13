from datetime import datetime, timedelta
from decimal import Decimal

from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce, TruncDate
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ...models import Sale, SaleItem, Tenant, UserTenant
from ...utils.subscription_access import authorize_tenant_access


class OwnerReportBaseView(APIView):
    permission_classes = [IsAuthenticated]
    required_subscription_feature = "advanced_reports"

    def _get_tenant(self, request):
        tenant_id = request.query_params.get("tenantId")
        tenant, error_message, error_status = authorize_tenant_access(
            request,
            tenant_id,
            required_role="OWNER",
            required_feature=self.required_subscription_feature,
        )
        if error_message:
            return None, None, Response({"detail": error_message}, status=error_status)

        return tenant_id, tenant, None

    def _parse_date(self, value, field_name):
        try:
            return datetime.strptime(value, "%Y-%m-%d").date(), None
        except ValueError:
            return None, Response(
                {"detail": f"{field_name} must be in YYYY-MM-DD format"},
                status=status.HTTP_400_BAD_REQUEST,
            )

    def _date_range(self, request):
        end_raw = request.query_params.get("endDate")
        start_raw = request.query_params.get("startDate")

        if end_raw:
            end_date, error = self._parse_date(end_raw, "endDate")
            if error:
                return None, None, error
        else:
            end_date = datetime.now().date()

        if start_raw:
            start_date, error = self._parse_date(start_raw, "startDate")
            if error:
                return None, None, error
        else:
            start_date = end_date - timedelta(days=6)

        if start_date > end_date:
            return None, None, Response(
                {"detail": "startDate cannot be greater than endDate"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return start_date, end_date, None

    def _owner_sales_queryset(self, tenant_id):
        return Sale.objects.filter(
            tenant_id=tenant_id,
            status__in=["APPROVED", "COMPLETED"],
        ).exclude(cashier__department="RETAIL")


class OwnerSalesReportsDashboardView(OwnerReportBaseView):
    """Owner sales reports dashboard matching reports screen cards/charts."""

    @extend_schema(
        description="Owner sales report dashboard: KPI cards, trend charts, category distribution, key metrics",
        tags=["owner"],
    )
    def get(self, request):
        tenant_id, tenant, tenant_error = self._get_tenant(request)
        if tenant_error:
            return tenant_error

        start_date, end_date, date_error = self._date_range(request)
        if date_error:
            return date_error

        sales_qs = self._owner_sales_queryset(tenant_id).filter(
            created_at__date__gte=start_date,
            created_at__date__lte=end_date,
        )

        aggregate = sales_qs.aggregate(
            total_revenue=Coalesce(Sum("paid_amount"), Decimal("0.00")),
            total_sales=Count("id"),
        )

        total_revenue = aggregate["total_revenue"] or Decimal("0.00")
        total_sales = aggregate["total_sales"] or 0
        average_sale = total_revenue / total_sales if total_sales else Decimal("0.00")

        top_product_row = (
            SaleItem.objects.filter(sale__in=sales_qs)
            .values("medicine__brand_name")
            .annotate(amount=Coalesce(Sum("subtotal"), Decimal("0.00")))
            .order_by("-amount")
            .first()
        )
        top_product = top_product_row["medicine__brand_name"] if top_product_row else "N/A"

        daily_rows = (
            sales_qs.annotate(day=TruncDate("created_at"))
            .values("day")
            .annotate(amount=Coalesce(Sum("paid_amount"), Decimal("0.00")))
            .order_by("day")
        )
        amount_by_day = {row["day"]: row["amount"] for row in daily_rows}

        labels = []
        line_data = []
        points = []
        days_count = (end_date - start_date).days + 1
        for i in range(days_count):
            day = start_date + timedelta(days=i)
            amount = amount_by_day.get(day, Decimal("0.00"))
            labels.append(day.strftime("%b %d"))
            line_data.append(float(amount))
            points.append({"date": day.isoformat(), "revenue": str(amount)})

        category_rows = (
            SaleItem.objects.filter(sale__in=sales_qs)
            .values("medicine__category")
            .annotate(amount=Coalesce(Sum("subtotal"), Decimal("0.00")))
            .order_by("-amount")
        )

        category_items = []
        for row in category_rows:
            amount = row["amount"] or Decimal("0.00")
            percentage = float((amount / total_revenue) * 100) if total_revenue else 0.0
            category_items.append(
                {
                    "category": row["medicine__category"] or "Uncategorized",
                    "amount": str(amount),
                    "percentage": round(percentage, 2),
                }
            )

        # Key metrics
        daily_average = total_revenue / days_count if days_count else Decimal("0.00")
        weekly_average = daily_average * Decimal("7")

        today = datetime.now().date()
        month_start = today.replace(day=1)
        monthly_revenue = (
            self._owner_sales_queryset(tenant_id).filter(
                created_at__date__gte=month_start,
                created_at__date__lte=today,
            ).aggregate(total=Coalesce(Sum("paid_amount"), Decimal("0.00")))["total"]
            or Decimal("0.00")
        )

        previous_start = start_date - timedelta(days=days_count)
        previous_end = start_date - timedelta(days=1)
        previous_revenue = (
            self._owner_sales_queryset(tenant_id).filter(
                created_at__date__gte=previous_start,
                created_at__date__lte=previous_end,
            ).aggregate(total=Coalesce(Sum("paid_amount"), Decimal("0.00")))["total"]
            or Decimal("0.00")
        )

        if previous_revenue > 0:
            growth_rate = ((total_revenue - previous_revenue) / previous_revenue) * 100
        else:
            growth_rate = Decimal("0.00")

        return Response(
            {
                "filters": {
                    "startDate": start_date.isoformat(),
                    "endDate": end_date.isoformat(),
                },
                "summary": {
                    "totalRevenue": str(total_revenue),
                    "totalSales": total_sales,
                    "averageSale": str(average_sale.quantize(Decimal("0.01"))),
                    "topProduct": top_product,
                    "currency": tenant.currency,
                },
                "salesTrend": {
                    "period": f"Last {days_count} Days",
                    "labels": labels,
                    "data": line_data,
                    "points": points,
                },
                "revenueByDay": {
                    "labels": labels,
                    "data": line_data,
                },
                "categoryDistribution": {
                    "totalCategories": len(category_items),
                    "items": category_items,
                },
                "keyMetrics": {
                    "dailyAverage": str(daily_average.quantize(Decimal("0.01"))),
                    "weeklyAverage": str(weekly_average.quantize(Decimal("0.01"))),
                    "monthlyRevenue": str(monthly_revenue.quantize(Decimal("0.01"))),
                    "growthRate": str(growth_rate.quantize(Decimal("0.01"))),
                    "totalCategories": len(category_items),
                },
            },
            status=status.HTTP_200_OK,
        )


class OwnerUserManagementReportView(OwnerReportBaseView):
    """Owner report endpoint for User Management page."""

    @extend_schema(
        description="Owner user management report (summary cards + user list)",
        tags=["owner"],
    )
    def get(self, request):
        tenant_id, _, tenant_error = self._get_tenant(request)
        if tenant_error:
            return tenant_error

        role = request.query_params.get("role")
        status_filter = request.query_params.get("status")
        search = request.query_params.get("search", "").strip()

        try:
            page = int(request.query_params.get("page", 1))
            page_size = int(request.query_params.get("pageSize", 10))
        except ValueError:
            return Response(
                {"detail": "page and pageSize must be integers"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if page <= 0 or page_size <= 0:
            return Response(
                {"detail": "page and pageSize must be greater than 0"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        base_qs = UserTenant.objects.filter(tenant_id=tenant_id).select_related("user")

        role_counts = {
            row["role"]: row["count"]
            for row in base_qs.values("role").annotate(count=Count("id"))
        }

        total_users = base_qs.count()
        active_users = base_qs.filter(user__is_active=True).count()

        filtered_qs = base_qs
        if role:
            filtered_qs = filtered_qs.filter(role=role)

        if status_filter:
            status_lower = status_filter.lower()
            if status_lower == "active":
                filtered_qs = filtered_qs.filter(user__is_active=True)
            elif status_lower == "inactive":
                filtered_qs = filtered_qs.filter(user__is_active=False)

        if search:
            filtered_qs = filtered_qs.filter(
                Q(user__name__icontains=search)
                | Q(user__email__icontains=search)
                | Q(role__icontains=search)
            )

        paginator = Paginator(filtered_qs.order_by("-user__created_at"), page_size)
        page_obj = paginator.get_page(page)

        results = []
        for membership in page_obj:
            results.append(
                {
                    "id": str(membership.user.id),
                    "name": membership.user.name,
                    "email": membership.user.email,
                    "role": membership.role,
                    "status": "Active" if membership.user.is_active else "Inactive",
                    "createdAt": membership.user.created_at,
                    "actions": {
                        "edit": f"/api/owner/users/{membership.user.id}/?tenantId={tenant_id}",
                        "toggleStatus": f"/api/owner/users/{membership.user.id}/status/?tenantId={tenant_id}",
                        "resetPassword": f"/api/owner/users/{membership.user.id}/reset-password/?tenantId={tenant_id}",
                    },
                }
            )

        return Response(
            {
                "summary": {
                    "totalUsers": total_users,
                    "activeUsers": active_users,
                    "owners": role_counts.get("OWNER", 0),
                    "cashiers": role_counts.get("CASHIER", 0),
                    "storeKeepers": role_counts.get("STORE_KEEPER", 0),
                    "accountants": role_counts.get("ACCOUNTANT", 0),
                    "pharmacists": role_counts.get("PHARMACIST", 0),
                },
                "list": {
                    "count": paginator.count,
                    "next": page + 1 if page_obj.has_next() else None,
                    "previous": page - 1 if page_obj.has_previous() else None,
                    "results": results,
                },
                "actions": {
                    "addUser": "/api/owner/create-user/",
                    "searchUsers": "/api/users/search/",
                },
            },
            status=status.HTTP_200_OK,
        )


class OwnerUsersSummaryCardsView(OwnerReportBaseView):
    """Owner users summary cards only."""

    @extend_schema(
        description="Owner users summary cards",
        tags=["owner"],
    )
    def get(self, request):
        tenant_id, _, tenant_error = self._get_tenant(request)
        if tenant_error:
            return tenant_error

        qs = UserTenant.objects.filter(tenant_id=tenant_id)
        role_counts = {row["role"]: row["count"] for row in qs.values("role").annotate(count=Count("id"))}

        return Response(
            {
                "totalUsers": qs.count(),
                "activeUsers": qs.filter(user__is_active=True).count(),
                "owners": role_counts.get("OWNER", 0),
                "cashiers": role_counts.get("CASHIER", 0),
                "storeKeepers": role_counts.get("STORE_KEEPER", 0),
                "accountants": role_counts.get("ACCOUNTANT", 0),
                "pharmacists": role_counts.get("PHARMACIST", 0),
            },
            status=status.HTTP_200_OK,
        )
