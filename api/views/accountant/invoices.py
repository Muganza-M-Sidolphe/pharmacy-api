from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from django.core.paginator import Paginator
from django.utils import timezone
from datetime import datetime
from decimal import Decimal

from ...models import Notification, Sale, Tenant, UserTenant
from ...serializers import SaleSerializer
from drf_spectacular.utils import extend_schema
from django.db.models import Q


class AccountantInvoicesListView(APIView):
    """List all approved invoices for accountant to manage payments."""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="List invoices approved by storekeeper with payment status filtering",
        responses=SaleSerializer(many=True),
        tags=["accountant"]
    )
    def get(self, request):
        """
        List invoices for accountant to process payments.
        
        Query params:
        - tenantId (required)
        - status: APPROVED|COMPLETED|CANCELLED (default: all approved)
        - paymentStatus: UNPAID|PARTIAL|PAID (custom filter)
        - startDate: filter from date (YYYY-MM-DD)
        - endDate: filter to date (YYYY-MM-DD)
        - search: search by invoice number or customer name
        - page: page number (default 1)
        - page_size: items per page (default 10)
        """
        tenant_id = request.query_params.get('tenantId')
        if not tenant_id:
            return Response({"detail": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        if not UserTenant.objects.filter(user=request.user, tenant_id=tenant_id).exists():
            return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        page = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', 10))
        
        # Start with invoices that have been approved by storekeeper.
        # For PARTIAL chain, accountant sees invoices only after owner + pharmacist approvals.
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
        
        # Status filtering
        status_filter = request.query_params.get('status')
        if status_filter and status_filter != 'all':
            qs = qs.filter(status=status_filter)
        
        # Payment status filtering (custom logic)
        payment_status = request.query_params.get('paymentStatus')
        if payment_status == 'UNPAID':
            qs = qs.filter(paid_amount=0)
        elif payment_status == 'PARTIAL':
            qs = qs.filter(paid_amount__gt=0, due_amount__gt=0)
        elif payment_status == 'PAID':
            qs = qs.filter(due_amount=0)
        
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
        
        # Search
        search = request.query_params.get('search', '').strip()
        if search:
            qs = qs.filter(invoice_number__icontains=search) | qs.filter(customer_name__icontains=search)
        
        paginator = Paginator(qs, page_size)
        page_obj = paginator.get_page(page)
        
        data = SaleSerializer(page_obj, many=True).data
        tenant = Tenant.objects.only("id", "currency").filter(id=tenant_id).first()
        return Response({
            'results': data,
            'currency': (tenant.currency if tenant else "USD"),
            'pagination': {
                'page': page,
                'page_size': page_size,
                'total_pages': paginator.num_pages,
                'total_items': paginator.count
            }
        })


class AccountantInvoiceDetailView(APIView):
    """Get detailed invoice with all items and payment history."""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Get invoice details with items",
        responses=SaleSerializer,
        tags=["accountant"]
    )
    def get(self, request, sale_id):
        """Get invoice details for payment processing."""
        tenant_id = request.query_params.get('tenantId')
        if not tenant_id:
            return Response({"detail": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        if not UserTenant.objects.filter(user=request.user, tenant_id=tenant_id).exists():
            return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        try:
            sale = Sale.objects.get(id=sale_id, tenant_id=tenant_id, status__in=['APPROVED', 'COMPLETED'])
            return Response(SaleSerializer(sale).data)
        except Sale.DoesNotExist:
            return Response({"detail": "Invoice not found"}, status=status.HTTP_404_NOT_FOUND)


class AccountantApproveInvoiceView(APIView):
    """Accountant approval stage for invoices."""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Stage 3 approval for PARTIAL chain. Requires OWNER + PHARMACIST approvals first; then notifies CASHIER and STORE_KEEPER for delivery.",
        request=None,
        tags=["accountant"]
    )
    def post(self, request, sale_id):
        """
        Accountant reviews invoice for payment handling.
        Keep invoice non-completed until it is fully paid.
        """
        tenant_id = request.query_params.get('tenantId')
        if not tenant_id:
            return Response({"detail": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        if not UserTenant.objects.filter(user=request.user, tenant_id=tenant_id).exists():
            return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        try:
            sale = Sale.objects.get(id=sale_id, tenant_id=tenant_id, status='APPROVED')
            if sale.payment_option == 'PARTIAL' and sale.due_amount > 0:
                if sale.owner_approval_status != 'APPROVED':
                    return Response(
                        {"detail": "Owner approval is required before accountant approval for partial invoices."},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                if sale.pharmacist_approval_status != 'APPROVED':
                    return Response(
                        {"detail": "Pharmacist approval is required before accountant approval for partial invoices."},
                        status=status.HTTP_400_BAD_REQUEST
                    )

            # Only fully paid invoices can be marked completed.
            if sale.due_amount <= 0:
                sale.status = 'COMPLETED'
            else:
                sale.status = 'APPROVED'
            sale.approved_at = timezone.now()
            sale.approved_by = request.user
            sale.save()

            # Notify delivery roles after accountant step in partial chain.
            if sale.payment_option == 'PARTIAL' and sale.due_amount > 0:
                user_tenants = UserTenant.objects.filter(
                    tenant_id=tenant_id,
                    role__in=['CASHIER', 'STORE_KEEPER']
                )
                for user_tenant in user_tenants:
                    Notification.objects.create(
                        tenant_id=tenant_id,
                        title="Invoice Ready for Delivery",
                        message=f"Partial invoice {sale.invoice_number} has passed approvals. Prepare and deliver products to customer.",
                        recipient_id=user_tenant.user_id
                    )
            else:
                Notification.objects.create(
                    tenant_id=tenant_id,
                    title="Invoice Approved by Accountant",
                    message=f"Invoice {sale.invoice_number} has been approved by accountant",
                    recipient=sale.cashier
                )

            return Response(SaleSerializer(sale).data, status=status.HTTP_200_OK)
        except Sale.DoesNotExist:
            return Response({"detail": "Invoice not found or not in APPROVED status"}, status=status.HTTP_404_NOT_FOUND)


class AccountantRecordPartialPaymentView(APIView):
    """Record partial payment for an invoice."""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Record a partial payment for an invoice",
        request={
            "type": "object",
            "properties": {
                "paidAmount": {"type": "number", "description": "Amount paid in this transaction"},
                "paymentMethod": {"type": "string", "enum": ["CASH", "CARD", "UPI", "MOBILE_MONEY", "BANK_TRANSFER"]},
                "notes": {"type": "string", "description": "Optional payment notes"}
            },
            "required": ["paidAmount", "paymentMethod"]
        },
        tags=["accountant"]
    )
    def post(self, request, sale_id):
        """
        Record a payment for an invoice.
        Invoice remains non-completed until due amount reaches zero.
        """
        tenant_id = request.query_params.get('tenantId')
        if not tenant_id:
            return Response({"detail": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        if not UserTenant.objects.filter(user=request.user, tenant_id=tenant_id).exists():
            return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        try:
            sale = Sale.objects.get(id=sale_id, tenant_id=tenant_id, status__in=['APPROVED', 'COMPLETED'])
            
            paid_amount = request.data.get('paidAmount')
            payment_method = request.data.get('paymentMethod')
            notes = request.data.get('notes', '')

            if not paid_amount or not payment_method:
                return Response(
                    {"detail": "paidAmount and paymentMethod are required"},
                    status=status.HTTP_400_BAD_REQUEST
                )

            try:
                paid_amount = Decimal(str(paid_amount))
            except:
                return Response(
                    {"detail": "Invalid paidAmount format"},
                    status=status.HTTP_400_BAD_REQUEST
                )

            if paid_amount <= 0:
                return Response(
                    {"detail": "paidAmount must be greater than 0"},
                    status=status.HTTP_400_BAD_REQUEST
                )

            if paid_amount > sale.due_amount:
                return Response(
                    {"detail": f"Payment amount ({paid_amount}) exceeds due amount ({sale.due_amount})"},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Update payment details
            new_paid_amount = sale.paid_amount + paid_amount
            new_due_amount = sale.total_amount - new_paid_amount

            sale.paid_amount = new_paid_amount
            sale.due_amount = new_due_amount
            sale.payment_method = payment_method
            
            # If fully paid, update payment_option
            if new_due_amount <= 0:
                sale.payment_option = 'FULL'
                sale.due_amount = Decimal('0.00')
                sale.status = 'COMPLETED'
            else:
                sale.payment_option = 'PARTIAL'
                sale.status = 'APPROVED'

            sale.save()

            # Notify cashier
            Notification.objects.create(
                tenant_id=tenant_id,
                title="Partial Payment Recorded",
                message=f"Partial payment of {paid_amount} recorded for invoice {sale.invoice_number}",
                recipient=sale.cashier
            )

            return Response(SaleSerializer(sale).data, status=status.HTTP_200_OK)
        except Sale.DoesNotExist:
            return Response({"detail": "Invoice not found"}, status=status.HTTP_404_NOT_FOUND)


class AccountantMarkFullyPaidView(APIView):
    """Mark invoice as fully paid."""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Mark invoice as fully paid",
        request={
            "type": "object",
            "properties": {
                "paymentMethod": {"type": "string", "enum": ["CASH", "CARD", "UPI", "MOBILE_MONEY", "BANK_TRANSFER"]},
                "notes": {"type": "string", "description": "Optional notes"}
            },
            "required": ["paymentMethod"]
        },
        tags=["accountant"]
    )
    def post(self, request, sale_id):
        """Mark invoice as fully paid."""
        tenant_id = request.query_params.get('tenantId')
        if not tenant_id:
            return Response({"detail": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        if not UserTenant.objects.filter(user=request.user, tenant_id=tenant_id).exists():
            return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        try:
            sale = Sale.objects.get(id=sale_id, tenant_id=tenant_id, status__in=['APPROVED', 'COMPLETED'])
            
            payment_method = request.data.get('paymentMethod')
            if not payment_method:
                return Response(
                    {"detail": "paymentMethod is required"},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Update payment details
            remaining_due = sale.due_amount
            sale.paid_amount = sale.paid_amount + remaining_due
            sale.due_amount = Decimal('0.00')
            sale.payment_option = 'FULL'
            sale.payment_method = payment_method
            sale.status = 'COMPLETED'
            sale.save()

            # Notify cashier
            Notification.objects.create(
                tenant_id=tenant_id,
                title="Invoice Fully Paid",
                message=f"Invoice {sale.invoice_number} has been marked as fully paid",
                recipient=sale.cashier
            )

            return Response(SaleSerializer(sale).data, status=status.HTTP_200_OK)
        except Sale.DoesNotExist:
            return Response({"detail": "Invoice not found"}, status=status.HTTP_404_NOT_FOUND)


class AccountantInvoicesSummaryView(APIView):
    """Get summary metrics for accountant dashboard."""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Get invoice summary: total, pending, partially paid, fully paid",
        tags=["accountant"]
    )
    def get(self, request):
        """
        Returns summary metrics:
        - totalInvoices: count of approved invoices
        - totalAmount: sum of all invoice amounts
        - paidAmount: total amount paid
        - pendingAmount: total outstanding/due
        - unpaidInvoices: count of invoices with zero payment
        - partialPaymentInvoices: count of partially paid invoices
        - fullyPaidInvoices: count of fully paid invoices
        """
        tenant_id = request.query_params.get('tenantId')
        if not tenant_id:
            return Response({"detail": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        if not UserTenant.objects.filter(user=request.user, tenant_id=tenant_id).exists():
            return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        invoices = Sale.objects.filter(
            tenant_id=tenant_id,
            status__in=['APPROVED', 'COMPLETED']
        )

        total_invoices = invoices.count()
        total_amount = sum(inv.total_amount for inv in invoices)
        paid_amount = sum(inv.paid_amount for inv in invoices)
        pending_amount = sum(inv.due_amount for inv in invoices)

        unpaid_invoices = invoices.filter(paid_amount=0).count()
        partial_payment_invoices = invoices.filter(
            paid_amount__gt=0,
            due_amount__gt=0
        ).count()
        fully_paid_invoices = invoices.filter(due_amount=0).count()
        tenant = Tenant.objects.only("id", "currency").filter(id=tenant_id).first()

        return Response({
            'totalInvoices': total_invoices,
            'totalAmount': str(total_amount),
            'paidAmount': str(paid_amount),
            'pendingAmount': str(pending_amount),
            'unpaidInvoices': unpaid_invoices,
            'partialPaymentInvoices': partial_payment_invoices,
            'fullyPaidInvoices': fully_paid_invoices,
            'currency': (tenant.currency if tenant else "USD"),
        })


class AccountantInvoicesReportView(APIView):
    """Generate invoice collection report for date range."""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Get invoice report with aggregated payment data",
        tags=["accountant"]
    )
    def get(self, request):
        """
        Generate report for invoices in date range.
        
        Query params:
        - tenantId (required)
        - startDate: filter from date (YYYY-MM-DD)
        - endDate: filter to date (YYYY-MM-DD)
        """
        tenant_id = request.query_params.get('tenantId')
        if not tenant_id:
            return Response({"detail": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        if not UserTenant.objects.filter(user=request.user, tenant_id=tenant_id).exists():
            return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        start_date = request.query_params.get('startDate')
        end_date = request.query_params.get('endDate')

        qs = Sale.objects.filter(
            tenant_id=tenant_id,
            status__in=['APPROVED', 'COMPLETED']
        )

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

        # Aggregate by payment status
        by_payment_method = {}
        for sale in qs:
            method = sale.payment_method
            if method not in by_payment_method:
                by_payment_method[method] = {'count': 0, 'amount': Decimal('0.00')}
            by_payment_method[method]['count'] += 1
            by_payment_method[method]['amount'] += sale.paid_amount

        total_collected = sum(s.paid_amount for s in qs)
        total_outstanding = sum(s.due_amount for s in qs)
        tenant = Tenant.objects.only("id", "currency").filter(id=tenant_id).first()

        return Response({
            'startDate': start_date or 'all',
            'endDate': end_date or 'all',
            'totalInvoices': qs.count(),
            'totalCollected': str(total_collected),
            'totalOutstanding': str(total_outstanding),
            'currency': (tenant.currency if tenant else "USD"),
            'byPaymentMethod': {
                method: {
                    'count': data['count'],
                    'amount': str(data['amount'])
                }
                for method, data in by_payment_method.items()
            }
        })
