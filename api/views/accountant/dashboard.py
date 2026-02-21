# Accountant Dashboard Views
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework import status
from datetime import datetime, timedelta, date
from decimal import Decimal
from django.db.models import Q, Sum, Count, F
from django.db.models.functions import TruncDate
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from api.models import (
    Sale,
    UserTenant,
    Notification,
    PartialPaymentReminderConfig,
    PartialPaymentReminderEvent,
)
from api.serializers import (
    AccountantDashboardPaymentsSerializer,
    PendingPartialPaymentsSerializer,
    OverduePaymentsSerializer,
    TotalPaidAmountSerializer,
    SelectedForInvoiceListSerializer,
    PartialPaymentRequestsSerializer,
    QuickStatsSerializer,
)
from api.utils.reminders import (
    send_partial_payment_email,
    send_partial_payment_sms,
)


class AccountantDashboardPaymentsView(APIView):
    """Get complete accountant dashboard with all payment-related metrics"""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Get accountant dashboard with pending payments, overdue payments, and payment requests",
        parameters=[{"name": "tenantId", "in": "query", "required": True, "schema": {"type": "string"}}],
        responses={200: AccountantDashboardPaymentsSerializer()},
    )
    def get(self, request):
        tenant_id = request.query_params.get("tenantId")

        if not tenant_id:
            return Response({"error": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        # Authorization check
        try:
            UserTenant.objects.get(user=request.user, tenant_id=tenant_id)
        except UserTenant.DoesNotExist:
            return Response({"error": "Not authorized for this tenant"}, status=status.HTTP_403_FORBIDDEN)

        pending_partial = self._get_pending_partial_payments(tenant_id)
        overdue = self._get_overdue_payments(tenant_id)
        total_paid = self._get_total_paid_amount(tenant_id)
        selected_for_invoice = self._get_selected_for_invoice(tenant_id)
        payment_requests = self._get_partial_payment_requests(tenant_id)
        quick_stats = self._get_quick_stats(tenant_id)

        dashboard_data = {
            "pendingPartialPayments": pending_partial,
            "overduePayments": overdue,
            "totalPaidAmount": total_paid,
            "selectedForInvoice": selected_for_invoice,
            "partialPaymentRequests": payment_requests,
            "quickStats": quick_stats,
        }

        serializer = AccountantDashboardPaymentsSerializer(dashboard_data)
        return Response(serializer.data)

    def _get_pending_partial_payments(self, tenant_id):
        """Get pending partial payments"""
        sales = Sale.objects.filter(
            tenant_id=tenant_id, status="APPROVED", payment_option="PARTIAL", due_amount__gt=0
        ).order_by('-created_at')[:10]

        total_due = sales.aggregate(Sum('due_amount'))['due_amount__sum'] or Decimal('0')

        items = []
        for sale in sales:
            items.append({
                'id': sale.id,
                'invoiceNumber': sale.invoice_number,
                'customerName': sale.customer_name,
                'customerPhone': sale.customer_phone,
                'totalAmount': sale.total_amount,
                'paidAmount': sale.paid_amount,
                'dueAmount': sale.due_amount,
                'createdAt': sale.created_at,
            })

        return {
            'count': len(items),
            'totalDue': total_due,
            'items': items,
        }

    def _get_overdue_payments(self, tenant_id):
        """Get overdue payments (more than 30 days past approved date)"""
        thirty_days_ago = datetime.now() - timedelta(days=30)
        sales = Sale.objects.filter(
            tenant_id=tenant_id, status="APPROVED", due_amount__gt=0, approved_at__lt=thirty_days_ago
        ).order_by('-approved_at')[:10]

        items = []
        now = datetime.now()
        for sale in sales:
            days_overdue = (now - sale.approved_at).days if sale.approved_at else 0
            items.append({
                'id': sale.id,
                'invoiceNumber': sale.invoice_number,
                'customerName': sale.customer_name,
                'totalAmount': sale.total_amount,
                'dueAmount': sale.due_amount,
                'daysOverdue': days_overdue,
                'createdAt': sale.created_at,
            })

        return {
            'count': len(items),
            'items': items,
        }

    def _get_total_paid_amount(self, tenant_id):
        """Get total paid amount from all approved sales"""
        today = date.today()
        period_start = today - timedelta(days=30)

        paid_sales = Sale.objects.filter(
            tenant_id=tenant_id, status="COMPLETED", created_at__date__gte=period_start
        )
        total_paid = paid_sales.aggregate(Sum('paid_amount'))['paid_amount__sum'] or Decimal('0')
        total_approved = paid_sales.count()

        return {
            'amount': total_paid,
            'totalApprovedSales': total_approved,
            'period': 'Last 30 days',
        }

    def _get_selected_for_invoice(self, tenant_id):
        """Get sales selected for invoicing (pending approval by accountant)"""
        sales = Sale.objects.filter(
            tenant_id=tenant_id, status="PENDING"
        ).order_by('-created_at')[:10]

        total_amount = sales.aggregate(Sum('total_amount'))['total_amount__sum'] or Decimal('0')

        items = []
        for sale in sales:
            items.append({
                'id': sale.id,
                'invoiceNumber': sale.invoice_number,
                'customerName': sale.customer_name,
                'totalAmount': sale.total_amount,
                'createdAt': sale.created_at,
            })

        return {
            'count': len(items),
            'totalAmount': total_amount,
            'items': items,
        }

    def _get_partial_payment_requests(self, tenant_id):
        """Get partial payment requests"""
        # Get all partial payment sales
        partial_sales = Sale.objects.filter(
            tenant_id=tenant_id, payment_option="PARTIAL", due_amount__gt=0
        )

        # Categorize by status
        pending_count = partial_sales.filter(status="PENDING").count()
        total_count = partial_sales.count()

        items = []
        for sale in partial_sales[:10]:
            items.append({
                'id': sale.id,
                'invoiceNumber': sale.invoice_number,
                'customerName': sale.customer_name,
                'requestedAmount': sale.due_amount,
                'status': sale.status,
                'createdAt': sale.created_at,
            })

        return {
            'count': total_count,
            'pending': pending_count,
            'items': items,
        }

    def _get_quick_stats(self, tenant_id):
        """Get quick statistics"""
        today = date.today()

        # Today's sales
        todays_sales = Sale.objects.filter(
            tenant_id=tenant_id, created_at__date=today
        ).count()

        # Pending invoices
        pending_invoices = Sale.objects.filter(
            tenant_id=tenant_id, status="PENDING"
        ).count()

        # Pending partial payments
        pending_partial = Sale.objects.filter(
            tenant_id=tenant_id, payment_option="PARTIAL", due_amount__gt=0, status__in=["PENDING", "APPROVED"]
        ).count()

        # Overdue payments
        thirty_days_ago = datetime.now() - timedelta(days=30)
        overdue_payments = Sale.objects.filter(
            tenant_id=tenant_id, status="APPROVED", due_amount__gt=0, approved_at__lt=thirty_days_ago
        ).count()

        # Total due
        total_due = Sale.objects.filter(
            tenant_id=tenant_id, status__in=["PENDING", "APPROVED"], due_amount__gt=0
        ).aggregate(Sum('due_amount'))['due_amount__sum'] or Decimal('0')

        # Total paid
        total_paid = Sale.objects.filter(
            tenant_id=tenant_id, status="COMPLETED"
        ).aggregate(Sum('paid_amount'))['paid_amount__sum'] or Decimal('0')

        return {
            'todaysSales': todays_sales,
            'pendingInvoices': pending_invoices,
            'pendingPartialPayments': pending_partial,
            'overduePayments': overdue_payments,
            'totalDue': total_due,
            'totalPaid': total_paid,
        }


class AccountantPendingPartialPaymentsView(APIView):
    """Get pending partial payments only"""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Get pending partial payments with total due",
        parameters=[{"name": "tenantId", "in": "query", "required": True, "schema": {"type": "string"}}],
        responses={200: PendingPartialPaymentsSerializer()},
    )
    def get(self, request):
        tenant_id = request.query_params.get("tenantId")

        if not tenant_id:
            return Response({"error": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            UserTenant.objects.get(user=request.user, tenant_id=tenant_id)
        except UserTenant.DoesNotExist:
            return Response({"error": "Not authorized for this tenant"}, status=status.HTTP_403_FORBIDDEN)

        dashboard_view = AccountantDashboardPaymentsView()
        data = dashboard_view._get_pending_partial_payments(tenant_id)

        serializer = PendingPartialPaymentsSerializer(data)
        return Response(serializer.data)


class AccountantOverduePaymentsView(APIView):
    """Get overdue payments only"""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Get overdue payments (>30 days past approval)",
        parameters=[{"name": "tenantId", "in": "query", "required": True, "schema": {"type": "string"}}],
        responses={200: OverduePaymentsSerializer()},
    )
    def get(self, request):
        tenant_id = request.query_params.get("tenantId")

        if not tenant_id:
            return Response({"error": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            UserTenant.objects.get(user=request.user, tenant_id=tenant_id)
        except UserTenant.DoesNotExist:
            return Response({"error": "Not authorized for this tenant"}, status=status.HTTP_403_FORBIDDEN)

        dashboard_view = AccountantDashboardPaymentsView()
        data = dashboard_view._get_overdue_payments(tenant_id)

        serializer = OverduePaymentsSerializer(data)
        return Response(serializer.data)


class AccountantTotalPaidAmountView(APIView):
    """Get total paid amount statistics"""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Get total paid amount from approved sales",
        parameters=[{"name": "tenantId", "in": "query", "required": True, "schema": {"type": "string"}}],
        responses={200: TotalPaidAmountSerializer()},
    )
    def get(self, request):
        tenant_id = request.query_params.get("tenantId")

        if not tenant_id:
            return Response({"error": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            UserTenant.objects.get(user=request.user, tenant_id=tenant_id)
        except UserTenant.DoesNotExist:
            return Response({"error": "Not authorized for this tenant"}, status=status.HTTP_403_FORBIDDEN)

        dashboard_view = AccountantDashboardPaymentsView()
        data = dashboard_view._get_total_paid_amount(tenant_id)

        serializer = TotalPaidAmountSerializer(data)
        return Response(serializer.data)


class AccountantSelectedForInvoiceView(APIView):
    """Get sales selected for invoicing"""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Get sales pending invoicing approval",
        parameters=[{"name": "tenantId", "in": "query", "required": True, "schema": {"type": "string"}}],
        responses={200: SelectedForInvoiceListSerializer()},
    )
    def get(self, request):
        tenant_id = request.query_params.get("tenantId")

        if not tenant_id:
            return Response({"error": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            UserTenant.objects.get(user=request.user, tenant_id=tenant_id)
        except UserTenant.DoesNotExist:
            return Response({"error": "Not authorized for this tenant"}, status=status.HTTP_403_FORBIDDEN)

        dashboard_view = AccountantDashboardPaymentsView()
        data = dashboard_view._get_selected_for_invoice(tenant_id)

        serializer = SelectedForInvoiceListSerializer(data)
        return Response(serializer.data)


class AccountantPartialPaymentRequestsView(APIView):
    """Get partial payment requests"""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Get partial payment requests with pending count",
        parameters=[{"name": "tenantId", "in": "query", "required": True, "schema": {"type": "string"}}],
        responses={200: PartialPaymentRequestsSerializer()},
    )
    def get(self, request):
        tenant_id = request.query_params.get("tenantId")

        if not tenant_id:
            return Response({"error": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            UserTenant.objects.get(user=request.user, tenant_id=tenant_id)
        except UserTenant.DoesNotExist:
            return Response({"error": "Not authorized for this tenant"}, status=status.HTTP_403_FORBIDDEN)

        dashboard_view = AccountantDashboardPaymentsView()
        data = dashboard_view._get_partial_payment_requests(tenant_id)

        serializer = PartialPaymentRequestsSerializer(data)
        return Response(serializer.data)


class AccountantQuickStatsView(APIView):
    """Get quick statistics dashboard"""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Get quick statistics: today's sales, pending invoices, overdue payments",
        parameters=[{"name": "tenantId", "in": "query", "required": True, "schema": {"type": "string"}}],
        responses={200: QuickStatsSerializer()},
    )
    def get(self, request):
        tenant_id = request.query_params.get("tenantId")

        if not tenant_id:
            return Response({"error": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            UserTenant.objects.get(user=request.user, tenant_id=tenant_id)
        except UserTenant.DoesNotExist:
            return Response({"error": "Not authorized for this tenant"}, status=status.HTTP_403_FORBIDDEN)

        dashboard_view = AccountantDashboardPaymentsView()
        data = dashboard_view._get_quick_stats(tenant_id)

        serializer = QuickStatsSerializer(data)
        return Response(serializer.data)


class PartialInvoiceReminderBaseView(APIView):
    permission_classes = [IsAuthenticated]
    ALLOWED_ROLES = ("OWNER", "ACCOUNTANT")

    def _authorize(self, request):
        tenant_id = request.query_params.get("tenantId")
        if not tenant_id:
            return None, Response({"error": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        has_access = UserTenant.objects.filter(
            user=request.user,
            tenant_id=tenant_id,
            role__in=self.ALLOWED_ROLES,
        ).exists()
        if not has_access:
            return None, Response({"error": "Not authorized for this tenant"}, status=status.HTTP_403_FORBIDDEN)
        return tenant_id, None

    def _get_partial_sale(self, tenant_id, sale_id):
        try:
            sale = Sale.objects.get(
                id=sale_id,
                tenant_id=tenant_id,
                payment_option="PARTIAL",
                due_amount__gt=0,
            )
            return sale, None
        except Sale.DoesNotExist:
            return None, Response(
                {"error": "Partial invoice not found or already settled"},
                status=status.HTTP_404_NOT_FOUND,
            )

    def _normalize_days(self, raw_days):
        if raw_days is None:
            return [7, 3, 1, 0]
        if not isinstance(raw_days, list):
            raise ValueError("reminderDaysBefore must be a list of integers")

        normalized = []
        for item in raw_days:
            day_value = int(item)
            if day_value < 0:
                raise ValueError("reminderDaysBefore values must be >= 0")
            normalized.append(day_value)

        return sorted(set(normalized), reverse=True)

    def _config_payload(self, config):
        return {
            "id": str(config.id),
            "saleId": str(config.sale_id),
            "invoiceNumber": config.sale.invoice_number,
            "dueDate": config.due_date,
            "reminderDaysBefore": config.reminder_days_before,
            "autoSendEnabled": config.auto_send_enabled,
            "channels": {
                "email": config.email_enabled,
                "sms": config.sms_enabled,
            },
            "customerEmail": config.customer_email,
            "customerPhone": config.customer_phone,
            "isActive": config.is_active,
            "updatedAt": config.updated_at,
        }


class PartialInvoiceReminderConfigView(PartialInvoiceReminderBaseView):
    @extend_schema(
        description="Configure due date + automatic reminder schedule for a partial invoice",
        parameters=[{"name": "tenantId", "in": "query", "required": True, "schema": {"type": "string"}}],
        request={
            "type": "object",
            "properties": {
                "dueDate": {"type": "string", "format": "date"},
                "daysToPay": {"type": "integer", "minimum": 1},
                "reminderDaysBefore": {"type": "array", "items": {"type": "integer", "minimum": 0}},
                "autoSendEnabled": {"type": "boolean"},
                "channels": {
                    "type": "object",
                    "properties": {
                        "email": {"type": "boolean"},
                        "sms": {"type": "boolean"},
                    },
                },
                "customerEmail": {"type": "string", "format": "email"},
                "customerPhone": {"type": "string"},
                "isActive": {"type": "boolean"},
            },
        },
        tags=["accountant", "owner"],
    )
    def post(self, request, sale_id):
        tenant_id, auth_error = self._authorize(request)
        if auth_error:
            return auth_error

        sale, sale_error = self._get_partial_sale(tenant_id, sale_id)
        if sale_error:
            return sale_error

        existing = PartialPaymentReminderConfig.objects.filter(sale=sale).first()
        due_date_str = request.data.get("dueDate")
        days_to_pay = request.data.get("daysToPay")

        if due_date_str:
            try:
                due_date = datetime.strptime(due_date_str, "%Y-%m-%d").date()
            except ValueError:
                return Response({"error": "dueDate must be YYYY-MM-DD"}, status=status.HTTP_400_BAD_REQUEST)
        elif days_to_pay is not None:
            try:
                days_to_pay = int(days_to_pay)
                if days_to_pay <= 0:
                    raise ValueError()
            except (TypeError, ValueError):
                return Response({"error": "daysToPay must be a positive integer"}, status=status.HTTP_400_BAD_REQUEST)
            due_date = timezone.now().date() + timedelta(days=days_to_pay)
        elif existing:
            due_date = existing.due_date
        else:
            return Response(
                {"error": "Provide either dueDate or daysToPay"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            reminder_days_before = self._normalize_days(request.data.get("reminderDaysBefore"))
        except (TypeError, ValueError) as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        channels = request.data.get("channels") or {}
        if channels and not isinstance(channels, dict):
            return Response({"error": "channels must be an object"}, status=status.HTTP_400_BAD_REQUEST)

        email_enabled = channels.get("email", request.data.get("emailEnabled", True))
        sms_enabled = channels.get("sms", request.data.get("smsEnabled", True))

        config, _ = PartialPaymentReminderConfig.objects.update_or_create(
            sale=sale,
            defaults={
                "tenant_id": tenant_id,
                "due_date": due_date,
                "reminder_days_before": reminder_days_before,
                "auto_send_enabled": bool(request.data.get("autoSendEnabled", True)),
                "email_enabled": bool(email_enabled),
                "sms_enabled": bool(sms_enabled),
                "customer_email": request.data.get("customerEmail"),
                "customer_phone": request.data.get("customerPhone") or sale.customer_phone,
                "is_active": bool(request.data.get("isActive", True)),
                "updated_by": request.user,
                "created_by": existing.created_by if existing else request.user,
            },
        )

        return Response(
            {
                "message": "Reminder configuration saved",
                "data": self._config_payload(config),
            },
            status=status.HTTP_200_OK,
        )


class PartialInvoiceReminderSendView(PartialInvoiceReminderBaseView):
    @extend_schema(
        description="Send partial invoice reminder now (manual email and/or SMS)",
        parameters=[{"name": "tenantId", "in": "query", "required": True, "schema": {"type": "string"}}],
        request={
            "type": "object",
            "properties": {
                "channels": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["EMAIL", "SMS"]},
                },
                "message": {"type": "string"},
                "customerEmail": {"type": "string", "format": "email"},
                "customerPhone": {"type": "string"},
            },
        },
        tags=["accountant", "owner"],
    )
    def post(self, request, sale_id):
        tenant_id, auth_error = self._authorize(request)
        if auth_error:
            return auth_error

        sale, sale_error = self._get_partial_sale(tenant_id, sale_id)
        if sale_error:
            return sale_error

        config = PartialPaymentReminderConfig.objects.filter(sale=sale).first()
        if not config:
            config = PartialPaymentReminderConfig.objects.create(
                tenant_id=tenant_id,
                sale=sale,
                due_date=timezone.now().date() + timedelta(days=7),
                reminder_days_before=[7, 3, 1, 0],
                auto_send_enabled=False,
                email_enabled=True,
                sms_enabled=True,
                customer_phone=sale.customer_phone,
                created_by=request.user,
                updated_by=request.user,
            )

        requested_channels = request.data.get("channels")
        if requested_channels is None:
            channels = []
            if config.email_enabled:
                channels.append("EMAIL")
            if config.sms_enabled:
                channels.append("SMS")
        elif isinstance(requested_channels, list):
            channels = [str(ch).upper() for ch in requested_channels]
        else:
            return Response({"error": "channels must be a list"}, status=status.HTTP_400_BAD_REQUEST)

        valid_channels = {"EMAIL", "SMS"}
        if not channels or any(ch not in valid_channels for ch in channels):
            return Response(
                {"error": "channels must contain EMAIL and/or SMS"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        custom_message = request.data.get("message", "")
        customer_email = request.data.get("customerEmail") or config.customer_email
        customer_phone = request.data.get("customerPhone") or config.customer_phone or sale.customer_phone

        results = []
        for channel in channels:
            if channel == "EMAIL":
                success, error_message = send_partial_payment_email(customer_email, sale, config.due_date, custom_message)
                recipient = customer_email
            else:
                success, error_message = send_partial_payment_sms(customer_phone, sale, config.due_date, custom_message)
                recipient = customer_phone

            status_value = "SENT" if success else "FAILED"
            event = PartialPaymentReminderEvent.objects.create(
                tenant_id=tenant_id,
                sale=sale,
                config=config,
                channel=channel,
                mode="MANUAL",
                status=status_value,
                scheduled_for=timezone.now().date(),
                recipient=recipient,
                message=custom_message,
                error_message=error_message,
                sent_by=request.user,
            )
            results.append(
                {
                    "eventId": str(event.id),
                    "channel": channel,
                    "status": status_value,
                    "recipient": recipient,
                    "error": error_message,
                }
            )

        return Response(
            {
                "message": "Manual reminder processing completed",
                "results": results,
            },
            status=status.HTTP_200_OK,
        )


class PartialInvoiceReminderHistoryView(PartialInvoiceReminderBaseView):
    @extend_schema(
        description="Get reminder history for a partial invoice",
        parameters=[{"name": "tenantId", "in": "query", "required": True, "schema": {"type": "string"}}],
        tags=["accountant", "owner"],
    )
    def get(self, request, sale_id):
        tenant_id, auth_error = self._authorize(request)
        if auth_error:
            return auth_error

        sale, sale_error = self._get_partial_sale(tenant_id, sale_id)
        if sale_error:
            return sale_error

        config = PartialPaymentReminderConfig.objects.filter(sale=sale).first()
        events = PartialPaymentReminderEvent.objects.filter(sale=sale).order_by("-sent_at")[:100]

        return Response(
            {
                "config": self._config_payload(config) if config else None,
                "events": [
                    {
                        "id": str(event.id),
                        "channel": event.channel,
                        "mode": event.mode,
                        "status": event.status,
                        "scheduledFor": event.scheduled_for,
                        "sentAt": event.sent_at,
                        "recipient": event.recipient,
                        "errorMessage": event.error_message,
                    }
                    for event in events
                ],
            },
            status=status.HTTP_200_OK,
        )
