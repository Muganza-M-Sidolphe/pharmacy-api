from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Sum, Value
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ...models import Expense, ExpenseCategory, Medicine, Sale, SaleItem, StockBatch, UserTenant


LOW_STOCK_THRESHOLD = 10


class RetailBaseView(APIView):
    permission_classes = [IsAuthenticated]

    def _get_tenant(self, request):
        tenant_id = request.query_params.get("tenantId") or request.data.get("tenantId")
        if not tenant_id:
            token = getattr(request, "auth", None)
            if token and hasattr(token, "get"):
                tenant_id = token.get("tenant_id")

        relation = None
        if tenant_id:
            relation = (
                UserTenant.objects.filter(user=request.user, tenant_id=tenant_id)
                .select_related("tenant")
                .first()
            )
        else:
            relation = UserTenant.objects.filter(user=request.user).select_related("tenant").first()

        if not relation:
            return None
        return relation.tenant


def _medicine_payload(medicine):
    batch = medicine.batches.order_by("-created_at").first()
    stock_qty = (
        medicine.batches.aggregate(total=Coalesce(Sum("quantity"), 0)).get("total")
        or 0
    )
    return {
        "id": str(medicine.id),
        "name": medicine.brand_name,
        "brand_name": medicine.brand_name,
        "generic_name": medicine.generic_name,
        "manufacturer": medicine.manufacturer,
        "category": medicine.category,
        "unit": medicine.unit,
        "description": medicine.description,
        "quantity": stock_qty,
        "has_stock": stock_qty > 0,
        "selling_price": float(batch.selling_price) if batch else 0.0,
        "RetailStocks": (
            [
                {
                    "id": str(batch.id),
                    "batch_number": batch.batch_number,
                    "quantity": batch.quantity,
                    "purchase_price": float(batch.purchase_price),
                    "selling_price": float(batch.selling_price),
                    "manufacture_date": batch.manufacture_date.isoformat() if batch.manufacture_date else None,
                    "expiry_date": batch.expiry_date.isoformat() if batch.expiry_date else None,
                }
            ]
            if batch
            else []
        ),
    }


def _sale_payload(sale):
    items = []
    for item in sale.items.select_related("medicine").all():
        items.append(
            {
                "id": str(item.id),
                "quantity": item.quantity,
                "subtotal": float(item.subtotal),
                "Medicine": {
                    "id": str(item.medicine.id),
                    "name": item.medicine.brand_name,
                },
            }
        )

    final_amount = sale.total_amount
    insurance_amount = Decimal("0.00")
    customer_amount = final_amount
    payment_status = "PAID" if sale.due_amount <= 0 else "UNPAID"
    if sale.payment_method == "CARD":
        ui_payment_method = "INSURANCE"
        insurance_amount = final_amount - sale.paid_amount if sale.paid_amount < final_amount else Decimal("0.00")
        customer_amount = sale.paid_amount
        if insurance_amount > 0:
            payment_status = "INSURANCE_PENDING"
    else:
        ui_payment_method = sale.payment_method

    return {
        "id": str(sale.id),
        "invoice_number": sale.invoice_number,
        "customer_name": sale.customer_name,
        "customer_phone": sale.customer_phone,
        "payment_method": ui_payment_method,
        "payment_status": payment_status,
        "status": sale.status,
        "discount": float(sale.discount_amount),
        "insurance_amount": float(insurance_amount),
        "customer_amount": float(customer_amount),
        "paid_amount": float(sale.paid_amount),
        "due_amount": float(sale.due_amount),
        "total_amount": float(sale.total_amount),
        "final_amount": float(final_amount),
        "createdAt": sale.created_at.isoformat(),
        "SaleItems": items,
    }


class RetailMedicinesView(RetailBaseView):
    def get(self, request):
        tenant = self._get_tenant(request)
        if not tenant:
            return Response({"success": False, "message": "No tenant context"}, status=403)

        medicines = Medicine.objects.filter(tenant=tenant).prefetch_related("batches").order_by("-created_at")
        data = [_medicine_payload(m) for m in medicines]
        return Response({"success": True, "data": data, "currency": tenant.currency})

    def post(self, request):
        tenant = self._get_tenant(request)
        if not tenant:
            return Response({"success": False, "message": "No tenant context"}, status=403)

        name = request.data.get("name")
        quantity = int(request.data.get("quantity", 0))
        purchase_price = Decimal(str(request.data.get("purchase_price", 0)))
        selling_price = Decimal(str(request.data.get("selling_price", 0)))
        expiry_date = request.data.get("expiry_date")
        batch_number = request.data.get("batch_number") or f"BATCH-{timezone.now().strftime('%Y%m%d%H%M%S')}"

        if not name:
            return Response({"success": False, "message": "name is required"}, status=400)
        if quantity <= 0:
            return Response({"success": False, "message": "quantity must be greater than 0"}, status=400)

        medicine = Medicine.objects.create(
            tenant=tenant,
            brand_name=name,
            generic_name=request.data.get("generic_name"),
            manufacturer=request.data.get("manufacturer"),
            category=request.data.get("category"),
            unit=request.data.get("unit"),
            description=request.data.get("description"),
        )

        StockBatch.objects.create(
            medicine=medicine,
            batch_number=batch_number,
            quantity=quantity,
            purchase_price=purchase_price,
            selling_price=selling_price,
            expiry_date=expiry_date or None,
        )
        return Response({"success": True, "data": _medicine_payload(medicine)}, status=201)


class RetailStockView(RetailBaseView):
    def post(self, request):
        tenant = self._get_tenant(request)
        if not tenant:
            return Response({"success": False, "message": "No tenant context"}, status=403)

        medicine_id = request.data.get("medicineId")
        quantity = int(request.data.get("quantity", 0))
        batch_number = request.data.get("batchNumber") or f"BATCH-{timezone.now().strftime('%Y%m%d%H%M%S')}"
        expiry_date = request.data.get("expiryDate")

        if not medicine_id or quantity <= 0:
            return Response({"success": False, "message": "medicineId and quantity are required"}, status=400)

        medicine = Medicine.objects.filter(id=medicine_id, tenant=tenant).first()
        if not medicine:
            return Response({"success": False, "message": "Medicine not found"}, status=404)

        latest = medicine.batches.order_by("-created_at").first()
        purchase_price = latest.purchase_price if latest else Decimal("0.00")
        selling_price = latest.selling_price if latest else Decimal("0.00")

        batch = StockBatch.objects.create(
            medicine=medicine,
            batch_number=batch_number,
            quantity=quantity,
            purchase_price=purchase_price,
            selling_price=selling_price,
            expiry_date=expiry_date or None,
        )
        return Response({"success": True, "data": {"id": str(batch.id)}}, status=201)


class RetailSalesView(RetailBaseView):
    def get(self, request):
        tenant = self._get_tenant(request)
        if not tenant:
            return Response({"success": False, "message": "No tenant context"}, status=403)

        sales = (
            Sale.objects.filter(tenant=tenant, cashier=request.user)
            .prefetch_related("items__medicine")
            .order_by("-created_at")
        )
        data = [_sale_payload(s) for s in sales]
        return Response({"success": True, "data": data, "currency": tenant.currency})

    def post(self, request):
        tenant = self._get_tenant(request)
        if not tenant:
            return Response({"success": False, "message": "No tenant context"}, status=403)

        invoice_number = request.data.get("invoice_number") or f"INV-{timezone.now().strftime('%Y%m%d%H%M%S')}"
        payment_method = request.data.get("payment_method", "CASH")
        items_data = request.data.get("items", [])
        discount = Decimal(str(request.data.get("discount", 0)))
        paid_amount = Decimal(str(request.data.get("paid_amount", 0)))

        if not items_data:
            return Response({"success": False, "message": "At least one item is required"}, status=400)

        sale = Sale.objects.create(
            tenant=tenant,
            cashier=request.user,
            invoice_number=invoice_number,
            customer_name=request.data.get("customer_name") or "Walk-in Customer",
            customer_phone=request.data.get("customer_phone"),
            notes=request.data.get("notes"),
            payment_option="FULL",
            payment_method="CARD" if payment_method == "INSURANCE" else payment_method,
            discount_amount=discount,
            paid_amount=paid_amount,
            status="COMPLETED",
        )

        subtotal = Decimal("0.00")
        for item in items_data:
            medicine_id = item.get("medicine_id")
            quantity = int(item.get("quantity", 0))
            if not medicine_id or quantity <= 0:
                sale.delete()
                return Response({"success": False, "message": "Invalid items payload"}, status=400)

            batch = (
                StockBatch.objects.filter(medicine_id=medicine_id, medicine__tenant=tenant, quantity__gt=0)
                .order_by("expiry_date", "created_at")
                .first()
            )
            if not batch or batch.quantity < quantity:
                sale.delete()
                return Response({"success": False, "message": "Insufficient stock for one or more items"}, status=400)

            item_subtotal = batch.selling_price * quantity
            SaleItem.objects.create(
                sale=sale,
                medicine=batch.medicine,
                batch=batch,
                quantity=quantity,
                unit_price=batch.selling_price,
                subtotal=item_subtotal,
            )
            batch.quantity -= quantity
            batch.save(update_fields=["quantity"])
            subtotal += item_subtotal

        total = max(Decimal("0.00"), subtotal - discount)
        due = max(Decimal("0.00"), total - paid_amount)
        sale.subtotal = subtotal
        sale.total_amount = total
        sale.due_amount = due
        sale.save(update_fields=["subtotal", "total_amount", "due_amount"])

        return Response({"success": True, "data": _sale_payload(sale)}, status=201)


class RetailInsuranceSalesView(RetailBaseView):
    def get(self, request):
        tenant = self._get_tenant(request)
        if not tenant:
            return Response({"success": False, "message": "No tenant context"}, status=403)

        sales = Sale.objects.filter(tenant=tenant, cashier=request.user, payment_method="CARD").prefetch_related("items__medicine")
        return Response({"success": True, "data": [_sale_payload(s) for s in sales], "currency": tenant.currency})


class RetailExpensesView(RetailBaseView):
    def get(self, request):
        tenant = self._get_tenant(request)
        if not tenant:
            return Response({"success": False, "message": "No tenant context"}, status=403)

        expenses = Expense.objects.filter(tenant=tenant).select_related("category").order_by("-expense_date")
        data = [
            {
                "id": str(e.id),
                "description": e.description,
                "category": e.category.name if e.category else None,
                "amount": float(e.amount),
                "expense_date": e.expense_date.isoformat(),
                "createdAt": e.created_at.isoformat(),
            }
            for e in expenses
        ]
        total = expenses.aggregate(total=Coalesce(Sum("amount"), Decimal("0.00"))).get("total") or Decimal("0.00")
        return Response(
            {
                "success": True,
                "data": {"expenses": data, "total": float(total)},
                "currency": tenant.currency,
            }
        )

    def post(self, request):
        tenant = self._get_tenant(request)
        if not tenant:
            return Response({"success": False, "message": "No tenant context"}, status=403)

        category_name = request.data.get("category") or "other"
        category, _ = ExpenseCategory.objects.get_or_create(tenant=tenant, name=category_name)
        expense = Expense.objects.create(
            tenant=tenant,
            category=category,
            amount=Decimal(str(request.data.get("amount", 0))),
            description=request.data.get("description"),
            expense_date=request.data.get("expense_date"),
            created_by=request.user,
        )
        return Response(
            {
                "success": True,
                "data": {
                    "id": str(expense.id),
                    "description": expense.description,
                    "category": category.name,
                    "amount": float(expense.amount),
                    "expense_date": expense.expense_date.isoformat(),
                },
            },
            status=201,
        )


class RetailExpenseDeleteView(RetailBaseView):
    def delete(self, request, expense_id):
        tenant = self._get_tenant(request)
        if not tenant:
            return Response({"success": False, "message": "No tenant context"}, status=403)

        expense = Expense.objects.filter(id=expense_id, tenant=tenant).first()
        if not expense:
            return Response({"success": False, "message": "Expense not found"}, status=404)
        expense.delete()
        return Response({"success": True})


class RetailExpiringMedicinesView(RetailBaseView):
    def get(self, request):
        tenant = self._get_tenant(request)
        if not tenant:
            return Response({"success": False, "message": "No tenant context"}, status=403)

        days = int(request.query_params.get("days", 30))
        today = timezone.now().date()
        cutoff = today + timedelta(days=days)
        batches = (
            StockBatch.objects.filter(
                medicine__tenant=tenant,
                quantity__gt=0,
                expiry_date__isnull=False,
                expiry_date__lte=cutoff,
            )
            .select_related("medicine")
            .order_by("expiry_date")
        )
        data = [
            {
                "id": str(b.id),
                "batch_number": b.batch_number,
                "quantity": b.quantity,
                "purchase_price": float(b.purchase_price),
                "selling_price": float(b.selling_price),
                "expiry_date": b.expiry_date.isoformat() if b.expiry_date else None,
                "Medicine": {
                    "id": str(b.medicine.id),
                    "name": b.medicine.brand_name,
                    "generic_name": b.medicine.generic_name,
                },
            }
            for b in batches
        ]
        return Response({"success": True, "data": data})


class RetailLowStockView(RetailBaseView):
    def get(self, request):
        tenant = self._get_tenant(request)
        if not tenant:
            return Response({"success": False, "message": "No tenant context"}, status=403)

        medicines = (
            Medicine.objects.filter(tenant=tenant)
            .annotate(total_qty=Coalesce(Sum("batches__quantity"), Value(0)))
            .filter(total_qty__lt=LOW_STOCK_THRESHOLD)
            .order_by("brand_name")
        )
        data = [
            {
                "id": str(m.id),
                "name": m.brand_name,
                "category": m.category,
                "quantity": int(m.total_qty),
            }
            for m in medicines
        ]
        return Response({"success": True, "data": data})


class RetailDashboardView(RetailBaseView):
    def get(self, request):
        tenant = self._get_tenant(request)
        if not tenant:
            return Response({"success": False, "message": "No tenant context"}, status=403)

        today = timezone.now().date()
        week_start = today - timedelta(days=6)
        month_start = today.replace(day=1)

        sales_today = Sale.objects.filter(tenant=tenant, created_at__date=today)
        sales_week = Sale.objects.filter(tenant=tenant, created_at__date__gte=week_start, created_at__date__lte=today)
        sales_month = Sale.objects.filter(tenant=tenant, created_at__date__gte=month_start, created_at__date__lte=today)

        def _metrics(qs):
            revenue = qs.aggregate(total=Coalesce(Sum("total_amount"), Decimal("0.00"))).get("total") or Decimal("0.00")
            return {
                "revenue": float(revenue),
                "profit": float(revenue),
                "sales_count": qs.count(),
            }

        stock_value = (
            StockBatch.objects.filter(medicine__tenant=tenant, quantity__gt=0)
            .aggregate(total=Coalesce(Sum("purchase_price"), Decimal("0.00")))
            .get("total")
            or Decimal("0.00")
        )

        expiring_count = StockBatch.objects.filter(
            medicine__tenant=tenant,
            quantity__gt=0,
            expiry_date__isnull=False,
            expiry_date__lte=today + timedelta(days=30),
        ).count()

        top_medicines_qs = (
            SaleItem.objects.filter(sale__tenant=tenant)
            .values("medicine__brand_name")
            .annotate(total_qty=Coalesce(Sum("quantity"), 0))
            .order_by("-total_qty")[:5]
        )
        top_medicines = [
            {"name": row["medicine__brand_name"], "quantity": row["total_qty"]}
            for row in top_medicines_qs
        ]

        cash_sales = (
            Sale.objects.filter(tenant=tenant, payment_method="CASH")
            .aggregate(total=Coalesce(Sum("total_amount"), Decimal("0.00")))
            .get("total")
            or Decimal("0.00")
        )
        insurance_sales = (
            Sale.objects.filter(tenant=tenant, payment_method="CARD")
            .aggregate(total=Coalesce(Sum("total_amount"), Decimal("0.00")))
            .get("total")
            or Decimal("0.00")
        )
        pending_insurance = (
            Sale.objects.filter(tenant=tenant, payment_method="CARD", due_amount__gt=0)
            .aggregate(total=Coalesce(Sum("due_amount"), Decimal("0.00")))
            .get("total")
            or Decimal("0.00")
        )

        daily_qs = (
            Sale.objects.filter(tenant=tenant, created_at__date__gte=week_start, created_at__date__lte=today)
            .annotate(day=TruncDate("created_at"))
            .values("day")
            .annotate(revenue=Coalesce(Sum("total_amount"), Decimal("0.00")), sales_count=Count("id"))
            .order_by("day")
        )
        daily_trend = [
            {"date": row["day"].isoformat(), "revenue": float(row["revenue"]), "sales_count": row["sales_count"]}
            for row in daily_qs
        ]

        return Response(
            {
                "success": True,
                "currency": tenant.currency,
                "data": {
                    "today": _metrics(sales_today),
                    "week": _metrics(sales_week),
                    "month": _metrics(sales_month),
                    "stock": {
                        "total_stock_value": float(stock_value),
                        "total_medicines": Medicine.objects.filter(tenant=tenant).count(),
                    },
                    "expiring": {"count": expiring_count},
                    "top_medicines": top_medicines,
                    "payment_methods": {
                        "cash_sales": float(cash_sales),
                        "insurance_sales": float(insurance_sales),
                        "pending_insurance": float(pending_insurance),
                    },
                    "daily_trend": daily_trend,
                },
            }
        )


class RetailReportsView(RetailBaseView):
    def get(self, request):
        tenant = self._get_tenant(request)
        if not tenant:
            return Response({"success": False, "message": "No tenant context"}, status=403)

        start_date = request.query_params.get("start_date")
        end_date = request.query_params.get("end_date")
        sales_qs = Sale.objects.filter(tenant=tenant).prefetch_related("items__medicine")
        expenses_qs = Expense.objects.filter(tenant=tenant).select_related("category")

        if start_date:
            sales_qs = sales_qs.filter(created_at__date__gte=start_date)
            expenses_qs = expenses_qs.filter(expense_date__gte=start_date)
        if end_date:
            sales_qs = sales_qs.filter(created_at__date__lte=end_date)
            expenses_qs = expenses_qs.filter(expense_date__lte=end_date)

        total_sales = sales_qs.aggregate(total=Coalesce(Sum("total_amount"), Decimal("0.00"))).get("total") or Decimal("0.00")
        total_expenses = expenses_qs.aggregate(total=Coalesce(Sum("amount"), Decimal("0.00"))).get("total") or Decimal("0.00")

        stock_qs = StockBatch.objects.filter(medicine__tenant=tenant, quantity__gt=0).select_related("medicine")
        expiring_qs = stock_qs.filter(expiry_date__isnull=False, expiry_date__lte=timezone.now().date() + timedelta(days=30))
        expired_qs = stock_qs.filter(expiry_date__isnull=False, expiry_date__lt=timezone.now().date())

        sales_data = [_sale_payload(s) for s in sales_qs.order_by("-created_at")]
        expenses_data = [
            {
                "id": str(e.id),
                "description": e.description,
                "category": e.category.name if e.category else None,
                "amount": float(e.amount),
                "expense_date": e.expense_date.isoformat(),
                "createdAt": e.created_at.isoformat(),
            }
            for e in expenses_qs.order_by("-expense_date")
        ]

        def _stock_payload(batch):
            return {
                "id": str(batch.id),
                "batch_number": batch.batch_number,
                "quantity": batch.quantity,
                "purchase_price": float(batch.purchase_price),
                "selling_price": float(batch.selling_price),
                "expiry_date": batch.expiry_date.isoformat() if batch.expiry_date else None,
                "Medicine": {
                    "id": str(batch.medicine.id),
                    "name": batch.medicine.brand_name,
                    "generic_name": batch.medicine.generic_name,
                },
            }

        data = {
            "sales": sales_data,
            "expenses": expenses_data,
            "stock": [_stock_payload(s) for s in stock_qs],
            "expiredStocks": [_stock_payload(s) for s in expired_qs],
            "expiringStocks": [_stock_payload(s) for s in expiring_qs],
            "summary": {
                "totalSales": float(total_sales),
                "totalExpenses": float(total_expenses),
                "netProfit": float(total_sales - total_expenses),
            },
            "dateRange": {"start_date": start_date, "end_date": end_date},
        }
        return Response({"success": True, "data": data, "currency": tenant.currency})
