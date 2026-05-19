from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework import status
from datetime import datetime, timedelta, time
from decimal import Decimal
from django.db.models import Q, Sum, Count, F, DecimalField
from django.db.models.functions import TruncDate, TruncHour
from drf_spectacular.utils import extend_schema
from api.models import Expense, Medicine, Sale, SaleItem, Tenant, UserTenant
from api.serializers import (
    AnalyticsDashboardSerializer,
    AnalyticsKPISerializer,
    TrendsAnalysisSerializer,
    DailyRevenueTrendSerializer,
    HourlySalesPatternSerializer,
    RevenueVsTransactionsSerializer,
    ForecastsSerializer,
    BusinessInsightsSerializer,
)
from api.utils.subscription_access import authorize_tenant_access


class AccountantAnalyticsBaseView(APIView):
    permission_classes = [IsAuthenticated]
    required_subscription_feature = "advanced_reports"

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

    def _wholesale_sales_queryset(self, tenant_id):
        return Sale.objects.filter(tenant_id=tenant_id).exclude(cashier__department='RETAIL')


class AccountantAnalyticsDashboardView(AccountantAnalyticsBaseView):
    """Get complete analytics dashboard with KPIs, trends, forecasts, and insights"""

    @extend_schema(
        description="Get analytics dashboard with KPIs, trends, forecasts, and business insights",
        parameters=[{"name": "tenantId", "in": "query", "required": True, "schema": {"type": "string"}},
                    {"name": "days", "in": "query", "required": False, "schema": {"type": "integer", "default": 30}}],
        responses={200: AnalyticsDashboardSerializer()},
    )
    def get(self, request):
        tenant, tenant_id, auth_error = self._authorize(request)
        days = int(request.query_params.get("days", 30))
        if auth_error:
            return auth_error
        currency = (tenant.currency if tenant else "USD")

        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=days)
        previous_start = start_date - timedelta(days=days)

        # Get current period sales
        current_sales = self._wholesale_sales_queryset(tenant_id).filter(
            status="COMPLETED", created_at__date__gte=start_date, created_at__date__lte=end_date
        )

        # Get previous period sales
        previous_sales = self._wholesale_sales_queryset(tenant_id).filter(
            status="COMPLETED", created_at__date__gte=previous_start, created_at__date__lt=start_date
        )

        # Calculate KPIs
        current_revenue = current_sales.aggregate(Sum('paid_amount'))['paid_amount__sum'] or Decimal('0')
        previous_revenue = previous_sales.aggregate(Sum('paid_amount'))['paid_amount__sum'] or Decimal('0')
        revenue_change = ((current_revenue - previous_revenue) / previous_revenue * 100) if previous_revenue else Decimal('0')

        avg_order_value = (current_revenue / current_sales.count()) if current_sales.count() > 0 else Decimal('0')
        unique_customers = current_sales.values('customer_phone').distinct().count()

        # Calculate inventory turnover
        medicines = Medicine.objects.filter(tenant_id=tenant_id)
        sold_items_count = SaleItem.objects.filter(sale__tenant_id=tenant_id).exclude(sale__cashier__department='RETAIL').count() if hasattr(SaleItem, 'objects') else 0
        inventory_turnover = Decimal(sold_items_count) / max(medicines.count(), 1) if medicines.count() > 0 else Decimal('0')

        kpis = {
            "revenue": current_revenue,
            "avgOrderValue": avg_order_value,
            "uniqueCustomers": unique_customers,
            "inventoryTurnover": inventory_turnover,
            "percentChange": revenue_change,
        }

        # Get trends analysis
        daily_trend = self._get_daily_revenue_trend(tenant_id, start_date, end_date, currency)
        hourly_pattern = self._get_hourly_sales_pattern(tenant_id, start_date, end_date)
        revenue_vs_transactions = self._get_revenue_vs_transactions(tenant_id, start_date, end_date, currency)

        trends_analysis = {
            "dailyRevenueTrend": daily_trend,
            "hourlySalesPattern": hourly_pattern,
            "revenueVsTransactions": revenue_vs_transactions,
        }

        # Get forecasts
        forecasts = self._get_forecasts(tenant_id, current_sales, days, currency)

        # Get business insights
        insights = self._get_business_insights(tenant_id, current_sales, current_revenue, unique_customers, currency)

        dashboard_data = {
            "kpis": kpis,
            "trendsAnalysis": trends_analysis,
            "forecasts": forecasts,
            "businessInsights": insights,
        }

        serializer = AnalyticsDashboardSerializer(dashboard_data)
        return Response(serializer.data)

    def _get_daily_revenue_trend(self, tenant_id, start_date, end_date, currency):
        """Calculate daily revenue trend"""
        daily_data = (
            self._wholesale_sales_queryset(tenant_id).filter(
                status="COMPLETED", created_at__date__gte=start_date, created_at__date__lte=end_date
            )
            .annotate(date=TruncDate('created_at'))
            .values('date')
            .annotate(revenue=Sum('paid_amount'), transactions=Count('id'))
            .order_by('date')
        )

        labels = []
        revenue_data = []
        items = []

        for item in daily_data:
            date_str = item['date'].strftime('%Y-%m-%d')
            labels.append(date_str)
            revenue_data.append(float(item['revenue'] or 0))
            items.append({
                'date': item['date'],
                'revenue': item['revenue'] or Decimal('0'),
                'transactions': item['transactions'],
            })

        return {
            "labels": labels,
            "datasets": [{"label": f"Revenue ({currency})", "data": revenue_data, "borderColor": "#10B981", "fill": False}],
            "items": items,
        }

    def _get_hourly_sales_pattern(self, tenant_id, start_date, end_date):
        """Calculate hourly sales pattern"""
        hourly_data = (
            self._wholesale_sales_queryset(tenant_id).filter(
                status="COMPLETED", created_at__date__gte=start_date, created_at__date__lte=end_date
            )
            .annotate(hour=TruncHour('created_at'))
            .values('hour')
            .annotate(sales=Count('id'), revenue=Sum('paid_amount'))
            .order_by('hour')
        )

        labels = []
        sales_data = []
        items = []

        for item in hourly_data:
            hour_str = item['hour'].strftime('%H:%M')
            labels.append(hour_str)
            sales_data.append(item['sales'])
            items.append({
                'hour': item['hour'].time(),
                'sales': item['sales'],
                'revenue': item['revenue'] or Decimal('0'),
            })

        return {
            "labels": labels,
            "datasets": [{"label": "Sales Count", "data": sales_data, "backgroundColor": "#10B981"}],
            "items": items,
        }

    def _get_revenue_vs_transactions(self, tenant_id, start_date, end_date, currency):
        """Calculate revenue vs transactions"""
        daily_data = (
            self._wholesale_sales_queryset(tenant_id).filter(
                status="COMPLETED", created_at__date__gte=start_date, created_at__date__lte=end_date
            )
            .annotate(date=TruncDate('created_at'))
            .values('date')
            .annotate(revenue=Sum('paid_amount'), transactions=Count('id'))
            .order_by('date')
        )

        labels = []
        revenue_data = []
        transaction_data = []
        items = []

        for item in daily_data:
            date_str = item['date'].strftime('%Y-%m-%d')
            labels.append(date_str)
            revenue_data.append(float(item['revenue'] or 0))
            transaction_data.append(item['transactions'])
            items.append({
                'date': item['date'],
                'revenue': item['revenue'] or Decimal('0'),
                'transactions': item['transactions'],
            })

        return {
            "labels": labels,
            "datasets": [
                {"label": f"Revenue ({currency})", "data": revenue_data, "backgroundColor": "#F59E0B", "type": "bar"},
                {"label": "Transactions", "data": transaction_data, "borderColor": "#3B82F6", "type": "line"},
            ],
            "items": items,
        }

    def _get_forecasts(self, tenant_id, sales_queryset, days, currency):
        """Generate forecasts based on historical data"""
        # Simple moving average forecast
        daily_revenue = sales_queryset.annotate(date=TruncDate('created_at')).values('date').annotate(
            revenue=Sum('paid_amount')
        ).order_by('date')

        revenues = [float(item['revenue'] or 0) for item in daily_revenue]
        if len(revenues) == 0:
            return {"labels": [], "datasets": [], "items": []}

        # Calculate simple moving average
        window = max(7, len(revenues) // 3)
        avg_revenue = sum(revenues[-window:]) / window if len(revenues) >= window else sum(revenues) / len(revenues)

        # Generate forecast for next 7 days
        forecast_items = []
        forecast_data = []
        labels = []

        start_forecast = datetime.now().date()
        for i in range(7):
            forecast_date = start_forecast + timedelta(days=i)
            # Simple forecast with small variation
            forecast_value = avg_revenue * (1 + (i * 0.01))
            forecast_items.append({
                'date': forecast_date,
                'forecastedRevenue': Decimal(str(forecast_value)),
                'confidence': Decimal('85'),
            })
            forecast_data.append(forecast_value)
            labels.append(forecast_date.strftime('%Y-%m-%d'))

        return {
            "labels": labels,
            "datasets": [{"label": f"Forecasted Revenue ({currency})", "data": forecast_data, "borderColor": "#8B5CF6", "borderDash": [5, 5]}],
            "items": forecast_items,
        }

    def _get_business_insights(self, tenant_id, sales_queryset, revenue, unique_customers, currency):
        """Generate business insights"""
        insights_list = []

        # Revenue insight
        avg_revenue = (revenue / sales_queryset.count()) if sales_queryset.count() > 0 else Decimal('0')
        insights_list.append({
            "title": "Revenue Performance",
            "description": f"Total revenue of {currency} {revenue:,} from {sales_queryset.count()} transactions",
            "metric": f"{currency} {revenue:,}",
            "trend": "up" if revenue > 0 else "stable",
            "recommendation": "Continue current sales strategies" if revenue > 0 else "Analyze sales patterns",
        })

        # Customer insight
        insights_list.append({
            "title": "Customer Base",
            "description": f"Served {unique_customers} unique customers in the period",
            "metric": str(unique_customers),
            "trend": "up" if unique_customers > 0 else "stable",
            "recommendation": "Focus on customer retention and repeat purchases",
        })

        # Payment method insight
        payment_methods = sales_queryset.values('payment_method').annotate(count=Count('id')).order_by('-count')
        top_payment = payment_methods.first()
        if top_payment:
            insights_list.append({
                "title": "Payment Method Preference",
                "description": f"{top_payment['payment_method']} is the most used payment method",
                "metric": f"{top_payment['count']} transactions",
                "trend": "stable",
                "recommendation": f"Ensure {top_payment['payment_method']} system is reliable",
            })

        # Product insight
        top_products = (
            sales_queryset.prefetch_related('items')
            .values('items__medicine__brand_name')
            .annotate(count=Count('items__id'))
            .order_by('-count')[:3]
        )
        if top_products:
            insights_list.append({
                "title": "Top Selling Products",
                "description": f"{top_products[0].get('items__medicine__brand_name', 'N/A')} leads sales",
                "metric": f"{top_products[0]['count']} units sold",
                "trend": "up",
                "recommendation": "Maintain adequate stock of top sellers",
            })

        return {"insights": insights_list}


class AccountantAnalyticsKPIView(AccountantAnalyticsBaseView):
    """Get analytics KPIs only"""

    @extend_schema(
        description="Get analytics KPIs (Revenue, Avg Order Value, Unique Customers, Inventory Turnover)",
        parameters=[{"name": "tenantId", "in": "query", "required": True, "schema": {"type": "string"}},
                    {"name": "days", "in": "query", "required": False, "schema": {"type": "integer", "default": 30}}],
        responses={200: AnalyticsKPISerializer()},
    )
    def get(self, request):
        _, tenant_id, auth_error = self._authorize(request)
        days = int(request.query_params.get("days", 30))
        if auth_error:
            return auth_error

        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=days)
        previous_start = start_date - timedelta(days=days)

        # Current period
        current_sales = Sale.objects.filter(
            tenant_id=tenant_id, status="COMPLETED", created_at__date__gte=start_date, created_at__date__lte=end_date
        )
        previous_sales = Sale.objects.filter(
            tenant_id=tenant_id, status="COMPLETED", created_at__date__gte=previous_start, created_at__date__lt=start_date
        )

        current_revenue = current_sales.aggregate(Sum('paid_amount'))['paid_amount__sum'] or Decimal('0')
        previous_revenue = previous_sales.aggregate(Sum('paid_amount'))['paid_amount__sum'] or Decimal('0')
        revenue_change = ((current_revenue - previous_revenue) / previous_revenue * 100) if previous_revenue else Decimal('0')

        avg_order_value = (current_revenue / current_sales.count()) if current_sales.count() > 0 else Decimal('0')
        unique_customers = current_sales.values('customer_phone').distinct().count()
        inventory_turnover = Decimal('0')  # Can be calculated based on business logic

        kpi_data = {
            "revenue": current_revenue,
            "avgOrderValue": avg_order_value,
            "uniqueCustomers": unique_customers,
            "inventoryTurnover": inventory_turnover,
            "percentChange": revenue_change,
        }

        serializer = AnalyticsKPISerializer(kpi_data)
        return Response(serializer.data)


class AccountantAnalyticsTrendsView(AccountantAnalyticsBaseView):
    """Get analytics trends (daily revenue, hourly pattern, revenue vs transactions)"""

    @extend_schema(
        description="Get analytics trends: daily revenue trend, hourly sales pattern, revenue vs transactions",
        parameters=[{"name": "tenantId", "in": "query", "required": True, "schema": {"type": "string"}},
                    {"name": "days", "in": "query", "required": False, "schema": {"type": "integer", "default": 7}}],
        responses={200: TrendsAnalysisSerializer()},
    )
    def get(self, request):
        tenant, tenant_id, auth_error = self._authorize(request)
        days = int(request.query_params.get("days", 7))
        if auth_error:
            return auth_error

        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=days)
        currency = (tenant.currency if tenant else "USD")

        dashboard_view = AccountantAnalyticsDashboardView()

        daily_trend = dashboard_view._get_daily_revenue_trend(tenant_id, start_date, end_date, currency)
        hourly_pattern = dashboard_view._get_hourly_sales_pattern(tenant_id, start_date, end_date)
        revenue_vs_transactions = dashboard_view._get_revenue_vs_transactions(tenant_id, start_date, end_date, currency)

        trends_data = {
            "dailyRevenueTrend": daily_trend,
            "hourlySalesPattern": hourly_pattern,
            "revenueVsTransactions": revenue_vs_transactions,
        }

        serializer = TrendsAnalysisSerializer(trends_data)
        return Response(serializer.data)


class AccountantAnalyticsForecastsView(AccountantAnalyticsBaseView):
    """Get analytics forecasts"""

    @extend_schema(
        description="Get revenue forecasts for next 7 days",
        parameters=[{"name": "tenantId", "in": "query", "required": True, "schema": {"type": "string"}},
                    {"name": "days", "in": "query", "required": False, "schema": {"type": "integer", "default": 30}}],
        responses={200: ForecastsSerializer()},
    )
    def get(self, request):
        tenant, tenant_id, auth_error = self._authorize(request)
        days = int(request.query_params.get("days", 30))
        if auth_error:
            return auth_error

        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=days)
        currency = (tenant.currency if tenant else "USD")

        sales = Sale.objects.filter(
            tenant_id=tenant_id, status="COMPLETED", created_at__date__gte=start_date, created_at__date__lte=end_date
        )

        dashboard_view = AccountantAnalyticsDashboardView()
        forecasts = dashboard_view._get_forecasts(tenant_id, sales, days, currency)

        serializer = ForecastsSerializer(forecasts)
        return Response(serializer.data)


class AccountantAnalyticsInsightsView(AccountantAnalyticsBaseView):
    """Get business insights and recommendations"""

    @extend_schema(
        description="Get business insights and recommendations",
        parameters=[{"name": "tenantId", "in": "query", "required": True, "schema": {"type": "string"}},
                    {"name": "days", "in": "query", "required": False, "schema": {"type": "integer", "default": 30}}],
        responses={200: BusinessInsightsSerializer()},
    )
    def get(self, request):
        tenant, tenant_id, auth_error = self._authorize(request)
        days = int(request.query_params.get("days", 30))
        if auth_error:
            return auth_error

        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=days)

        sales = Sale.objects.filter(
            tenant_id=tenant_id, status="COMPLETED", created_at__date__gte=start_date, created_at__date__lte=end_date
        )
        revenue = sales.aggregate(Sum('paid_amount'))['paid_amount__sum'] or Decimal('0')
        unique_customers = sales.values('customer_phone').distinct().count()
        currency = (tenant.currency if tenant else "USD")

        dashboard_view = AccountantAnalyticsDashboardView()
        insights = dashboard_view._get_business_insights(tenant_id, sales, revenue, unique_customers, currency)

        serializer = BusinessInsightsSerializer(insights)
        return Response(serializer.data)


# Import SaleItem if not already imported
try:
    from api.models import SaleItem
except ImportError:
    pass
