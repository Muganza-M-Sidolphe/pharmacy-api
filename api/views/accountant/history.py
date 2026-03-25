from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from django.core.paginator import Paginator
from datetime import datetime
from decimal import Decimal

from ...models import UserTenant, Sale, Expense, Notification
from ...serializers import AccountantDashboardSummarySerializer, NotificationItemSerializer, ApprovedSalesListSerializer
from ...utils.subscription_access import authorize_tenant_access
from drf_spectacular.utils import extend_schema
from django.db.models import Q


class AccountantHistoryBaseView(APIView):
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


class AccountantDashboardSummaryView(AccountantHistoryBaseView):
    """Get accountant dashboard summary with totals and counts."""

    @extend_schema(
        description="Get dashboard summary: total sales, revenue, expenses, notifications",
        responses=AccountantDashboardSummarySerializer,
        tags=["accountant"]
    )
    def get(self, request):
        _, tenant_id, auth_error = self._authorize(request)
        if auth_error:
            return auth_error

        sales_qs = Sale.objects.filter(tenant_id=tenant_id, status__in=['APPROVED', 'COMPLETED'])
        expenses_qs = Expense.objects.filter(tenant_id=tenant_id)
        notifications_qs = Notification.objects.filter(tenant_id=tenant_id)

        total_sales = sales_qs.count()
        total_revenue = sum((s.total_amount for s in sales_qs), Decimal('0.00'))
        total_paid = sum((s.paid_amount for s in sales_qs), Decimal('0.00'))
        total_due = sum((s.due_amount for s in sales_qs), Decimal('0.00'))
        total_expenses = sum((e.amount for e in expenses_qs), Decimal('0.00'))
        expense_count = expenses_qs.count()
        notifications_count = notifications_qs.count()
        unread_count = notifications_qs.filter(is_read=False).count()

        return Response({
            'totalSales': total_sales,
            'totalRevenue': str(total_revenue),
            'totalPaid': str(total_paid),
            'totalDue': str(total_due),
            'totalExpenses': str(total_expenses),
            'expenseCount': expense_count,
            'notificationsCount': notifications_count,
            'unreadNotificationsCount': unread_count
        })


class AccountantRecentNotificationsView(AccountantHistoryBaseView):
    """Get recent notifications for accountant."""

    @extend_schema(
        description="Get recent notifications",
        tags=["accountant"]
    )
    def get(self, request):
        _, tenant_id, auth_error = self._authorize(request)
        if auth_error:
            return auth_error

        limit = int(request.query_params.get('limit', 10))
        notifications = Notification.objects.filter(tenant_id=tenant_id).order_by('-created_at')[:limit]

        data = []
        for n in notifications:
            data.append({
                'id': str(n.id),
                'title': n.title,
                'message': n.message,
                'isRead': n.is_read,
                'createdAt': n.created_at,
            })

        return Response({'results': data})


class AccountantApprovedSalesListView(AccountantHistoryBaseView):
    """List approved sales with payment status filters."""

    @extend_schema(
        description="List approved sales with filters (All/Partial/Paid)",
        tags=["accountant"]
    )
    def get(self, request):
        _, tenant_id, auth_error = self._authorize(request)
        if auth_error:
            return auth_error

        page = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', 10))
        payment_filter = request.query_params.get('paymentFilter', 'all')  # all, partial, paid

        qs = Sale.objects.filter(
            tenant_id=tenant_id,
            status__in=['APPROVED', 'COMPLETED']
        ).filter(
            Q(payment_option__in=['FULL', 'CREDIT']) |
            Q(
                payment_option='PARTIAL',
                owner_approval_status='APPROVED',
                pharmacist_approval_status='APPROVED',
            )
        ).order_by('-created_at')

        # Apply payment status filter
        if payment_filter == 'partial':
            qs = qs.filter(paid_amount__gt=0, due_amount__gt=0)
        elif payment_filter == 'paid':
            qs = qs.filter(due_amount=0)
        elif payment_filter == 'unpaid':
            qs = qs.filter(paid_amount=0)

        paginator = Paginator(qs, page_size)
        page_obj = paginator.get_page(page)

        items = []
        for s in page_obj:
            items.append({
                'id': str(s.id),
                'invoiceNumber': s.invoice_number,
                'customerName': s.customer_name,
                'customerPhone': s.customer_phone,
                'totalAmount': str(s.total_amount),
                'paidAmount': str(s.paid_amount),
                'dueAmount': str(s.due_amount),
                'paymentMethod': s.payment_method,
                'paymentOption': s.payment_option,
                'status': s.status,
                'createdAt': s.created_at,
            })

        return Response({
            'results': items,
            'pagination': {
                'page': page,
                'page_size': page_size,
                'total_pages': paginator.num_pages,
                'total_items': paginator.count
            }
        })


class AccountantMarkNotificationAsReadView(AccountantHistoryBaseView):
    """Mark a notification as read."""

    @extend_schema(
        description="Mark notification as read",
        tags=["accountant"]
    )
    def post(self, request, notification_id):
        _, tenant_id, auth_error = self._authorize(request)
        if auth_error:
            return auth_error

        try:
            notification = Notification.objects.get(id=notification_id, tenant_id=tenant_id)
            notification.is_read = True
            notification.save()
            return Response({
                'id': str(notification.id),
                'title': notification.title,
                'message': notification.message,
                'isRead': notification.is_read,
                'createdAt': notification.created_at,
            })
        except Notification.DoesNotExist:
            return Response({"detail": "Notification not found"}, status=status.HTTP_404_NOT_FOUND)


class AccountantMarkAllNotificationsAsReadView(AccountantHistoryBaseView):
    """Mark all notifications as read."""

    @extend_schema(
        description="Mark all notifications as read",
        tags=["accountant"],
        responses=None
    )
    def post(self, request):
        _, tenant_id, auth_error = self._authorize(request)
        if auth_error:
            return auth_error

        count = Notification.objects.filter(tenant_id=tenant_id, is_read=False).update(is_read=True)
        return Response({'marked_as_read': count})
