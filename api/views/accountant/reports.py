from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db.models import DecimalField, ExpressionWrapper, F, Sum
from django.db.models.functions import Coalesce
from django.http import HttpResponse
from datetime import datetime
from decimal import Decimal
import csv

from ...models import Sale, Medicine, StockBatch, Expense, SaleItem
from ...serializers import FinancialReportSerializer, InventoryReportSerializer, SalesReportSerializer
from ...utils.reporting import display_label, money, percent, report_branding, report_period, report_theme
from ...utils.subscription_access import authorize_tenant_access
from drf_spectacular.utils import extend_schema


class AccountantReportsBaseView(APIView):
    permission_classes = [IsAuthenticated]
    required_subscription_feature = "advanced_reports"

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

    def _wholesale_sales_queryset(self, tenant_id):
        return Sale.objects.filter(
            tenant_id=tenant_id,
            status__in=['APPROVED', 'COMPLETED']
        ).exclude(cashier__department='RETAIL')


class AccountantFinancialReportView(AccountantReportsBaseView):
    """Generate financial report with summary and breakdown."""

    @extend_schema(
        description="Get financial report: revenue, net profit, profit margin, transactions, breakdown",
        responses=FinancialReportSerializer,
        tags=["accountant"]
    )
    def get(self, request):
        tenant, tenant_id, auth_error = self._authorize(request)
        if auth_error:
            return auth_error

        start_date = request.query_params.get('startDate')
        end_date = request.query_params.get('endDate')

        sales_qs = self._wholesale_sales_queryset(tenant_id)
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

        total_revenue = sum((s.paid_amount for s in sales_qs), Decimal('0.00'))
        total_discounts = sum((s.discount_amount for s in sales_qs), Decimal('0.00'))
        total_expenses = sum((e.amount for e in expenses_qs), Decimal('0.00'))
        net_profit = total_revenue - total_expenses
        profit_margin = float((net_profit / total_revenue * 100) if total_revenue else 0.0)
        transactions = sales_qs.count()

        sale_items_qs = SaleItem.objects.filter(sale__in=sales_qs).select_related("medicine", "batch")
        cost_of_goods = (
            sale_items_qs.aggregate(
                total=Coalesce(
                    Sum(
                        ExpressionWrapper(
                            F("batch__purchase_price") * F("quantity"),
                            output_field=DecimalField(max_digits=14, decimal_places=2),
                        )
                    ),
                    Decimal("0.00"),
                )
            ).get("total")
            or Decimal("0.00")
        )
        gross_profit = total_revenue - cost_of_goods
        inventory_value = (
            StockBatch.objects.filter(medicine__tenant_id=tenant_id, quantity__gt=0).aggregate(
                total=Coalesce(
                    Sum(
                        ExpressionWrapper(
                            F("purchase_price") * F("quantity"),
                            output_field=DecimalField(max_digits=14, decimal_places=2),
                        )
                    ),
                    Decimal("0.00"),
                )
            ).get("total")
            or Decimal("0.00")
        )
        expired_qs = StockBatch.objects.filter(
            medicine__tenant_id=tenant_id,
            quantity__gt=0,
            expiry_date__isnull=False,
            expiry_date__lt=datetime.now().date(),
        )
        expired_stock_loss = (
            expired_qs.aggregate(
                total=Coalesce(
                    Sum(
                        ExpressionWrapper(
                            F("purchase_price") * F("quantity"),
                            output_field=DecimalField(max_digits=14, decimal_places=2),
                        )
                    ),
                    Decimal("0.00"),
                )
            ).get("total")
            or Decimal("0.00")
        )

        revenue_rows = []
        for row in (
            sale_items_qs.values("medicine__category")
            .annotate(amount=Coalesce(Sum("subtotal"), Decimal("0.00")))
            .order_by("-amount")
        ):
            amount = row["amount"] or Decimal("0.00")
            revenue_rows.append({
                "category": display_label(row["medicine__category"]),
                "revenue": money(amount),
                "percentage": percent((amount / total_revenue * 100) if total_revenue else Decimal("0.00")),
            })
        revenue_rows.append({"category": "Total Revenue", "revenue": money(total_revenue), "percentage": 100.0 if total_revenue else 0.0})

        expense_rows = []
        for row in (
            expenses_qs.values("category__name")
            .annotate(amount=Coalesce(Sum("amount"), Decimal("0.00")))
            .order_by("-amount")
        ):
            amount = row["amount"] or Decimal("0.00")
            expense_rows.append({
                "type": display_label(row["category__name"], "Other Expenses"),
                "amount": money(amount),
                "percentage": percent((amount / total_expenses * 100) if total_expenses else Decimal("0.00")),
            })
        if cost_of_goods:
            expense_rows.insert(0, {
                "type": "Inventory Purchases (COGS)",
                "amount": money(cost_of_goods),
                "percentage": percent((cost_of_goods / total_revenue * 100) if total_revenue else Decimal("0.00")),
            })
        expense_rows.append({"type": "Total Expenses", "amount": money(total_expenses), "percentage": 100.0 if total_expenses else 0.0})

        top_medicines_rows = []
        for row in (
            sale_items_qs.values("medicine__brand_name")
            .annotate(qty_sold=Coalesce(Sum("quantity"), 0), revenue=Coalesce(Sum("subtotal"), Decimal("0.00")))
            .order_by("-revenue")[:10]
        ):
            top_medicines_rows.append({
                "medicine": row["medicine__brand_name"] or "Unknown Medicine",
                "qtySold": int(row["qty_sold"] or 0),
                "revenue": money(row["revenue"] or Decimal("0.00")),
            })

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
            'breakdown': breakdown,
            'printReport': {
                "title": "Financial Performance Report",
                "period": report_period(start_date, end_date),
                "reportNumber": f"FR-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
                "generatedAt": datetime.now().isoformat(),
                "generatedBy": request.user.name or request.user.email,
                "page": {"current": 1, "total": 1},
                "branding": report_branding(tenant),
                "theme": report_theme(),
                "summary": {
                    "totalRevenue": money(total_revenue),
                    "totalExpenses": money(total_expenses),
                    "grossProfit": money(gross_profit),
                    "netProfit": money(net_profit),
                    "profitMargin": round(profit_margin, 2),
                    "transactions": transactions,
                    "inventoryValue": money(inventory_value),
                    "expiredStockLoss": money(expired_stock_loss),
                    "currency": tenant.currency,
                },
                "sections": [
                    {
                        "title": "Revenue Breakdown (by Category)",
                        "columns": ["Category", f"Revenue ({tenant.currency})", "% of Revenue"],
                        "rows": revenue_rows,
                    },
                    {
                        "title": "Expense Breakdown (by Type)",
                        "columns": ["Expense Type", f"Amount ({tenant.currency})", "% of Expenses"],
                        "rows": expense_rows,
                    },
                    {
                        "title": "Profit Analysis",
                        "columns": ["Description", f"Amount ({tenant.currency})"],
                        "rows": [
                            {"description": "Total Revenue", "amount": money(total_revenue)},
                            {"description": "Cost of Goods Sold (Inventory Purchases)", "amount": money(cost_of_goods)},
                            {"description": "Gross Profit", "amount": money(gross_profit)},
                            {"description": "Operating Expenses", "amount": money(total_expenses)},
                            {"description": "Net Profit", "amount": money(net_profit)},
                        ],
                    },
                    {
                        "title": "Top Selling Medicines",
                        "columns": ["Medicine", "Qty Sold", f"Revenue ({tenant.currency})"],
                        "rows": top_medicines_rows,
                    },
                    {
                        "title": "Stock Loss Report",
                        "columns": ["Reason", f"Value ({tenant.currency})", "Count"],
                        "rows": [
                            {"reason": "Expired Medicines", "value": money(expired_stock_loss), "count": expired_qs.count()},
                            {"reason": "Damaged Stock", "value": money(Decimal("0.00")), "count": 0},
                            {"reason": "Missing Stock", "value": money(Decimal("0.00")), "count": 0},
                            {"reason": "Total Loss", "value": money(expired_stock_loss), "count": expired_qs.count()},
                        ],
                    },
                ],
            },
        })


class AccountantInventoryReportView(AccountantReportsBaseView):
    """Generate inventory report with stock details."""

    @extend_schema(
        description="Get inventory report: medicines, quantities, values, expiring items",
        responses=InventoryReportSerializer,
        tags=["accountant"]
    )
    def get(self, request):
        _, tenant_id, auth_error = self._authorize(request)
        if auth_error:
            return auth_error

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


class AccountantSalesReportView(AccountantReportsBaseView):
    """Generate sales report with details."""

    @extend_schema(
        description="Get sales report with line items",
        responses=SalesReportSerializer,
        tags=["accountant"]
    )
    def get(self, request):
        _, tenant_id, auth_error = self._authorize(request)
        if auth_error:
            return auth_error

        start_date = request.query_params.get('startDate')
        end_date = request.query_params.get('endDate')

        qs = self._wholesale_sales_queryset(tenant_id).order_by('-created_at')

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

        total_revenue = sum((s.paid_amount for s in qs), Decimal('0.00'))
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


class AccountantExportReportView(AccountantReportsBaseView):
    """Export financial report as CSV."""

    @extend_schema(
        description="Export financial report to CSV",
        tags=["accountant"],
        responses=None
    )
    def get(self, request):
        _, tenant_id, auth_error = self._authorize(request)
        if auth_error:
            return auth_error

        start_date = request.query_params.get('startDate')
        end_date = request.query_params.get('endDate')
        report_type = request.query_params.get('type', 'financial')

        response = HttpResponse(content_type='text/csv')
        filename = f"{report_type}_report_{tenant_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}.csv"
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        response['Access-Control-Expose-Headers'] = 'Content-Disposition'
        writer = csv.writer(response)

        if report_type == 'financial':
            sales_qs = self._wholesale_sales_queryset(tenant_id)
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

            total_revenue = sum((s.paid_amount for s in sales_qs), Decimal('0.00'))
            total_expenses = sum((e.amount for e in expenses_qs), Decimal('0.00'))
            net_profit = total_revenue - total_expenses
            profit_margin = float((net_profit / total_revenue * 100) if total_revenue else 0.0)

            writer.writerow(['Metric', 'Amount', 'Percentage'])
            writer.writerow(['Total Revenue', str(total_revenue), '100.00%'])
            writer.writerow(['Total Expenses', str(total_expenses), f"{float((total_expenses / total_revenue * 100) if total_revenue else 0.0):.2f}%"])
            writer.writerow(['Net Profit', str(net_profit), f"{profit_margin:.2f}%"])

        elif report_type == 'sales':
            qs = self._wholesale_sales_queryset(tenant_id).order_by('-created_at')
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
