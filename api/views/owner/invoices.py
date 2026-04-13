from datetime import datetime
from decimal import Decimal

from django.db.models import Q, Sum
from django.db.models.functions import Coalesce
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ...models import Notification, Sale, Tenant, UserTenant
from ...utils.subscription_access import authorize_tenant_access


class OwnerInvoicesBaseView(APIView):
    permission_classes = [IsAuthenticated]
    required_subscription_feature = "sales_management"

    def _sale_item_names(self, sale):
        names = []
        seen = set()
        for item in sale.items.all():
            name = item.medicine.brand_name if item.medicine_id and item.medicine else None
            if name and name not in seen:
                seen.add(name)
                names.append(name)
        return names

    def _get_tenant_id(self, request):
        tenant_id = request.query_params.get("tenantId")
        if not tenant_id:
            return None, Response(
                {"detail": "tenantId is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return tenant_id, None

    def _ensure_owner_access(self, request, tenant_id):
        _, error_message, error_status = authorize_tenant_access(
            request,
            tenant_id,
            required_role="OWNER",
            required_feature=self.required_subscription_feature,
        )
        if error_message:
            return Response({"detail": error_message}, status=error_status)
        return None

    def _tenant_currency(self, tenant_id):
        tenant = Tenant.objects.only("id", "currency").filter(id=tenant_id).first()
        return tenant.currency if tenant else "USD"

    def _parse_positive_int(self, value, name, default):
        raw = value if value is not None else default
        try:
            parsed = int(raw)
        except (TypeError, ValueError):
            return None, Response(
                {"detail": f"{name} must be a valid integer"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if parsed <= 0:
            return None, Response(
                {"detail": f"{name} must be greater than 0"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return parsed, None

    def _apply_common_filters(self, request, queryset):
        status_filter = request.query_params.get("status")
        payment_status = request.query_params.get("paymentStatus")
        search = request.query_params.get("search", "").strip()
        start_date = request.query_params.get("startDate")
        end_date = request.query_params.get("endDate")

        if status_filter and status_filter.lower() != "all":
            queryset = queryset.filter(status=status_filter)

        if payment_status:
            if payment_status == "PAID":
                queryset = queryset.filter(due_amount=0)
            elif payment_status == "PARTIAL":
                queryset = queryset.filter(paid_amount__gt=0, due_amount__gt=0)
            elif payment_status == "UNPAID":
                queryset = queryset.filter(paid_amount=0, due_amount__gt=0)

        if search:
            queryset = queryset.filter(
                Q(invoice_number__icontains=search)
                | Q(customer_name__icontains=search)
                | Q(customer_phone__icontains=search)
            )

        if start_date:
            try:
                parsed_start = datetime.strptime(start_date, "%Y-%m-%d").date()
                queryset = queryset.filter(created_at__date__gte=parsed_start)
            except ValueError:
                return None, Response(
                    {"detail": "startDate must be in YYYY-MM-DD format"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        if end_date:
            try:
                parsed_end = datetime.strptime(end_date, "%Y-%m-%d").date()
                queryset = queryset.filter(created_at__date__lte=parsed_end)
            except ValueError:
                return None, Response(
                    {"detail": "endDate must be in YYYY-MM-DD format"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        return queryset, None

    def _owner_visible_invoices(self, tenant_id):
        # Owner can action partial-payment invoices; fully-paid invoices are visible only if COMPLETED.
        return Sale.objects.filter(tenant_id=tenant_id).filter(
            Q(payment_option="PARTIAL", due_amount__gt=0)
            | Q(due_amount=0, status="COMPLETED")
        )

    def _notify_other_approvers(self, tenant_id, actor_user, invoice, action, reason=""):
        approver_roles = ["STORE_KEEPER", "ACCOUNTANT", "PHARMACIST", "CASHIER"]
        recipients = UserTenant.objects.filter(
            tenant_id=tenant_id,
            role__in=approver_roles,
        ).exclude(user=actor_user)

        action_text = "approved" if action == "approve" else "rejected"
        reason_text = f" Reason: {reason}" if reason else ""

        for recipient in recipients:
            Notification.objects.create(
                tenant_id=tenant_id,
                recipient_id=recipient.user_id,
                title=f"Partial Invoice {action_text.title()} by Owner",
                message=(
                    f"Invoice {invoice.invoice_number} partial payment request was "
                    f"{action_text} by owner.{reason_text}"
                ),
            )

    def _approved_by_name(self, sale):
        if not sale.approved_by:
            return None
        return sale.approved_by.name

    def _approved_by_role(self, sale):
        if not sale.approved_by:
            return None
        if sale.approved_by.is_super_admin:
            return "SUPER_ADMIN"
        user_tenant = UserTenant.objects.filter(
            user=sale.approved_by,
            tenant_id=sale.tenant_id,
        ).first()
        return user_tenant.role if user_tenant else None


class OwnerInvoicesListView(OwnerInvoicesBaseView):
    @extend_schema(
        description="List owner invoices with filters and pagination",
        tags=["owner"],
    )
    def get(self, request):
        tenant_id, error = self._get_tenant_id(request)
        if error:
            return error

        access_error = self._ensure_owner_access(request, tenant_id)
        if access_error:
            return access_error

        page, page_error = self._parse_positive_int(
            request.query_params.get("page"), "page", 1
        )
        if page_error:
            return page_error

        page_size, page_size_error = self._parse_positive_int(
            request.query_params.get("pageSize"), "pageSize", 10
        )
        if page_size_error:
            return page_size_error

        invoices = (
            self._owner_visible_invoices(tenant_id)
            .select_related("cashier", "approved_by")
            .prefetch_related("items__medicine")
            .order_by("-created_at")
        )
        invoices, filter_error = self._apply_common_filters(request, invoices)
        if filter_error:
            return filter_error

        total_count = invoices.count()
        start = (page - 1) * page_size
        end = start + page_size

        results = []
        for sale in invoices[start:end]:
            item_names = self._sale_item_names(sale)
            results.append(
                {
                    "invoiceId": str(sale.id),
                    "invoiceNumber": sale.invoice_number,
                    "customerName": sale.customer_name,
                    "customerPhone": sale.customer_phone,
                    "totalAmount": str(sale.total_amount),
                    "paidAmount": str(sale.paid_amount),
                    "dueAmount": str(sale.due_amount),
                    "currency": sale.currency,
                    "paymentMethod": sale.payment_method,
                    "paymentOption": sale.payment_option,
                    "status": sale.status,
                    "itemsCount": sale.items.count(),
                    "items": item_names,
                    "cashierId": str(sale.cashier_id) if sale.cashier_id else None,
                    "cashierName": sale.cashier.name if sale.cashier_id and sale.cashier else "N/A",
                    "createdAt": sale.created_at,
                    "approvedAt": sale.approved_at,
                    "approvedBy": str(sale.approved_by_id) if sale.approved_by_id else None,
                    "approvedByName": self._approved_by_name(sale),
                    "approvedByRole": self._approved_by_role(sale),
                }
            )

        return Response(
            {
                "count": total_count,
                "next": page + 1 if end < total_count else None,
                "previous": page - 1 if page > 1 else None,
                "currency": self._tenant_currency(tenant_id),
                "results": results,
            },
            status=status.HTTP_200_OK,
        )


class OwnerInvoiceDetailView(OwnerInvoicesBaseView):
    @extend_schema(
        description="Get one owner invoice details with items",
        tags=["owner"],
    )
    def get(self, request, invoice_id):
        tenant_id, error = self._get_tenant_id(request)
        if error:
            return error

        access_error = self._ensure_owner_access(request, tenant_id)
        if access_error:
            return access_error

        try:
            sale = self._owner_visible_invoices(tenant_id).select_related("cashier", "approved_by").prefetch_related("items__medicine", "items__batch").get(
                id=invoice_id,
            )
        except Sale.DoesNotExist:
            return Response(
                {"detail": "Invoice not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        items = []
        for item in sale.items.all():
            items.append(
                {
                    "id": str(item.id),
                    "medicineId": str(item.medicine_id),
                    "medicineName": item.medicine.brand_name,
                    "batchId": str(item.batch_id),
                    "batchNumber": item.batch.batch_number,
                    "quantity": item.quantity,
                    "unitPrice": str(item.unit_price),
                    "subtotal": str(item.subtotal),
                }
            )

        return Response(
            {
                "invoiceId": str(sale.id),
                "invoiceNumber": sale.invoice_number,
                "customerName": sale.customer_name,
                "customerPhone": sale.customer_phone,
                "notes": sale.notes,
                "status": sale.status,
                "paymentOption": sale.payment_option,
                "paymentMethod": sale.payment_method,
                "subtotal": str(sale.subtotal),
                "discountAmount": str(sale.discount_amount),
                "paidAmount": str(sale.paid_amount),
                "dueAmount": str(sale.due_amount),
                "totalAmount": str(sale.total_amount),
                "currency": sale.currency,
                "createdAt": sale.created_at,
                "updatedAt": sale.updated_at,
                "cashierId": str(sale.cashier_id) if sale.cashier_id else None,
                "cashierName": sale.cashier.name if sale.cashier_id and sale.cashier else "N/A",
                "approvedAt": sale.approved_at,
                "approvedBy": str(sale.approved_by_id) if sale.approved_by_id else None,
                "approvedByName": self._approved_by_name(sale),
                "approvedByRole": self._approved_by_role(sale),
                "items": items,
            },
            status=status.HTTP_200_OK,
        )


class OwnerInvoicesSummaryView(OwnerInvoicesBaseView):
    @extend_schema(
        description="Owner invoice summary metrics",
        tags=["owner"],
    )
    def get(self, request):
        tenant_id, error = self._get_tenant_id(request)
        if error:
            return error

        access_error = self._ensure_owner_access(request, tenant_id)
        if access_error:
            return access_error

        invoices = self._owner_visible_invoices(tenant_id)
        invoices, filter_error = self._apply_common_filters(request, invoices)
        if filter_error:
            return filter_error

        aggregate = invoices.aggregate(
            totalAmount=Coalesce(Sum("total_amount"), Decimal("0.00")),
            paidAmount=Coalesce(Sum("paid_amount"), Decimal("0.00")),
            dueAmount=Coalesce(Sum("due_amount"), Decimal("0.00")),
        )

        paid_invoices = invoices.filter(due_amount=0).count()
        partial_invoices = invoices.filter(paid_amount__gt=0, due_amount__gt=0).count()
        unpaid_invoices = invoices.filter(paid_amount=0, due_amount__gt=0).count()

        return Response(
            {
                "totalInvoices": invoices.count(),
                "totalAmount": str(aggregate["totalAmount"]),
                "paidAmount": str(aggregate["paidAmount"]),
                "dueAmount": str(aggregate["dueAmount"]),
                "currency": self._tenant_currency(tenant_id),
                "paidInvoices": paid_invoices,
                "partialInvoices": partial_invoices,
                "unpaidInvoices": unpaid_invoices,
            },
            status=status.HTTP_200_OK,
        )


class OwnerInvoicesDashboardView(OwnerInvoicesBaseView):
    @extend_schema(
        description=(
            "Owner invoices consolidated payload (summary cards, charts, and "
            "recent invoices table)"
        ),
        tags=["owner"],
    )
    def get(self, request):
        tenant_id, error = self._get_tenant_id(request)
        if error:
            return error

        access_error = self._ensure_owner_access(request, tenant_id)
        if access_error:
            return access_error

        invoices = (
            self._owner_visible_invoices(tenant_id)
            .select_related("cashier", "approved_by")
            .prefetch_related("items__medicine")
            .order_by("-created_at")
        )
        invoices, filter_error = self._apply_common_filters(request, invoices)
        if filter_error:
            return filter_error

        invoices_list = list(invoices)
        total_invoices = len(invoices_list)

        total_amount = sum((invoice.total_amount for invoice in invoices_list), Decimal("0.00"))
        paid_amount = sum((invoice.paid_amount for invoice in invoices_list), Decimal("0.00"))
        due_amount = sum((invoice.due_amount for invoice in invoices_list), Decimal("0.00"))

        by_status = {}
        by_payment_state = {
            "PAID": 0,
            "PARTIAL": 0,
            "UNPAID": 0,
        }
        daily_paid = {}

        for invoice in invoices_list:
            by_status[invoice.status] = by_status.get(invoice.status, 0) + 1

            if invoice.due_amount == 0:
                by_payment_state["PAID"] += 1
            elif invoice.paid_amount > 0:
                by_payment_state["PARTIAL"] += 1
            else:
                by_payment_state["UNPAID"] += 1

            day = invoice.created_at.date().isoformat()
            daily_paid.setdefault(day, Decimal("0.00"))
            daily_paid[day] += invoice.paid_amount

        recent_rows = []
        for invoice in invoices_list[:10]:
            item_names = self._sale_item_names(invoice)
            recent_rows.append(
                {
                    "invoiceId": str(invoice.id),
                    "invoiceNumber": invoice.invoice_number,
                    "customerName": invoice.customer_name or "Walk-in Customer",
                    "customerPhone": invoice.customer_phone or "",
                    "status": invoice.status,
                    "paymentOption": invoice.payment_option,
                    "totalAmount": str(invoice.total_amount),
                    "paidAmount": str(invoice.paid_amount),
                    "dueAmount": str(invoice.due_amount),
                    "currency": invoice.currency,
                    "items": item_names,
                    "cashierId": str(invoice.cashier_id) if invoice.cashier_id else None,
                    "cashierName": invoice.cashier.name if invoice.cashier_id and invoice.cashier else "N/A",
                    "createdAt": invoice.created_at,
                    "approvedBy": str(invoice.approved_by_id) if invoice.approved_by_id else None,
                    "approvedByName": self._approved_by_name(invoice),
                    "approvedByRole": self._approved_by_role(invoice),
                }
            )

        return Response(
            {
                "summary": {
                    "totalInvoices": total_invoices,
                    "totalAmount": str(total_amount),
                    "paidAmount": str(paid_amount),
                    "dueAmount": str(due_amount),
                    "currency": self._tenant_currency(tenant_id),
                    "paidInvoices": by_payment_state["PAID"],
                    "partialInvoices": by_payment_state["PARTIAL"],
                    "unpaidInvoices": by_payment_state["UNPAID"],
                },
                "statusDistribution": [
                    {"status": status_key, "count": count}
                    for status_key, count in by_status.items()
                ],
                "paymentStatusDistribution": [
                    {"status": status_key, "count": count}
                    for status_key, count in by_payment_state.items()
                ],
                "collectionsTrend": {
                    "labels": sorted(daily_paid.keys()),
                    "data": [float(daily_paid[key]) for key in sorted(daily_paid.keys())],
                },
                "recentInvoices": {
                    "count": total_invoices,
                    "results": recent_rows,
                },
            },
            status=status.HTTP_200_OK,
        )


class OwnerApprovePartialInvoiceView(OwnerInvoicesBaseView):
    @extend_schema(
        description="Owner final approval for partial-payment invoice (confirm loan)",
        tags=["owner"],
    )
    def post(self, request, invoice_id):
        tenant_id, error = self._get_tenant_id(request)
        if error:
            return error

        access_error = self._ensure_owner_access(request, tenant_id)
        if access_error:
            return access_error

        try:
            sale = Sale.objects.get(id=invoice_id, tenant_id=tenant_id)
        except Sale.DoesNotExist:
            return Response({"detail": "Invoice not found"}, status=status.HTTP_404_NOT_FOUND)

        if sale.payment_option == "PARTIAL" and sale.due_amount > 0:
            return Response(
                {
                    "detail": (
                        "Use /api/owner/partial-payments/<invoice_id>/approve/ for "
                        "partial-payment approval workflow."
                    )
                },
                status=status.HTTP_409_CONFLICT,
            )

        return Response(
            {"detail": "Owner invoice approval is not available for this invoice"},
            status=status.HTTP_400_BAD_REQUEST,
        )


class OwnerRejectPartialInvoiceView(OwnerInvoicesBaseView):
    @extend_schema(
        description="Owner rejects partial-payment invoice",
        tags=["owner"],
    )
    def post(self, request, invoice_id):
        tenant_id, error = self._get_tenant_id(request)
        if error:
            return error

        access_error = self._ensure_owner_access(request, tenant_id)
        if access_error:
            return access_error

        try:
            sale = Sale.objects.get(id=invoice_id, tenant_id=tenant_id)
        except Sale.DoesNotExist:
            return Response({"detail": "Invoice not found"}, status=status.HTTP_404_NOT_FOUND)

        if sale.payment_option == "PARTIAL" and sale.due_amount > 0:
            return Response(
                {
                    "detail": (
                        "Use /api/owner/partial-payments/<invoice_id>/reject/ for "
                        "partial-payment approval workflow."
                    )
                },
                status=status.HTTP_409_CONFLICT,
            )

        return Response(
            {"detail": "Owner invoice rejection is not available for this invoice"},
            status=status.HTTP_400_BAD_REQUEST,
        )
