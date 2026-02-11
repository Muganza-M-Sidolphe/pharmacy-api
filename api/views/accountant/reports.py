from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from django.http import HttpResponse
from datetime import datetime
from decimal import Decimal
import csv

from ...models import UserTenant, Sale, Medicine, StockBatch, Expense
from ...serializers import FinancialReportSerializer, InventoryReportSerializer, SalesReportSerializer
from drf_spectacular.utils import extend_schema


class AccountantFinancialReportView(APIView):
    """Generate financial report with summary and breakdown."""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Get financial report: revenue, net profit, profit margin, transactions, breakdown",
        responses=FinancialReportSerializer,
        tags=["accountant"]
    )
    def get(self, request):
        tenant_id = request.query_params.get('tenantId')
        if not tenant_id:
            return Response({"detail": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        if not UserTenant.objects.filter(user=request.user, tenant_id=tenant_id).exists():
            return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        start_date = request.query_params.get('startDate')
        end_date = request.query_params.get('endDate')

        sales_qs = Sale.objects.filter(tenant_id=tenant_id, status__in=['APPROVED', 'COMPLETED'])
        expenses_qs = Expense.objects.filter(tenant_id=tenant_id)

        if start_date:
            try:
                s = datetime.strptime(start_date, '%Y-%m-%d').date()
                sales_qs = sales_qs.filter(created_at__date__gte=s)
                expenses_qs = expenses_qs.filter(expense_date__gte=s)
            except ValueError:
                pass
        if end_date:
            try:
                e = datetime.strptime(end_date, '%Y-%m-%d').date()
                sales_qs = sales_qs.filter(created_at__date__lte=e)
                expenses_qs = expenses_qs.filter(expense_date__lte=e)
            except ValueError:
                pass

        total_revenue = sum((s.total_amount for s in sales_qs), Decimal('0.00'))
        total_discounts = sum((s.discount_amount for s in sales_qs), Decimal('0.00'))
        total_expenses = sum((e.amount for e in expenses_qs), Decimal('0.00'))
        net_profit = total_revenue - total_expenses
        profit_margin = float((net_profit / total_revenue * 100) if total_revenue else 0.0)
        transactions = sales_qs.count()

        breakdown = [
            {"metric": "Total Revenue", "amount": str(total_revenue), "percentageOfRevenue": 100.0},
            {"metric": "Total Discounts", "amount": str(-total_discounts), "percentageOfRevenue": float((-total_discounts / total_revenue * 100) if total_revenue else 0.0)},
            {"metric": "Total Payments (COGS)", "amount": str(-total_expenses), "percentageOfRevenue": float((-total_expenses / total_revenue * 100) if total_revenue else 0.0)},
            {"metric": "Net Profit", "amount": str(net_profit), "percentageOfRevenue": profit_margin},
        ]

        return Response({
            'totalRevenue': str(total_revenue),
            'netProfit': str(net_profit),
            'profitMargin': round(profit_margin, 2),
            'transactions': transactions,
            'breakdown': breakdown
        })


class AccountantInventoryReportView(APIView):
    """Generate inventory report with stock details."""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Get inventory report: medicines, quantities, values, expiring items",
        responses=InventoryReportSerializer,
        tags=["accountant"]
    )
    def get(self, request):
        tenant_id = request.query_params.get('tenantId')
        if not tenant_id:
            return Response({"detail": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        if not UserTenant.objects.filter(user=request.user, tenant_id=tenant_id).exists():
            return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        medicines = Medicine.objects.filter(tenant_id=tenant_id)
        items = []
        total_value = Decimal('0.00')
        total_units = 0
        expiring_count = 0

        from django.utils import timezone
        from datetime import timedelta
        expiring_soon = timezone.now().date() + timedelta(days=30)

        for medicine in medicines:
            batches = medicine.batches.all()
            total_qty = sum(b.quantity for b in batches)
            total_batch_value = sum((b.selling_price * b.quantity for b in batches), Decimal('0.00'))
            expiring_batches = sum(1 for b in batches if b.expiry_date and b.expiry_date <= expiring_soon)

            if total_qty > 0 or batches.exists():
                items.append({
                    'medicineId': str(medicine.id),
                    'brandName': medicine.brand_name,
                    'totalBatches': batches.count(),
                    'totalQuantity': total_qty,
                    'totalValue': str(total_batch_value),
                    'expiringBatches': expiring_batches
                })
                total_value += total_batch_value
                total_units += total_qty
                expiring_count += expiring_batches

        return Response({
            'totalMedicines': len(items),
            'totalInventoryValue': str(total_value),
            'totalUnits': total_units,
            'expiringCount': expiring_count,
            'items': items
        })


class AccountantSalesReportView(APIView):
    """Generate sales report with details."""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Get sales report with line items",
        responses=SalesReportSerializer,
        tags=["accountant"]
    )
    def get(self, request):
        tenant_id = request.query_params.get('tenantId')
        if not tenant_id:
            return Response({"detail": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        if not UserTenant.objects.filter(user=request.user, tenant_id=tenant_id).exists():
            return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        start_date = request.query_params.get('startDate')
        end_date = request.query_params.get('endDate')

        qs = Sale.objects.filter(tenant_id=tenant_id, status__in=['APPROVED', 'COMPLETED']).order_by('-created_at')

        if start_date:
            try:
                s = datetime.strptime(start_date, '%Y-%m-%d').date()
                qs = qs.filter(created_at__date__gte=s)
            except ValueError:
                pass
        if end_date:
            try:
                e = datetime.strptime(end_date, '%Y-%m-%d').date()
                qs = qs.filter(created_at__date__lte=e)
            except ValueError:
                pass

        total_revenue = sum((s.total_amount for s in qs), Decimal('0.00'))
        total_discount = sum((s.discount_amount for s in qs), Decimal('0.00'))
        total_paid = sum((s.paid_amount for s in qs), Decimal('0.00'))
        total_due = sum((s.due_amount for s in qs), Decimal('0.00'))

        items = []
        for s in qs:
            items.append({
                'invoiceNumber': s.invoice_number,
                'customerName': s.customer_name,
                'totalAmount': str(s.total_amount),
                'paidAmount': str(s.paid_amount),
                'dueAmount': str(s.due_amount),
                'paymentMethod': s.payment_method,
                'status': s.status,
                'createdAt': s.created_at,
            })

        return Response({
            'totalSales': qs.count(),
            'totalRevenue': str(total_revenue),
            'totalDiscount': str(total_discount),
            'totalPaid': str(total_paid),
            'totalDue': str(total_due),
            'items': items
        })


class AccountantExportReportView(APIView):
    """Export financial report as CSV."""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Export financial report to CSV",
        tags=["accountant"],
        responses=None
    )
    def get(self, request):
        tenant_id = request.query_params.get('tenantId')
        if not tenant_id:
            return Response({"detail": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        if not UserTenant.objects.filter(user=request.user, tenant_id=tenant_id).exists():
            return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        start_date = request.query_params.get('startDate')
        end_date = request.query_params.get('endDate')
        report_type = request.query_params.get('type', 'financial')

        response = HttpResponse(content_type='text/csv')
        filename = f"{report_type}_report_{tenant_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}.csv"
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        writer = csv.writer(response)

        if report_type == 'financial':
            sales_qs = Sale.objects.filter(tenant_id=tenant_id, status__in=['APPROVED', 'COMPLETED'])
            expenses_qs = Expense.objects.filter(tenant_id=tenant_id)

            if start_date:
                try:
                    s = datetime.strptime(start_date, '%Y-%m-%d').date()
                    sales_qs = sales_qs.filter(created_at__date__gte=s)
                    expenses_qs = expenses_qs.filter(expense_date__gte=s)
                except ValueError:
                    pass
            if end_date:
                try:
                    e = datetime.strptime(end_date, '%Y-%m-%d').date()
                    sales_qs = sales_qs.filter(created_at__date__lte=e)
                    expenses_qs = expenses_qs.filter(expense_date__lte=e)
                except ValueError:
                    pass

            total_revenue = sum((s.total_amount for s in sales_qs), Decimal('0.00'))
            total_expenses = sum((e.amount for e in expenses_qs), Decimal('0.00'))
            net_profit = total_revenue - total_expenses
            profit_margin = float((net_profit / total_revenue * 100) if total_revenue else 0.0)

            writer.writerow(['Metric', 'Amount', 'Percentage'])
            writer.writerow(['Total Revenue', str(total_revenue), '100.00%'])
            writer.writerow(['Total Expenses', str(total_expenses), f"{float((total_expenses / total_revenue * 100) if total_revenue else 0.0):.2f}%"])
            writer.writerow(['Net Profit', str(net_profit), f"{profit_margin:.2f}%"])

        elif report_type == 'sales':
            qs = Sale.objects.filter(tenant_id=tenant_id, status__in=['APPROVED', 'COMPLETED']).order_by('-created_at')
            if start_date:
                try:
                    s = datetime.strptime(start_date, '%Y-%m-%d').date()
                    qs = qs.filter(created_at__date__gte=s)
                except ValueError:
                    pass
            if end_date:
                try:
                    e = datetime.strptime(end_date, '%Y-%m-%d').date()
                    qs = qs.filter(created_at__date__lte=e)
                except ValueError:
                    pass

            writer.writerow(['InvoiceNumber', 'CustomerName', 'TotalAmount', 'PaidAmount', 'DueAmount', 'PaymentMethod', 'Status', 'CreatedAt'])
            for s in qs:
                writer.writerow([s.invoice_number, s.customer_name or '', str(s.total_amount), str(s.paid_amount), str(s.due_amount), s.payment_method, s.status, s.created_at.isoformat()])

        elif report_type == 'inventory':
            medicines = Medicine.objects.filter(tenant_id=tenant_id)
            writer.writerow(['BrandName', 'TotalBatches', 'TotalQuantity', 'TotalValue'])
            for medicine in medicines:
                batches = medicine.batches.all()
                total_qty = sum(b.quantity for b in batches)
                total_value = sum((b.selling_price * b.quantity for b in batches), Decimal('0.00'))
                if total_qty > 0 or batches.exists():
                    writer.writerow([medicine.brand_name, batches.count(), total_qty, str(total_value)])

        return response
