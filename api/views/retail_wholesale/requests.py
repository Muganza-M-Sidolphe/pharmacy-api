import logging
from decimal import Decimal

from django.conf import settings
from django.core.mail import send_mail
from django.db.models import Q
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ...models import Expense, ExpenseCategory, Medicine, Notification, RetailWholesaleRequest, RetailWholesaleRequestItem, Sale, SaleItem, StockBatch, UserTenant
from ...serializers import (
    CreateRetailWholesaleRequestSerializer,
    RetailWholesaleRequestSerializer,
)
from ...utils.subscription_access import check_subscription_access


logger = logging.getLogger(__name__)


def _tenant_membership(user, tenant_id):
    return UserTenant.objects.filter(user=user, tenant_id=tenant_id).select_related("tenant").first()


def _tenant_role(user, tenant_id):
    membership = UserTenant.objects.filter(user=user, tenant_id=tenant_id).first()
    return membership.role if membership else None


def _tenant_business_type(tenant):
    tenant_roles = UserTenant.objects.filter(tenant=tenant).values_list("role", flat=True)
    if "OWNER" in tenant_roles:
        return "WHOLESALE"
    if "PHARMACIST" in tenant_roles:
        return "RETAIL"
    return None


def _notify_users(tenant_id, recipients, title, message):
    notifications = []
    seen = set()
    for recipient in recipients:
        if not recipient or recipient.id in seen:
            continue
        seen.add(recipient.id)
        notifications.append(
            Notification(
                tenant_id=tenant_id,
                recipient=recipient,
                title=title,
                message=message,
            )
        )
    if notifications:
        Notification.objects.bulk_create(notifications)


def _send_workflow_emails(recipients, subject, message):
    from_email = (
        getattr(settings, "DEFAULT_FROM_EMAIL", None)
        or getattr(settings, "EMAIL_HOST_USER", "")
        or "no-reply@pharmacy.local"
    )
    seen_emails = set()
    for recipient in recipients:
        email = (getattr(recipient, "email", None) or "").strip().lower()
        if not email or email in seen_emails:
            continue
        seen_emails.add(email)
        try:
            send_mail(subject, message, from_email, [email], fail_silently=False)
        except Exception:  # pragma: no cover - depends on email backend configuration
            logger.exception(
                "Failed to send collaborative retail workflow email to %s",
                email,
            )


def _users_for_role_and_department(tenant_id, role=None, department=None):
    qs = UserTenant.objects.filter(tenant_id=tenant_id).select_related("user")
    if role:
        qs = qs.filter(role=role)
    if department:
        qs = qs.filter(user__department=department)
    return [item.user for item in qs]


def _notify_request_created(rw_request):
    owner_users = _users_for_role_and_department(
        rw_request.wholesale_tenant_id,
        role="OWNER",
        department="WHOLESALE",
    )
    title = "Collaborative Retail Request Created"
    message = (
        f"{rw_request.requested_by.name if rw_request.requested_by_id else 'Retail user'} "
        f"created request {rw_request.id}. Owner approval is required."
    )
    _notify_users(
        rw_request.wholesale_tenant_id,
        owner_users,
        title,
        message,
    )
    _send_workflow_emails(
        owner_users,
        title,
        (
            f"Hello,\n\n"
            f"A collaborative retail request has been created in {rw_request.wholesale_tenant.name}.\n\n"
            f"Request ID: {rw_request.id}\n"
            f"Requested by: {rw_request.requested_by.name if rw_request.requested_by_id else 'Retail user'}\n"
            f"Payment option: {rw_request.payment_option}\n"
            f"Current status: {rw_request.status}\n\n"
            "Owner approval is required next."
        ),
    )


def _notify_request_transition(rw_request, action):
    request_label = str(rw_request.id)[:8].upper()
    wholesale_tenant_id = rw_request.wholesale_tenant_id
    retail_user = rw_request.requested_by

    notification_map = {
        "OWNER_APPROVE": {
            "recipients": _users_for_role_and_department(wholesale_tenant_id, role="STORE_KEEPER", department="WHOLESALE"),
            "title": "Retail Request Approved By Owner",
            "message": f"Request {request_label} was approved by owner. Confirm stock next.",
        },
        "OWNER_REJECT": {
            "recipients": [retail_user],
            "title": "Retail Request Rejected",
            "message": f"Your request {request_label} was rejected by owner.",
        },
        "STOREKEEPER_CONFIRM_STOCK": {
            "recipients": _users_for_role_and_department(wholesale_tenant_id, role="PHARMACIST", department="WHOLESALE"),
            "title": "Retail Request Stock Confirmed",
            "message": f"Request {request_label} stock was confirmed. Pharmacist review is required.",
        },
        "PHARMACIST_APPROVE": {
            "recipients": [retail_user],
            "title": "Retail Request Awaiting Payment",
            "message": f"Your request {request_label} was approved by pharmacist and is now awaiting payment.",
        },
        "PHARMACIST_APPROVE_CREDIT": {
            "recipients": _users_for_role_and_department(wholesale_tenant_id, role="ACCOUNTANT", department="WHOLESALE"),
            "title": "Retail Credit Request Awaiting Accountant Confirmation",
            "message": f"Request {request_label} was approved by pharmacist under CREDIT terms. Accountant confirmation is required next.",
        },
        "PHARMACIST_REJECT": {
            "recipients": [retail_user],
            "title": "Retail Request Rejected By Pharmacist",
            "message": f"Your request {request_label} was rejected by pharmacist.",
        },
        "RETAIL_PAY": {
            "recipients": _users_for_role_and_department(wholesale_tenant_id, role="ACCOUNTANT", department="WHOLESALE"),
            "title": "Retail Payment Submitted",
            "message": f"Retail payment for request {request_label} was submitted. Accountant confirmation is required.",
        },
        "ACCOUNTANT_CONFIRM_PAYMENT": {
            "recipients": (
                _users_for_role_and_department(wholesale_tenant_id, role="STORE_KEEPER", department="WHOLESALE")
                + ([retail_user] if retail_user else [])
            ),
            "title": "Retail Payment Confirmed",
            "message": f"Payment for request {request_label} was confirmed. Order is moving to delivery preparation.",
        },
        "STOREKEEPER_PREPARE_ORDER": {
            "recipients": [retail_user],
            "title": "Retail Order Ready For Delivery",
            "message": f"Your request {request_label} is ready for delivery confirmation.",
        },
        "RETAIL_CONFIRM_RECEIVED": {
            "recipients": _users_for_role_and_department(wholesale_tenant_id, department="WHOLESALE"),
            "title": "Retail Order Delivered",
            "message": f"Retail request {request_label} was confirmed received and completed.",
        },
    }

    payload = notification_map.get(action)
    if not payload:
        return

    _notify_users(
        wholesale_tenant_id,
        payload["recipients"],
        payload["title"],
        payload["message"],
    )
    note_line = f"\nDecision note: {rw_request.decision_note}" if rw_request.decision_note else ""
    _send_workflow_emails(
        payload["recipients"],
        payload["title"],
        (
            f"Hello,\n\n"
            f"{payload['message']}\n\n"
            f"Tenant: {rw_request.wholesale_tenant.name}\n"
            f"Request ID: {rw_request.id}\n"
            f"Short code: {request_label}\n"
            f"Current status: {rw_request.status}\n"
            f"Payment option: {rw_request.payment_option}\n"
            f"Payment method: {rw_request.payment_method or 'Not set'}\n"
            f"Paid amount: {rw_request.paid_amount}\n"
            f"Requested by: {retail_user.name if retail_user else 'Retail user'}"
            f"{note_line}"
        ),
    )


def _request_invoice_number(rw_request):
    return f"RWR-{timezone.now().strftime('%Y%m%d%H%M%S')}-{str(rw_request.id)[:6].upper()}"


def _estimate_request_total(rw_request):
    total = Decimal("0.00")
    for item in rw_request.items.select_related("medicine").all():
        batch = _wholesale_inventory_batches(item.medicine).first()
        unit_price = batch.selling_price if batch else Decimal("0.00")
        total += unit_price * item.quantity
    return total


def _wholesale_inventory_batches(medicine):
    return (
        medicine.batches.filter(quantity__gt=0)
        .filter(Q(created_by__department="WHOLESALE") | Q(created_by__isnull=True))
        .order_by("expiry_date", "created_at")
    )


def _allocate_request_batches(rw_request):
    allocations = []
    for item in rw_request.items.select_related("medicine").all():
        remaining = item.quantity
        source_batches = list(_wholesale_inventory_batches(item.medicine))
        total_available = sum(max(batch.quantity, 0) for batch in source_batches)
        if total_available < item.quantity:
            raise ValueError(
                f"Insufficient wholesale stock for {item.medicine.brand_name}. "
                f"Requested {item.quantity}, available {total_available}."
            )

        for batch in source_batches:
            if remaining <= 0:
                break
            take_qty = min(batch.quantity, remaining)
            if take_qty <= 0:
                continue
            allocations.append(
                {
                    "request_item": item,
                    "medicine": item.medicine,
                    "batch": batch,
                    "quantity": take_qty,
                }
            )
            remaining -= take_qty

    return allocations


def _create_wholesale_sale_and_reduce_inventory(rw_request):
    if rw_request.wholesale_sale_id:
        return rw_request.wholesale_sale

    allocations = _allocate_request_batches(rw_request)
    subtotal = Decimal("0.00")
    requested_total = Decimal("0.00")
    collected_amount = Decimal("0.00")
    sale_status = "COMPLETED"
    sale_payment_option = rw_request.payment_option

    sale = Sale.objects.create(
        tenant=rw_request.wholesale_tenant,
        cashier=None,
        invoice_number=_request_invoice_number(rw_request),
        customer_name=(
            f"Collaborative Retail - {rw_request.requested_by.name}"
            if rw_request.requested_by_id
            else "Collaborative Retail"
        ),
        customer_phone=getattr(rw_request.requested_by, "email", None),
        notes=f"Generated from collaborative retail request {rw_request.id}",
        status="COMPLETED",
        payment_option=rw_request.payment_option,
        payment_method=rw_request.payment_method,
        currency=(rw_request.wholesale_tenant.currency or "USD"),
        paid_amount=Decimal("0.00"),
        due_amount=Decimal("0.00"),
        total_amount=Decimal("0.00"),
        subtotal=Decimal("0.00"),
        due_date=rw_request.due_date,
        approved_at=timezone.now(),
        approved_by=(
            rw_request.decided_by
            if rw_request.decided_by_id and getattr(rw_request.decided_by, "department", None) == "WHOLESALE"
            else None
        ),
        owner_approval_status="APPROVED",
        pharmacist_approval_status="APPROVED",
    )

    for allocation in allocations:
        batch = allocation["batch"]
        quantity = allocation["quantity"]
        unit_price = batch.selling_price
        item_subtotal = unit_price * quantity
        SaleItem.objects.create(
            sale=sale,
            medicine=allocation["medicine"],
            batch=batch,
            quantity=quantity,
            unit_price=unit_price,
            subtotal=item_subtotal,
        )
        batch.quantity -= quantity
        batch.save(update_fields=["quantity"])
        subtotal += item_subtotal

    requested_total = subtotal
    if rw_request.payment_option == "FULL":
        collected_amount = requested_total
        sale_status = "COMPLETED"
    elif rw_request.payment_option == "PARTIAL":
        collected_amount = min(max(rw_request.paid_amount, Decimal("0.00")), requested_total)
        sale_status = "APPROVED" if collected_amount < requested_total else "COMPLETED"
    elif rw_request.payment_option == "CREDIT":
        collected_amount = Decimal("0.00")
        sale_status = "APPROVED"

    sale.subtotal = requested_total
    sale.total_amount = requested_total
    sale.paid_amount = collected_amount
    sale.due_amount = requested_total - collected_amount
    sale.status = sale_status
    sale.save(
        update_fields=[
            "subtotal",
            "total_amount",
            "paid_amount",
            "due_amount",
            "status",
            "updated_at",
        ]
    )
    return sale


def _sync_request_status_from_sale(sale):
    source_request = sale.retail_wholesale_source_requests.first()
    if not source_request:
        return

    target_status = "COMPLETED" if sale.due_amount <= 0 else "DELIVERED"
    if source_request.status != target_status:
        source_request.status = target_status
        source_request.save(update_fields=["status", "updated_at"])


def _sync_completed_request_to_retail_inventory(rw_request):
    if not rw_request.requested_by_id:
        return

    if StockBatch.objects.filter(source_request=rw_request, created_by=rw_request.requested_by).exists():
        return

    sale_items = []
    if rw_request.wholesale_sale_id:
        sale_items = list(
            rw_request.wholesale_sale.items.select_related("medicine", "batch").all()
        )

    if sale_items:
        source_rows = [
            {
                "medicine": sale_item.medicine,
                "batch": sale_item.batch,
                "quantity": sale_item.quantity,
                "index": index,
            }
            for index, sale_item in enumerate(sale_items, start=1)
        ]
    else:
        request_items = rw_request.items.select_related("medicine").all()
        source_rows = [
            {
                "medicine": item.medicine,
                "batch": item.medicine.batches.order_by("-created_at").first(),
                "quantity": item.quantity,
                "index": index,
            }
            for index, item in enumerate(request_items, start=1)
        ]

    for row in source_rows:
        source_medicine = row["medicine"]
        source_batch = row["batch"]
        target_medicine = Medicine.objects.filter(
            tenant=rw_request.retail_tenant,
            created_by=rw_request.requested_by,
            brand_name=source_medicine.brand_name,
            generic_name=source_medicine.generic_name,
            manufacturer=source_medicine.manufacturer,
            category=source_medicine.category,
            unit=source_medicine.unit,
        ).first()

        if not target_medicine:
            target_medicine = Medicine.objects.create(
                tenant=rw_request.retail_tenant,
                created_by=rw_request.requested_by,
                brand_name=source_medicine.brand_name,
                generic_name=source_medicine.generic_name,
                manufacturer=source_medicine.manufacturer,
                category=source_medicine.category,
                unit=source_medicine.unit,
                description=source_medicine.description,
            )

        StockBatch.objects.create(
            medicine=target_medicine,
            source_request=rw_request,
            created_by=rw_request.requested_by,
            batch_number=f"REQ-{str(rw_request.id)[:8]}-{row['index']}",
            quantity=row["quantity"],
            purchase_price=source_batch.purchase_price if source_batch else 0,
            selling_price=source_batch.selling_price if source_batch else 0,
            manufacture_date=source_batch.manufacture_date if source_batch else None,
            expiry_date=source_batch.expiry_date if source_batch else None,
            supplier_name=rw_request.wholesale_tenant.name,
            supplier_phone=rw_request.wholesale_tenant.phone,
            supplier_address=rw_request.wholesale_tenant.address,
        )


def _create_retail_procurement_expense(rw_request):
    if rw_request.retail_procurement_expense_id:
        return rw_request.retail_procurement_expense

    total_amount = Decimal("0.00")
    if rw_request.wholesale_sale_id:
        total_amount = rw_request.wholesale_sale.total_amount
    else:
        for item in rw_request.items.select_related("medicine").all():
            batch = _wholesale_inventory_batches(item.medicine).first()
            unit_price = batch.selling_price if batch else Decimal("0.00")
            total_amount += unit_price * item.quantity

    category, _ = ExpenseCategory.objects.get_or_create(
        tenant=rw_request.retail_tenant,
        name="Wholesale Procurement",
    )
    expense = Expense.objects.create(
        tenant=rw_request.retail_tenant,
        category=category,
        amount=total_amount,
        description=(
            f"Collaborative wholesale order {rw_request.id} supplied by "
            f"{rw_request.wholesale_tenant.name}"
        ),
        expense_date=timezone.now().date(),
        created_by=rw_request.requested_by,
    )
    return expense


def _finalize_completed_request(rw_request):
    sale = _create_wholesale_sale_and_reduce_inventory(rw_request)
    if rw_request.wholesale_sale_id != sale.id:
        rw_request.wholesale_sale = sale

    _sync_completed_request_to_retail_inventory(rw_request)

    expense = _create_retail_procurement_expense(rw_request)
    if rw_request.retail_procurement_expense_id != expense.id:
        rw_request.retail_procurement_expense = expense

    rw_request.save(update_fields=["wholesale_sale", "retail_procurement_expense", "updated_at"])


class RetailWholesaleRequestListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        tenant_id = request.query_params.get("tenantId")
        if not tenant_id:
            return Response({"detail": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        membership = _tenant_membership(request.user, tenant_id)
        if not membership:
            return Response({"detail": "Unauthorized tenant access"}, status=status.HTTP_403_FORBIDDEN)
        if _tenant_business_type(membership.tenant) != "WHOLESALE":
            return Response(
                {"detail": "Wholesale requesting is available only inside WHOLESALE tenants"},
                status=status.HTTP_403_FORBIDDEN,
            )
        allowed, details = check_subscription_access(
            membership.tenant,
            required_feature="collaborative_retail_orders",
            allowed_business_types={"WHOLESALE"},
        )
        if not allowed:
            return Response({"detail": details}, status=status.HTTP_403_FORBIDDEN)

        if request.user.department == "RETAIL":
            queryset = (
                RetailWholesaleRequest.objects.filter(
                    retail_tenant_id=tenant_id,
                    requested_by=request.user,
                )
                .select_related("retail_tenant", "wholesale_tenant", "requested_by", "decided_by")
                .prefetch_related("items__medicine")
                .order_by("-created_at")
            )
        elif request.user.department == "WHOLESALE":
            queryset = (
                RetailWholesaleRequest.objects.filter(wholesale_tenant_id=tenant_id)
                .select_related("retail_tenant", "wholesale_tenant", "requested_by", "decided_by")
                .prefetch_related("items__medicine")
                .order_by("-created_at")
            )
        else:
            return Response({"detail": "Invalid department"}, status=status.HTTP_403_FORBIDDEN)

        return Response(RetailWholesaleRequestSerializer(queryset, many=True).data)

    @transaction.atomic
    def post(self, request):
        if request.user.department != "RETAIL":
            return Response(
                {"detail": "Only RETAIL department users can create wholesale requests"},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = CreateRetailWholesaleRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        retail_membership = _tenant_membership(request.user, data["tenantId"])
        if not retail_membership:
            return Response({"detail": "Unauthorized tenant access"}, status=status.HTTP_403_FORBIDDEN)
        if _tenant_business_type(retail_membership.tenant) != "WHOLESALE":
            return Response(
                {"detail": "Wholesale requesting is available only inside WHOLESALE tenants"},
                status=status.HTTP_403_FORBIDDEN,
            )
        allowed, details = check_subscription_access(
            retail_membership.tenant,
            required_feature="collaborative_retail_orders",
            allowed_business_types={"WHOLESALE"},
        )
        if not allowed:
            return Response({"detail": details}, status=status.HTTP_403_FORBIDDEN)

        wholesale_has_users = UserTenant.objects.filter(
            tenant_id=data["tenantId"],
            user__department="WHOLESALE",
        ).exists()
        if not wholesale_has_users:
            return Response(
                {"detail": "This tenant has no WHOLESALE department users"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        medicine_ids = [item["medicineId"] for item in data["items"]]
        medicine_map = {
            str(m.id): m
            for m in Medicine.objects.filter(id__in=medicine_ids, tenant_id=data["tenantId"])
        }

        missing = [str(med_id) for med_id in medicine_ids if str(med_id) not in medicine_map]
        if missing:
            return Response(
                {"detail": "Some medicines do not exist in this wholesale tenant", "medicineIds": missing},
                status=status.HTTP_400_BAD_REQUEST,
            )

        rw_request = RetailWholesaleRequest.objects.create(
            retail_tenant_id=data["tenantId"],
            wholesale_tenant_id=data["tenantId"],
            requested_by=request.user,
            payment_option=data["paymentOption"],
            payment_method=data["paymentMethod"],
            due_date=data.get("dueDate"),
            note=data.get("note"),
        )

        RetailWholesaleRequestItem.objects.bulk_create(
            [
                RetailWholesaleRequestItem(
                    request=rw_request,
                    medicine=medicine_map[str(item["medicineId"])],
                    quantity=item["quantity"],
                )
                for item in data["items"]
            ]
        )

        rw_request = (
            RetailWholesaleRequest.objects.filter(id=rw_request.id)
            .select_related("retail_tenant", "wholesale_tenant", "requested_by", "decided_by")
            .prefetch_related("items__medicine")
            .first()
        )

        _notify_request_created(rw_request)

        return Response(RetailWholesaleRequestSerializer(rw_request).data, status=status.HTTP_201_CREATED)


class RetailWholesaleRequestDecisionView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, request_id):
        tenant_id = request.data.get("tenantId") or request.query_params.get("tenantId")
        if not tenant_id:
            return Response({"detail": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        membership = _tenant_membership(request.user, tenant_id)
        if not membership:
            return Response({"detail": "Unauthorized tenant access"}, status=status.HTTP_403_FORBIDDEN)
        if _tenant_business_type(membership.tenant) != "WHOLESALE":
            return Response(
                {"detail": "Wholesale requesting is available only inside WHOLESALE tenants"},
                status=status.HTTP_403_FORBIDDEN,
            )
        allowed, details = check_subscription_access(
            membership.tenant,
            required_feature="collaborative_retail_orders",
            allowed_business_types={"WHOLESALE"},
        )
        if not allowed:
            return Response({"detail": details}, status=status.HTTP_403_FORBIDDEN)

        action = (request.data.get("action") or "").upper().strip()
        if not action:
            return Response({"detail": "action is required"}, status=status.HTTP_400_BAD_REQUEST)

        rw_request = (
            RetailWholesaleRequest.objects.filter(id=request_id, wholesale_tenant_id=tenant_id)
            .select_related("retail_tenant", "wholesale_tenant", "requested_by", "decided_by")
            .prefetch_related("items__medicine")
            .first()
        )
        if not rw_request:
            return Response({"detail": "Request not found"}, status=status.HTTP_404_NOT_FOUND)

        actor_role = _tenant_role(request.user, tenant_id)
        actor_department = request.user.department

        transition_rules = {
            ("PENDING", "OWNER_APPROVE"): {"role": "OWNER", "department": "WHOLESALE", "next_status": "OWNER_APPROVED"},
            ("PENDING", "OWNER_REJECT"): {"role": "OWNER", "department": "WHOLESALE", "next_status": "REJECTED"},
            ("OWNER_APPROVED", "STOREKEEPER_CONFIRM_STOCK"): {"role": "STORE_KEEPER", "department": "WHOLESALE", "next_status": "STOCK_CONFIRMED"},
            ("STOCK_CONFIRMED", "PHARMACIST_APPROVE"): {"role": "PHARMACIST", "department": "WHOLESALE", "next_status": "PHARMACIST_APPROVED"},
            ("STOCK_CONFIRMED", "PHARMACIST_REJECT"): {"role": "PHARMACIST", "department": "WHOLESALE", "next_status": "REJECTED"},
            ("AWAITING_PAYMENT", "RETAIL_PAY"): {"role": None, "department": "RETAIL", "next_status": "PAID_PENDING_CONFIRMATION"},
            ("PAID_PENDING_CONFIRMATION", "ACCOUNTANT_CONFIRM_PAYMENT"): {"role": "ACCOUNTANT", "department": "WHOLESALE", "next_status": "PAYMENT_CONFIRMED"},
            ("PAYMENT_CONFIRMED", "STOREKEEPER_PREPARE_ORDER"): {"role": "STORE_KEEPER", "department": "WHOLESALE", "next_status": "READY_FOR_DELIVERY"},
            ("READY_FOR_DELIVERY", "RETAIL_CONFIRM_RECEIVED"): {"role": None, "department": "RETAIL", "next_status": "DELIVERED"},
        }

        rule = transition_rules.get((rw_request.status, action))
        if not rule:
            return Response(
                {"detail": f"Invalid action '{action}' for current status '{rw_request.status}'"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if rule["department"] and actor_department != rule["department"]:
            return Response({"detail": "Department is not allowed for this action"}, status=status.HTTP_403_FORBIDDEN)

        if rule["role"] and actor_role != rule["role"]:
            return Response({"detail": "Role is not allowed for this action"}, status=status.HTTP_403_FORBIDDEN)

        if action in {"RETAIL_PAY", "RETAIL_CONFIRM_RECEIVED"} and rw_request.requested_by_id != request.user.id:
            return Response({"detail": "Only request creator can perform this action"}, status=status.HTTP_403_FORBIDDEN)

        if action == "RETAIL_PAY":
            estimated_total = _estimate_request_total(rw_request)
            payment_amount_raw = request.data.get("paidAmount")
            if rw_request.payment_option == "FULL":
                paid_amount = estimated_total
                if payment_amount_raw not in (None, ""):
                    try:
                        paid_amount = Decimal(str(payment_amount_raw))
                    except Exception:
                        return Response({"detail": "paidAmount must be a valid number"}, status=status.HTTP_400_BAD_REQUEST)
                    if paid_amount != estimated_total:
                        return Response(
                            {"detail": "FULL payment requests must be paid in full"},
                            status=status.HTTP_400_BAD_REQUEST,
                        )
            elif rw_request.payment_option == "PARTIAL":
                if payment_amount_raw in (None, ""):
                    return Response(
                        {"detail": "paidAmount is required for PARTIAL payment requests"},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                try:
                    paid_amount = Decimal(str(payment_amount_raw))
                except Exception:
                    return Response({"detail": "paidAmount must be a valid number"}, status=status.HTTP_400_BAD_REQUEST)
                if paid_amount <= 0 or paid_amount > estimated_total:
                    return Response(
                        {"detail": "paidAmount must be greater than 0 and not exceed total amount"},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
            else:
                return Response(
                    {"detail": "CREDIT requests do not require RETAIL_PAY action"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            payment_method = request.data.get("paymentMethod")
            rw_request.paid_amount = paid_amount
            if payment_method:
                rw_request.payment_method = payment_method
            rw_request.save(update_fields=["paid_amount", "payment_method", "updated_at"])

        rw_request.status = rule["next_status"]
        rw_request.decision_note = request.data.get("note")
        rw_request.decided_by = request.user
        rw_request.decided_at = timezone.now()
        rw_request.save(update_fields=["status", "decision_note", "decided_by", "decided_at", "updated_at"])

        if rw_request.status == "PHARMACIST_APPROVED":
            if rw_request.payment_option == "CREDIT":
                rw_request.status = "PAID_PENDING_CONFIRMATION"
            else:
                rw_request.status = "AWAITING_PAYMENT"
            rw_request.save(update_fields=["status", "updated_at"])
        elif rw_request.status == "DELIVERED":
            try:
                _finalize_completed_request(rw_request)
            except ValueError as exc:
                return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
            if rw_request.wholesale_sale_id and rw_request.wholesale_sale.due_amount <= 0:
                rw_request.status = "COMPLETED"
                rw_request.save(update_fields=["status", "updated_at"])

        notification_action = action
        if action == "PHARMACIST_APPROVE" and rw_request.payment_option == "CREDIT":
            notification_action = "PHARMACIST_APPROVE_CREDIT"

        _notify_request_transition(rw_request, notification_action)

        return Response(RetailWholesaleRequestSerializer(rw_request).data)
