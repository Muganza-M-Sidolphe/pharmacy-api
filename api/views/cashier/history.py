from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from django.core.paginator import Paginator
from django.utils import timezone
from datetime import timedelta, datetime
from decimal import Decimal

from ...models import UserTenant, Sale, Notification
from ...serializers import SaleSerializer
from ...utils.subscription_access import authorize_tenant_access
from drf_spectacular.utils import extend_schema


class CashierHistoryBaseView(APIView):
    permission_classes = [IsAuthenticated]
    required_subscription_feature = "sales_management"

    def _authorize(self, request):
        tenant_id = request.query_params.get('tenantId')
        tenant, error_message, error_status = authorize_tenant_access(
            request,
            tenant_id,
            required_feature=self.required_subscription_feature,
        )
        if error_message:
            return None, tenant_id, Response({"detail": error_message}, status=error_status)
        return tenant, tenant_id, None


class CashierHistorySummaryView(CashierHistoryBaseView):
    """Get cashier history summary metrics."""

    @extend_schema(
        description="Get cashier history summary: total sales, total amount, pending amount, unread notifications",
        tags=["cashier"]
    )
    def get(self, request):
        """
        Returns history summary:
        - totalSales: count of all sales
        - totalAmount: sum of all completed sales amounts
        - pendingAmount: sum of due amounts (not yet paid)
        - unreadNotifications: count of unread notifications for user
        """
        _, tenant_id, auth_error = self._authorize(request)
        if auth_error:
            return auth_error

        # Get all sales for this cashier
        sales = Sale.objects.filter(tenant_id=tenant_id, cashier=request.user)
        
        total_sales = sales.count()
        total_amount = sum(s.total_amount for s in sales.filter(status__in=['APPROVED', 'COMPLETED']))
        pending_amount = sum(s.due_amount for s in sales.filter(status__in=['APPROVED', 'COMPLETED']))
        
        # Get unread notifications
        unread_notifications = Notification.objects.filter(
            tenant_id=tenant_id,
            recipient=request.user,
            is_read=False
        ).count()
        
        return Response({
            'totalSales': total_sales,
            'totalAmount': str(total_amount),
            'pendingAmount': str(pending_amount),
            'unreadNotifications': unread_notifications
        })


class CashierSalesHistoryView(CashierHistoryBaseView):
    """Get sales history with filtering and aggregations."""

    @extend_schema(
        description="Get sales history with date filtering, payment type filtering, and search",
        responses=SaleSerializer(many=True),
        tags=["cashier"]
    )
    def get(self, request):
        """
        List sales history for cashier with optional filtering.
        
        Query params:
        - tenantId (required)
        - startDate: filter from date (YYYY-MM-DD)
        - endDate: filter to date (YYYY-MM-DD)
        - paymentType: FULL|PARTIAL|CREDIT|ALL (default: ALL)
        - status: PENDING|APPROVED|COMPLETED|REJECTED|CANCELLED
        - search: search invoice number or customer name
        - page: page number (default 1)
        - page_size: items per page (default 10)
        """
        _, tenant_id, auth_error = self._authorize(request)
        if auth_error:
            return auth_error

        page = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', 10))
        
        # Start with all sales for this cashier
        qs = Sale.objects.filter(tenant_id=tenant_id, cashier=request.user).order_by('-created_at')
        
        # Date filtering
        start_date = request.query_params.get('startDate')
        end_date = request.query_params.get('endDate')
        
        if start_date:
            try:
                start = datetime.strptime(start_date, '%Y-%m-%d').date()
                qs = qs.filter(created_at__date__gte=start)
            except ValueError:
                pass
        
        if end_date:
            try:
                end = datetime.strptime(end_date, '%Y-%m-%d').date()
                qs = qs.filter(created_at__date__lte=end)
            except ValueError:
                pass
        
        # Payment type filtering
        payment_type = request.query_params.get('paymentType', 'ALL')
        if payment_type != 'ALL':
            qs = qs.filter(payment_option=payment_type)
        
        # Status filtering
        status_filter = request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter)
        
        # Search by invoice or customer
        search = request.query_params.get('search', '').strip()
        if search:
            qs = qs.filter(invoice_number__icontains=search) | qs.filter(customer_name__icontains=search)
        
        paginator = Paginator(qs, page_size)
        page_obj = paginator.get_page(page)
        
        data = SaleSerializer(page_obj, many=True).data
        return Response({
            'results': data,
            'pagination': {
                'page': page,
                'page_size': page_size,
                'total_pages': paginator.num_pages,
                'total_items': paginator.count
            }
        })


class CashierSalesChartDataView(CashierHistoryBaseView):
    """Get aggregated chart data for sales analytics."""

    @extend_schema(
        description="Get aggregated sales data for charts (by date, payment type, status)",
        tags=["cashier"]
    )
    def get(self, request):
        """
        Returns chart data aggregated by date range.
        
        Query params:
        - tenantId (required)
        - startDate: filter from date (YYYY-MM-DD)
        - endDate: filter to date (YYYY-MM-DD)
        - groupBy: DAY|WEEK|MONTH (default: DAY)
        
        Returns:
        - salesByDate: list of {date, count, totalAmount}
        - salesByPaymentType: {FULL, PARTIAL, CREDIT}
        - salesByStatus: {PENDING, APPROVED, COMPLETED, REJECTED, CANCELLED}
        - dailyTotals: sum totals for period
        """
        _, tenant_id, auth_error = self._authorize(request)
        if auth_error:
            return auth_error

        # Get date range
        start_date = request.query_params.get('startDate')
        end_date = request.query_params.get('endDate')
        group_by = request.query_params.get('groupBy', 'DAY')
        
        qs = Sale.objects.filter(tenant_id=tenant_id, cashier=request.user)
        
        if start_date:
            try:
                start = datetime.strptime(start_date, '%Y-%m-%d').date()
                qs = qs.filter(created_at__date__gte=start)
            except ValueError:
                pass
        
        if end_date:
            try:
                end = datetime.strptime(end_date, '%Y-%m-%d').date()
                qs = qs.filter(created_at__date__lte=end)
            except ValueError:
                pass
        
        # Aggregate by date
        sales_by_date = {}
        for sale in qs.order_by('created_at'):
            date_key = sale.created_at.strftime('%Y-%m-%d')
            if date_key not in sales_by_date:
                sales_by_date[date_key] = {'count': 0, 'totalAmount': Decimal('0')}
            sales_by_date[date_key]['count'] += 1
            sales_by_date[date_key]['totalAmount'] += sale.total_amount
        
        # Convert to list format
        sales_by_date_list = [
            {'date': date, **data} 
            for date, data in sorted(sales_by_date.items())
        ]
        
        # Sales by payment type
        sales_by_payment = {}
        for payment_type in ['FULL', 'PARTIAL', 'CREDIT']:
            sales_by_payment[payment_type] = qs.filter(payment_option=payment_type).count()
        
        # Sales by status
        sales_by_status = {}
        for sale_status in ['PENDING', 'APPROVED', 'COMPLETED', 'REJECTED', 'CANCELLED']:
            sales_by_status[sale_status] = qs.filter(status=sale_status).count()
        
        # Daily totals
        total_sales = qs.count()
        total_amount = sum(s.total_amount for s in qs)
        total_pending = sum(s.due_amount for s in qs.filter(status__in=['APPROVED', 'COMPLETED']))
        
        return Response({
            'salesByDate': sales_by_date_list,
            'salesByPaymentType': sales_by_payment,
            'salesByStatus': sales_by_status,
            'dailyTotals': {
                'totalSales': total_sales,
                'totalAmount': str(total_amount),
                'totalPending': str(total_pending)
            }
        })


class CashierCompletedSalesView(CashierHistoryBaseView):
    """Get completed sales (full or partial payments)."""

    @extend_schema(
        description="Get completed sales (APPROVED or COMPLETED status)",
        responses=SaleSerializer(many=True),
        tags=["cashier"]
    )
    def get(self, request):
        """
        List completed sales.
        
        Query params:
        - tenantId (required)
        - startDate: filter from date (YYYY-MM-DD)
        - endDate: filter to date (YYYY-MM-DD)
        - page: page number (default 1)
        - page_size: items per page (default 10)
        """
        _, tenant_id, auth_error = self._authorize(request)
        if auth_error:
            return auth_error

        page = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', 10))
        
        qs = Sale.objects.filter(
            tenant_id=tenant_id,
            cashier=request.user,
            status__in=['APPROVED', 'COMPLETED']
        ).order_by('-created_at')
        
        # Date filtering
        start_date = request.query_params.get('startDate')
        end_date = request.query_params.get('endDate')
        
        if start_date:
            try:
                start = datetime.strptime(start_date, '%Y-%m-%d').date()
                qs = qs.filter(created_at__date__gte=start)
            except ValueError:
                pass
        
        if end_date:
            try:
                end = datetime.strptime(end_date, '%Y-%m-%d').date()
                qs = qs.filter(created_at__date__lte=end)
            except ValueError:
                pass
        
        paginator = Paginator(qs, page_size)
        page_obj = paginator.get_page(page)
        
        data = SaleSerializer(page_obj, many=True).data
        return Response({
            'results': data,
            'pagination': {
                'page': page,
                'page_size': page_size,
                'total_pages': paginator.num_pages,
                'total_items': paginator.count
            }
        })


class CashierPartialPaymentSalesView(CashierHistoryBaseView):
    """Get sales with partial payments."""

    @extend_schema(
        description="Get sales with partial payments or credit",
        responses=SaleSerializer(many=True),
        tags=["cashier"]
    )
    def get(self, request):
        """
        List sales with partial payments or credit.
        
        Query params:
        - tenantId (required)
        - startDate: filter from date (YYYY-MM-DD)
        - endDate: filter to date (YYYY-MM-DD)
        - page: page number (default 1)
        - page_size: items per page (default 10)
        """
        _, tenant_id, auth_error = self._authorize(request)
        if auth_error:
            return auth_error

        page = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', 10))
        
        qs = Sale.objects.filter(
            tenant_id=tenant_id,
            cashier=request.user,
            payment_option__in=['PARTIAL', 'CREDIT']
        ).order_by('-created_at')
        
        # Date filtering
        start_date = request.query_params.get('startDate')
        end_date = request.query_params.get('endDate')
        
        if start_date:
            try:
                start = datetime.strptime(start_date, '%Y-%m-%d').date()
                qs = qs.filter(created_at__date__gte=start)
            except ValueError:
                pass
        
        if end_date:
            try:
                end = datetime.strptime(end_date, '%Y-%m-%d').date()
                qs = qs.filter(created_at__date__lte=end)
            except ValueError:
                pass
        
        paginator = Paginator(qs, page_size)
        page_obj = paginator.get_page(page)
        
        data = SaleSerializer(page_obj, many=True).data
        return Response({
            'results': data,
            'pagination': {
                'page': page,
                'page_size': page_size,
                'total_pages': paginator.num_pages,
                'total_items': paginator.count
            }
        })


class CashierStockRequestsView(CashierHistoryBaseView):
    """Get stock requests for cashier (placeholder for future requests model)."""

    @extend_schema(
        description="Get stock requests made by cashier",
        tags=["cashier"]
    )
    def get(self, request):
        """
        List stock requests.
        TODO: Implement once StockRequest model is available
        """
        _, tenant_id, auth_error = self._authorize(request)
        if auth_error:
            return auth_error

        page = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', 10))
        
        # Placeholder: no stock requests yet
        stock_requests = []
        
        paginator = Paginator(stock_requests, page_size)
        page_obj = paginator.get_page(page)
        
        return Response({
            'results': page_obj.object_list,
            'pagination': {
                'page': page,
                'page_size': page_size,
                'total_pages': paginator.num_pages,
                'total_items': paginator.count
            }
        })
