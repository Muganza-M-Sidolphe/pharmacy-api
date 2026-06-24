from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, DecimalField, ExpressionWrapper, F, Prefetch, Q, Sum, Value
from django.db.models.functions import Coalesce, TruncDate
from django.http import HttpResponse
from django.utils.dateparse import parse_date
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.renderers import BaseRenderer, JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from ...models import Expense, ExpenseCategory, Medicine, Sale, SaleItem, StockBatch, UserTenant
from ...utils.report_pdf import render_report_pdf
from ...utils.reporting import (
    MONEY_ZERO,
    display_label,
    money,
    percent,
    report_branding,
    report_period,
    report_theme,
)
from ...utils.subscription_access import check_subscription_access


LOW_STOCK_THRESHOLD = 10


class PDFRenderer(BaseRenderer):
    media_type = "application/pdf"
    format = "pdf"
    charset = None

    def render(self, data, accepted_media_type=None, renderer_context=None):
        if data is None:
            return b""
        if isinstance(data, bytes):
            return data
        return JSONRenderer().render(data, accepted_media_type, renderer_context)


def _first_present(data, *fields, default=None):
    for field in fields:
        if field in data:
            return data.get(field)
    return default


class RetailBaseView(APIView):
    permission_classes = [IsAuthenticated]
    required_subscription_feature = None
    allowed_subscription_business_types = None

    def _get_relation(self, request):
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

        return relation

    def _get_tenant(self, request):
        relation = self._get_relation(request)
        if not relation:
            return None
        return relation.tenant

    def _tenant_business_type(self, tenant):
        roles = UserTenant.objects.filter(tenant=tenant).values_list("role", flat=True)
        if "OWNER" in roles:
            return "WHOLESALE"
        if "PHARMACIST" in roles:
            return "RETAIL"
        return None

    def _is_collaborative_retail(self, request, tenant=None):
        if request.user.department != "RETAIL":
            return False
        target_tenant = tenant or self._get_tenant(request)
        if not target_tenant:
            return False
        return self._tenant_business_type(target_tenant) == "WHOLESALE"

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        tenant = self._get_tenant(request)
        if not tenant:
            return
        allowed, details = check_subscription_access(
            tenant,
            required_feature=self.required_subscription_feature,
            allowed_business_types=self.allowed_subscription_business_types,
        )
        if not allowed:
            self.permission_denied(request, message=details)


def _medicine_payload(medicine):
    batch = medicine.batches.order_by("-created_at").first()
    stock_qty = (
        medicine.batches.aggregate(total=Coalesce(Sum("quantity"), 0)).get("total")
        or 0
    )
    supplier_name = batch.supplier_name if batch else None
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
                    "supplier_name": batch.supplier_name,
                    "supplierName": batch.supplier_name,
                    "wholesale_name": batch.supplier_name,
                    "wholesaleName": batch.supplier_name,
                    "supplier_phone": batch.supplier_phone,
                    "supplier_address": batch.supplier_address,
                }
            ]
            if batch
            else []
        ),
        "supplier_name": supplier_name,
        "supplierName": supplier_name,
        "wholesale_name": supplier_name,
        "wholesaleName": supplier_name,
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


def _expense_payload(expense):
    return {
        "id": str(expense.id),
        "description": expense.description,
        "category": expense.category.name if expense.category else None,
        "amount": float(expense.amount),
        "expense_date": expense.expense_date.isoformat() if expense.expense_date else None,
        "createdAt": expense.created_at.isoformat(),
    }


class RetailMedicinesView(RetailBaseView):
    required_subscription_feature = "inventory_management"

    def get(self, request, medicine_id=None):
        tenant = self._get_tenant(request)
        if not tenant:
            return Response({"success": False, "message": "No tenant context"}, status=403)

        is_collaborative_retail = self._is_collaborative_retail(request, tenant=tenant)
        if medicine_id:
            medicine_filters = {"id": medicine_id, "tenant": tenant}
            if is_collaborative_retail:
                medicine_filters["created_by"] = request.user

            medicine = Medicine.objects.filter(**medicine_filters).prefetch_related("batches").first()
            if not medicine:
                return Response({"success": False, "message": "Medicine not found"}, status=404)
            return Response({"success": True, "data": _medicine_payload(medicine), "currency": tenant.currency})

        medicines = Medicine.objects.filter(tenant=tenant)
        if is_collaborative_retail:
            medicines = medicines.filter(created_by=request.user).prefetch_related(
                Prefetch("batches", queryset=StockBatch.objects.filter(created_by=request.user).order_by("-created_at"))
            )
        else:
            medicines = medicines.prefetch_related("batches")
        medicines = medicines.order_by("-created_at")
        data = [_medicine_payload(m) for m in medicines]
        return Response({"success": True, "data": data, "currency": tenant.currency})

    def post(self, request):
        tenant = self._get_tenant(request)
        if not tenant:
            return Response({"success": False, "message": "No tenant context"}, status=403)
        is_collaborative_retail = self._is_collaborative_retail(request, tenant=tenant)

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
            created_by=request.user if is_collaborative_retail else None,
            brand_name=name,
            generic_name=request.data.get("generic_name"),
            manufacturer=request.data.get("manufacturer"),
            category=request.data.get("category"),
            unit=request.data.get("unit"),
            description=request.data.get("description"),
        )

        StockBatch.objects.create(
            medicine=medicine,
            created_by=request.user if is_collaborative_retail else None,
            batch_number=batch_number,
            quantity=quantity,
            purchase_price=purchase_price,
            selling_price=selling_price,
            expiry_date=expiry_date or None,
            supplier_name=_first_present(request.data, "supplier_name", "supplierName", "wholesale_name", "wholesaleName"),
            supplier_phone=_first_present(request.data, "supplier_phone", "supplierPhone"),
            supplier_address=_first_present(request.data, "supplier_address", "supplierAddress"),
        )
        return Response({"success": True, "data": _medicine_payload(medicine)}, status=201)

    def patch(self, request, medicine_id):
        tenant = self._get_tenant(request)
        if not tenant:
            return Response({"success": False, "message": "No tenant context"}, status=403)
        is_collaborative_retail = self._is_collaborative_retail(request, tenant=tenant)

        medicine_filters = {"id": medicine_id, "tenant": tenant}
        if is_collaborative_retail:
            medicine_filters["created_by"] = request.user

        medicine = Medicine.objects.filter(**medicine_filters).first()
        if not medicine:
            return Response({"success": False, "message": "Medicine not found"}, status=404)

        medicine_field_aliases = {
            "brand_name": ("brand_name", "brandName", "name"),
            "generic_name": ("generic_name", "genericName"),
            "manufacturer": ("manufacturer",),
            "category": ("category",),
            "unit": ("unit",),
            "description": ("description",),
        }
        changed_medicine_fields = []
        for model_field, request_fields in medicine_field_aliases.items():
            for request_field in request_fields:
                if request_field in request.data:
                    setattr(medicine, model_field, request.data.get(request_field))
                    changed_medicine_fields.append(model_field)
                    break

        if changed_medicine_fields:
            medicine.save(update_fields=changed_medicine_fields)

        batch_field_aliases = {
            "batch_number": ("batch_number", "batchNumber"),
            "quantity": ("quantity",),
            "purchase_price": ("purchase_price", "purchasePrice"),
            "selling_price": ("selling_price", "sellingPrice"),
            "manufacture_date": ("manufacture_date", "manufactureDate"),
            "expiry_date": ("expiry_date", "expiryDate"),
            "supplier_name": ("supplier_name", "supplierName", "wholesale_name", "wholesaleName"),
            "supplier_phone": ("supplier_phone", "supplierPhone"),
            "supplier_address": ("supplier_address", "supplierAddress"),
        }
        batch_payload = {}
        nested_batches = request.data.get("RetailStocks") or request.data.get("retailStocks")
        if isinstance(nested_batches, list) and nested_batches:
            batch_payload = nested_batches[0] or {}
        elif isinstance(nested_batches, dict):
            batch_payload = nested_batches
        elif isinstance(request.data.get("stock"), dict):
            batch_payload = request.data.get("stock")

        changed_batch_values = {}
        for model_field, request_fields in batch_field_aliases.items():
            for request_field in request_fields:
                if request_field in batch_payload:
                    value = batch_payload.get(request_field)
                    changed_batch_values[model_field] = value or None if model_field.endswith("_date") else value
                    break
                if request_field in request.data:
                    value = request.data.get(request_field)
                    changed_batch_values[model_field] = value or None if model_field.endswith("_date") else value
                    break

        if changed_batch_values:
            batches_qs = medicine.batches.all()
            if is_collaborative_retail:
                batches_qs = batches_qs.filter(created_by=request.user)
            batch = batches_qs.order_by("-created_at").first()
            if not batch:
                return Response({"success": False, "message": "Stock batch not found"}, status=404)

            for field, value in changed_batch_values.items():
                if field in {"purchase_price", "selling_price"} and value not in (None, ""):
                    value = Decimal(str(value))
                elif field == "quantity" and value not in (None, ""):
                    value = int(value)
                setattr(batch, field, value)
            batch.save(update_fields=list(changed_batch_values.keys()))

        return Response({"success": True, "data": _medicine_payload(medicine)})

    def delete(self, request, medicine_id):
        tenant = self._get_tenant(request)
        if not tenant:
            return Response({"success": False, "message": "No tenant context"}, status=403)
        is_collaborative_retail = self._is_collaborative_retail(request, tenant=tenant)

        medicine_filters = {"id": medicine_id, "tenant": tenant}
        if is_collaborative_retail:
            medicine_filters["created_by"] = request.user

        medicine = Medicine.objects.filter(**medicine_filters).first()
        if not medicine:
            return Response({"success": False, "message": "Medicine not found"}, status=404)

        if SaleItem.objects.filter(medicine=medicine).exists():
            return Response(
                {"success": False, "message": "Medicine cannot be deleted because it is used in existing sales records"},
                status=400,
            )

        medicine.delete()
        return Response({"success": True})


class RetailStockView(RetailBaseView):
    required_subscription_feature = "inventory_management"

    def post(self, request):
        tenant = self._get_tenant(request)
        if not tenant:
            return Response({"success": False, "message": "No tenant context"}, status=403)
        is_collaborative_retail = self._is_collaborative_retail(request, tenant=tenant)

        medicine_id = request.data.get("medicineId")
        quantity = int(request.data.get("quantity", 0))
        batch_number = request.data.get("batchNumber") or f"BATCH-{timezone.now().strftime('%Y%m%d%H%M%S')}"
        expiry_date = request.data.get("expiryDate")

        if not medicine_id or quantity <= 0:
            return Response({"success": False, "message": "medicineId and quantity are required"}, status=400)

        medicine_filters = {"id": medicine_id, "tenant": tenant}
        if is_collaborative_retail:
            medicine_filters["created_by"] = request.user
        medicine = Medicine.objects.filter(**medicine_filters).first()
        if not medicine:
            return Response({"success": False, "message": "Medicine not found"}, status=404)

        batches_qs = medicine.batches.all()
        if is_collaborative_retail:
            batches_qs = batches_qs.filter(created_by=request.user)
        latest = batches_qs.order_by("-created_at").first()
        purchase_price = latest.purchase_price if latest else Decimal("0.00")
        selling_price = latest.selling_price if latest else Decimal("0.00")

        batch = StockBatch.objects.create(
            medicine=medicine,
            created_by=request.user if is_collaborative_retail else None,
            batch_number=batch_number,
            quantity=quantity,
            purchase_price=purchase_price,
            selling_price=selling_price,
            expiry_date=expiry_date or None,
            supplier_name=_first_present(request.data, "supplier_name", "supplierName", "wholesale_name", "wholesaleName"),
            supplier_phone=_first_present(request.data, "supplier_phone", "supplierPhone"),
            supplier_address=_first_present(request.data, "supplier_address", "supplierAddress"),
        )
        return Response({"success": True, "data": {"id": str(batch.id)}}, status=201)


class CollaborativeRetailWholesaleCatalogView(RetailBaseView):
    required_subscription_feature = "collaborative_retail_orders"
    allowed_subscription_business_types = {"WHOLESALE"}

    def get(self, request):
        tenant = self._get_tenant(request)
        if not tenant:
            return Response({"success": False, "message": "No tenant context"}, status=403)
        if not self._is_collaborative_retail(request, tenant=tenant):
            return Response(
                {"success": False, "message": "This catalog is available only for collaborative retail users"},
                status=403,
            )

        medicines = (
            Medicine.objects.filter(tenant=tenant)
            .filter(Q(created_by__department="WHOLESALE") | Q(created_by__isnull=True))
            .prefetch_related(
                Prefetch(
                    "batches",
                    queryset=StockBatch.objects.filter(
                        Q(created_by__department="WHOLESALE") | Q(created_by__isnull=True)
                    ).order_by("-created_at"),
                )
            )
            .order_by("-created_at")
        )
        data = [_medicine_payload(medicine) for medicine in medicines]
        return Response({"success": True, "data": data, "currency": tenant.currency})


class RetailSalesView(RetailBaseView):
    required_subscription_feature = "sales_management"

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
        is_collaborative_retail = self._is_collaborative_retail(request, tenant=tenant)

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

            batch_qs = StockBatch.objects.filter(medicine_id=medicine_id, medicine__tenant=tenant, quantity__gt=0)
            if is_collaborative_retail:
                batch_qs = batch_qs.filter(created_by=request.user)
            batch = batch_qs.order_by("expiry_date", "created_at").first()
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
    required_subscription_feature = "sales_management"

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

        expenses = Expense.objects.filter(tenant=tenant)
        if self._is_collaborative_retail(request, tenant=tenant):
            expenses = expenses.filter(created_by=request.user)
        expenses = expenses.select_related("category").order_by("-expense_date")
        data = [_expense_payload(e) for e in expenses]
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

        raw_amount = request.data.get("amount", 0)
        try:
            amount = Decimal(str(raw_amount))
        except Exception:
            return Response({"success": False, "message": "amount must be a valid number"}, status=400)

        raw_expense_date = request.data.get("expense_date") or request.data.get("expenseDate")
        expense_date = parse_date(str(raw_expense_date)) if raw_expense_date else timezone.now().date()
        if raw_expense_date and not expense_date:
            return Response({"success": False, "message": "expense_date must be in YYYY-MM-DD format"}, status=400)

        category_name = request.data.get("category") or "other"
        category, _ = ExpenseCategory.objects.get_or_create(tenant=tenant, name=category_name)
        expense = Expense.objects.create(
            tenant=tenant,
            category=category,
            amount=amount,
            description=request.data.get("description"),
            expense_date=expense_date,
            created_by=request.user,
        )
        return Response(
            {
                "success": True,
                "data": _expense_payload(expense),
            },
            status=201,
        )


class RetailExpenseDeleteView(RetailBaseView):
    def delete(self, request, expense_id):
        tenant = self._get_tenant(request)
        if not tenant:
            return Response({"success": False, "message": "No tenant context"}, status=403)

        expense_qs = Expense.objects.filter(id=expense_id, tenant=tenant)
        if self._is_collaborative_retail(request, tenant=tenant):
            expense_qs = expense_qs.filter(created_by=request.user)
        expense = expense_qs.first()
        if not expense:
            return Response({"success": False, "message": "Expense not found"}, status=404)
        expense.delete()
        return Response({"success": True})


class RetailExpiringMedicinesView(RetailBaseView):
    required_subscription_feature = "expiry_alerts"

    def get(self, request):
        tenant = self._get_tenant(request)
        if not tenant:
            return Response({"success": False, "message": "No tenant context"}, status=403)
        is_collaborative_retail = self._is_collaborative_retail(request, tenant=tenant)

        days = int(request.query_params.get("days", 30))
        today = timezone.now().date()
        cutoff = today + timedelta(days=days)
        batches = StockBatch.objects.filter(
            medicine__tenant=tenant,
            quantity__gt=0,
            expiry_date__isnull=False,
            expiry_date__lte=cutoff,
        )
        if is_collaborative_retail:
            batches = batches.filter(created_by=request.user)
        batches = batches.select_related("medicine").order_by("expiry_date")
        data = [
            {
                "id": str(b.id),
                "batch_number": b.batch_number,
                "quantity": b.quantity,
                "purchase_price": float(b.purchase_price),
                "selling_price": float(b.selling_price),
                "expiry_date": b.expiry_date.isoformat() if b.expiry_date else None,
                "supplier_name": b.supplier_name,
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
    required_subscription_feature = "inventory_management"

    def get(self, request):
        tenant = self._get_tenant(request)
        if not tenant:
            return Response({"success": False, "message": "No tenant context"}, status=403)
        is_collaborative_retail = self._is_collaborative_retail(request, tenant=tenant)

        medicines = Medicine.objects.filter(tenant=tenant)
        if is_collaborative_retail:
            medicines = medicines.filter(created_by=request.user).annotate(
                total_qty=Coalesce(Sum("batches__quantity", filter=Q(batches__created_by=request.user)), Value(0))
            )
        else:
            medicines = medicines.annotate(total_qty=Coalesce(Sum("batches__quantity"), Value(0)))
        medicines = medicines.filter(total_qty__lt=LOW_STOCK_THRESHOLD).order_by("brand_name")
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
        is_collaborative_retail = self._is_collaborative_retail(request, tenant=tenant)

        today = timezone.now().date()
        week_start = today - timedelta(days=6)
        month_start = today.replace(day=1)

        sales_base = Sale.objects.filter(tenant=tenant)
        if is_collaborative_retail:
            sales_base = sales_base.filter(cashier=request.user)

        sales_today = sales_base.filter(created_at__date=today)
        sales_week = sales_base.filter(created_at__date__gte=week_start, created_at__date__lte=today)
        sales_month = sales_base.filter(created_at__date__gte=month_start, created_at__date__lte=today)

        def _metrics(qs):
            revenue = qs.aggregate(total=Coalesce(Sum("total_amount"), Decimal("0.00"))).get("total") or Decimal("0.00")
            return {
                "revenue": float(revenue),
                "profit": float(revenue),
                "sales_count": qs.count(),
            }

        stock_batches_qs = StockBatch.objects.filter(medicine__tenant=tenant, quantity__gt=0)
        medicines_qs = Medicine.objects.filter(tenant=tenant)
        if is_collaborative_retail:
            stock_batches_qs = stock_batches_qs.filter(created_by=request.user)
            medicines_qs = medicines_qs.filter(created_by=request.user)

        stock_value = (
            stock_batches_qs.aggregate(
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

        expiring_batches = stock_batches_qs.filter(
            expiry_date__isnull=False,
            expiry_date__lte=today + timedelta(days=30),
        ).select_related("medicine").order_by("expiry_date")
        expiring_count = expiring_batches.count()
        total_medicines = medicines_qs.count()
        low_stock_count = (
            medicines_qs.annotate(total_qty=Coalesce(Sum("batches__quantity"), Value(0)))
            .filter(total_qty__lt=LOW_STOCK_THRESHOLD)
            .count()
        )

        top_medicines_filter = {"sale__tenant": tenant}
        if is_collaborative_retail:
            top_medicines_filter["sale__cashier"] = request.user

        top_medicines_qs = (
            SaleItem.objects.filter(**top_medicines_filter)
            .values("medicine__brand_name")
            .annotate(total_qty=Coalesce(Sum("quantity"), 0))
            .order_by("-total_qty")[:5]
        )
        top_medicines = [
            {"name": row["medicine__brand_name"], "quantity": row["total_qty"]}
            for row in top_medicines_qs
        ]

        cash_sales = (
            sales_base.filter(payment_method="CASH")
            .aggregate(total=Coalesce(Sum("total_amount"), Decimal("0.00")))
            .get("total")
            or Decimal("0.00")
        )
        insurance_sales = (
            sales_base.filter(payment_method="CARD")
            .aggregate(total=Coalesce(Sum("total_amount"), Decimal("0.00")))
            .get("total")
            or Decimal("0.00")
        )
        pending_insurance = (
            sales_base.filter(payment_method="CARD", due_amount__gt=0)
            .aggregate(total=Coalesce(Sum("due_amount"), Decimal("0.00")))
            .get("total")
            or Decimal("0.00")
        )

        expiring_data = [
            {
                "id": str(batch.id),
                "batch_number": batch.batch_number,
                "quantity": batch.quantity,
                "expiry_date": batch.expiry_date.isoformat() if batch.expiry_date else None,
                "supplier_name": batch.supplier_name,
                "supplier_phone": batch.supplier_phone,
                "supplier_address": batch.supplier_address,
                "Medicine": {
                    "id": str(batch.medicine.id),
                    "name": batch.medicine.brand_name,
                    "generic_name": batch.medicine.generic_name,
                },
            }
            for batch in expiring_batches
        ]

        daily_qs = (
            sales_base.filter(created_at__date__gte=week_start, created_at__date__lte=today)
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
                        "total_medicines": total_medicines,
                        "low_stock_count": low_stock_count,
                    },
                    "expiring": {
                        "count": expiring_count,
                        "data": expiring_data,
                    },
                    "top_medicines": top_medicines,
                    "payment_methods": {
                        "cash_sales": float(cash_sales),
                        "insurance_sales": float(insurance_sales),
                        "pending_insurance": float(pending_insurance),
                    },
                    "daily_trend": daily_trend,
                    "isCollaborativeRetail": is_collaborative_retail,
                },
            }
        )


class RetailReportsView(RetailBaseView):
    required_subscription_feature = "advanced_reports"

    def _report_date_range(self, request):
        selected_date = _first_present(request.query_params, "date", "selectedDate", "reportDate")
        start_date = _first_present(request.query_params, "start_date", "startDate", "fromDate", "dateFrom")
        end_date = _first_present(request.query_params, "end_date", "endDate", "toDate", "dateTo")

        if selected_date and not start_date and not end_date:
            start_date = selected_date
            end_date = selected_date

        parsed_start = parse_date(start_date) if start_date else None
        parsed_end = parse_date(end_date) if end_date else None
        if start_date and not parsed_start:
            return None, None, "start_date must be in YYYY-MM-DD format"
        if end_date and not parsed_end:
            return None, None, "end_date must be in YYYY-MM-DD format"
        if parsed_start and parsed_end and parsed_start > parsed_end:
            return None, None, "start_date cannot be after end_date"

        return parsed_start, parsed_end, None

    def get(self, request):
        tenant = self._get_tenant(request)
        if not tenant:
            return Response({"success": False, "message": "No tenant context"}, status=403)
        is_collaborative_retail = self._is_collaborative_retail(request, tenant=tenant)

        start_date, end_date, date_error = self._report_date_range(request)
        if date_error:
            return Response({"success": False, "message": date_error}, status=400)
        start_date_label = start_date.isoformat() if start_date else None
        end_date_label = end_date.isoformat() if end_date else None

        sales_qs = Sale.objects.filter(tenant=tenant)
        expenses_qs = Expense.objects.filter(tenant=tenant)
        if is_collaborative_retail:
            sales_qs = sales_qs.filter(cashier=request.user)
            expenses_qs = expenses_qs.filter(created_by=request.user)
        sales_qs = sales_qs.prefetch_related("items__medicine")
        expenses_qs = expenses_qs.select_related("category")

        if start_date:
            sales_qs = sales_qs.filter(created_at__date__gte=start_date)
            expenses_qs = expenses_qs.filter(expense_date__gte=start_date)
        if end_date:
            sales_qs = sales_qs.filter(created_at__date__lte=end_date)
            expenses_qs = expenses_qs.filter(expense_date__lte=end_date)

        total_sales = sales_qs.aggregate(total=Coalesce(Sum("total_amount"), Decimal("0.00"))).get("total") or Decimal("0.00")
        total_expenses = expenses_qs.aggregate(total=Coalesce(Sum("amount"), Decimal("0.00"))).get("total") or Decimal("0.00")
        total_paid = sales_qs.aggregate(total=Coalesce(Sum("paid_amount"), Decimal("0.00"))).get("total") or Decimal("0.00")
        total_due = sales_qs.aggregate(total=Coalesce(Sum("due_amount"), Decimal("0.00"))).get("total") or Decimal("0.00")
        total_discounts = sales_qs.aggregate(total=Coalesce(Sum("discount_amount"), Decimal("0.00"))).get("total") or Decimal("0.00")
        transactions = sales_qs.count()

        stock_qs = StockBatch.objects.filter(medicine__tenant=tenant, quantity__gt=0)
        if is_collaborative_retail:
            stock_qs = stock_qs.filter(created_by=request.user)
        stock_qs = stock_qs.select_related("medicine")
        expiring_qs = stock_qs.filter(expiry_date__isnull=False, expiry_date__lte=timezone.now().date() + timedelta(days=30))
        expired_qs = stock_qs.filter(expiry_date__isnull=False, expiry_date__lt=timezone.now().date())

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
        gross_profit = total_sales - cost_of_goods
        net_profit = total_sales - total_expenses
        profit_margin = ((net_profit / total_sales) * 100) if total_sales else Decimal("0.00")
        inventory_value = (
            stock_qs.aggregate(
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
            revenue_rows.append(
                {
                    "category": display_label(row["medicine__category"]),
                    "revenue": money(amount),
                    "percentage": percent((amount / total_sales * 100) if total_sales else Decimal("0.00")),
                }
            )
        revenue_rows.append({"category": "Total Revenue", "revenue": money(total_sales), "percentage": 100.0 if total_sales else 0.0})

        expense_rows = []
        for row in (
            expenses_qs.values("category__name")
            .annotate(amount=Coalesce(Sum("amount"), Decimal("0.00")))
            .order_by("-amount")
        ):
            amount = row["amount"] or Decimal("0.00")
            expense_rows.append(
                {
                    "type": display_label(row["category__name"], "Other Expenses"),
                    "amount": money(amount),
                    "percentage": percent((amount / total_expenses * 100) if total_expenses else Decimal("0.00")),
                }
            )
        if cost_of_goods:
            expense_rows.insert(
                0,
                {
                    "type": "Inventory Purchases (COGS)",
                    "amount": money(cost_of_goods),
                    "percentage": percent((cost_of_goods / total_sales * 100) if total_sales else Decimal("0.00")),
                },
            )
        expense_rows.append({"type": "Total Expenses", "amount": money(total_expenses), "percentage": 100.0 if total_expenses else 0.0})

        top_medicines_rows = []
        for row in (
            sale_items_qs.values("medicine__brand_name")
            .annotate(qty_sold=Coalesce(Sum("quantity"), 0), revenue=Coalesce(Sum("subtotal"), Decimal("0.00")))
            .order_by("-revenue")[:10]
        ):
            top_medicines_rows.append(
                {
                    "medicine": row["medicine__brand_name"] or "Unknown Medicine",
                    "qtySold": int(row["qty_sold"] or 0),
                    "revenue": money(row["revenue"] or Decimal("0.00")),
                }
            )

        stock_loss_rows = [
            {"reason": "Expired Medicines", "value": money(expired_stock_loss), "count": expired_qs.count()},
            {"reason": "Damaged Stock", "value": money(MONEY_ZERO), "count": 0},
            {"reason": "Missing Stock", "value": money(MONEY_ZERO), "count": 0},
        ]
        total_stock_loss = expired_stock_loss

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
                "supplier_name": batch.supplier_name,
                "supplier_phone": batch.supplier_phone,
                "supplier_address": batch.supplier_address,
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
                "totalPaid": float(total_paid),
                "totalDue": float(total_due),
                "totalDiscounts": float(total_discounts),
                "totalExpenses": float(total_expenses),
                "costOfGoods": float(cost_of_goods),
                "grossProfit": float(gross_profit),
                "netProfit": float(net_profit),
                "profitMargin": percent(profit_margin),
                "transactions": transactions,
                "inventoryValue": float(inventory_value),
                "expiredStockLoss": float(expired_stock_loss),
            },
            "dateRange": {
                "start_date": start_date_label,
                "end_date": end_date_label,
                "startDate": start_date_label,
                "endDate": end_date_label,
            },
            "isCollaborativeRetail": is_collaborative_retail,
            "printReport": {
                "title": "Financial Performance Report",
                "period": report_period(start_date_label, end_date_label),
                "reportNumber": f"FR-{timezone.now().strftime('%Y%m%d-%H%M%S')}",
                "generatedAt": timezone.now().isoformat(),
                "generatedBy": request.user.name or request.user.email,
                "page": {"current": 1, "total": 1},
                "branding": report_branding(tenant),
                "theme": report_theme(),
                "summary": {
                    "totalRevenue": money(total_sales),
                    "totalExpenses": money(total_expenses),
                    "grossProfit": money(gross_profit),
                    "netProfit": money(net_profit),
                    "profitMargin": percent(profit_margin),
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
                            {"description": "Total Revenue", "amount": money(total_sales)},
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
                        "rows": stock_loss_rows + [{"reason": "Total Loss", "value": money(total_stock_loss), "count": expired_qs.count()}],
                    },
                ],
            },
        }
        return Response({"success": True, "data": data, "currency": tenant.currency})


class RetailReportsDownloadView(RetailReportsView):
    required_subscription_feature = "advanced_reports"
    renderer_classes = [PDFRenderer, JSONRenderer]

    def get(self, request):
        report_response = super().get(request)
        if report_response.status_code != status.HTTP_200_OK:
            return report_response

        report = report_response.data["data"]["printReport"]
        pdf_bytes = render_report_pdf(report)
        filename = f"retail_financial_report_{timezone.now().strftime('%Y%m%d%H%M%S')}.pdf"
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        response["Access-Control-Expose-Headers"] = "Content-Disposition"
        return response
