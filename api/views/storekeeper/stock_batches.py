from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils.dateparse import parse_date
from rest_framework import status
from rest_framework.response import Response

from ...models import Medicine, StockBatch, StockMovement, UserTenant
from ...utils.permission_codes import user_has_tenant_permission
from .inventory import StorekeeperBaseView


def request_tenant_id(request):
    return (
        request.query_params.get("tenantId")
        or request.data.get("tenantId")
        or request.data.get("tenant_id")
    )


def visible_medicines(tenant_id, user):
    """Medicines on the user's side of the pharmacy (wholesale vs collaborative retail)."""
    qs = Medicine.objects.filter(tenant_id=tenant_id)
    if not UserTenant.objects.filter(tenant_id=tenant_id, role="OWNER").exists():
        return qs
    if user.department == "RETAIL":
        return qs.filter(created_by__department="RETAIL")
    return qs.exclude(created_by__department="RETAIL")


def serialize_movement(movement):
    user = movement.created_by
    return {
        "id": str(movement.id),
        "batchId": str(movement.batch_id),
        "type": movement.movement_type,
        "movement_type": movement.movement_type,
        "quantity": movement.quantity,
        "balance_after": movement.balance_after,
        "reason": movement.reason,
        "destination": movement.destination,
        "reference": movement.reference,
        "userName": user.name if user else None,
        "created_at": movement.created_at.isoformat(),
        "createdAt": movement.created_at.isoformat(),
    }


def record_movement(batch, tenant, user, movement_type, quantity, **extra):
    return StockMovement.objects.create(
        tenant=tenant,
        batch=batch,
        movement_type=movement_type,
        quantity=quantity,
        balance_after=batch.quantity,
        created_by=user,
        **extra,
    )


def _whole_number(value):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if number != number.to_integral_value():
        return None
    return int(number)


class StockPermissionView(StorekeeperBaseView):
    required_subscription_feature = "inventory_management"
    required_permission = None

    def _authorize_permission(self, request):
        """Return (tenant, error response)."""
        tenant_id = request_tenant_id(request)
        tenant, error = self._authorize(request, tenant_id)
        if error:
            return None, error
        if self.required_permission and not user_has_tenant_permission(request.user, tenant.id, self.required_permission):
            return None, Response(
                {"detail": f"You do not have the {self.required_permission} permission."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return tenant, None

    def _get_batch(self, tenant, batch_id, lock=False):
        qs = StockBatch.objects.select_related("medicine")
        if lock:
            qs = qs.select_for_update()
        return qs.filter(id=batch_id, medicine__tenant=tenant).first()


class StockInView(StockPermissionView):
    """Receive stock as a new batch of an existing medicine."""

    required_permission = "inventory.receive"

    def post(self, request):
        tenant, error = self._authorize_permission(request)
        if error:
            return error

        data = request.data
        medicine_id = data.get("medicineId") or data.get("medicine_id")
        batch_number = str(data.get("batchNumber") or data.get("batch_number") or "").strip()
        quantity = _whole_number(data.get("quantity"))
        try:
            purchase_price = Decimal(str(data.get("purchasePrice", data.get("purchase_price", 0)) or 0))
            selling_price = Decimal(str(data.get("sellingPrice", data.get("selling_price", 0)) or 0))
        except InvalidOperation:
            return Response({"detail": "Prices must be numbers."}, status=status.HTTP_400_BAD_REQUEST)
        manufacture_date = parse_date(str(data.get("manufactureDate") or data.get("manufacture_date") or "")) or None
        expiry_date = parse_date(str(data.get("expiryDate") or data.get("expiry_date") or "")) or None

        if not medicine_id:
            return Response({"detail": "medicineId is required."}, status=status.HTTP_400_BAD_REQUEST)
        if not batch_number:
            return Response({"detail": "Batch number is required."}, status=status.HTTP_400_BAD_REQUEST)
        if quantity is None or quantity <= 0:
            return Response({"detail": "Quantity must be a whole number above 0."}, status=status.HTTP_400_BAD_REQUEST)
        if purchase_price < 0 or selling_price < 0:
            return Response({"detail": "Prices cannot be negative."}, status=status.HTTP_400_BAD_REQUEST)
        if manufacture_date and expiry_date and manufacture_date > expiry_date:
            return Response({"detail": "Manufacturing date is after the expiry date."}, status=status.HTTP_400_BAD_REQUEST)

        medicine = visible_medicines(tenant.id, request.user).filter(id=medicine_id).first()
        if not medicine:
            return Response({"detail": "Medicine not found."}, status=status.HTTP_404_NOT_FOUND)
        if medicine.batches.filter(batch_number__iexact=batch_number).exists():
            return Response(
                {"detail": f"Batch {batch_number} already exists for this medicine. Use Adjust Stock to change its quantity."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            batch = StockBatch.objects.create(
                medicine=medicine,
                created_by=request.user,
                batch_number=batch_number,
                quantity=quantity,
                purchase_price=purchase_price,
                selling_price=selling_price,
                manufacture_date=manufacture_date,
                expiry_date=expiry_date,
                supplier_name=(data.get("supplierName") or data.get("supplier_name") or "").strip() or None,
            )
            movement = record_movement(batch, tenant, request.user, "RECEIVE", quantity)

        return Response({
            "message": "Stock received.",
            "batch": {
                "id": str(batch.id),
                "medicineId": str(medicine.id),
                "batch_number": batch.batch_number,
                "quantity": batch.quantity,
                "purchase_price": str(batch.purchase_price),
                "selling_price": str(batch.selling_price),
                "manufacture_date": batch.manufacture_date,
                "expiry_date": batch.expiry_date,
            },
            "movement": serialize_movement(movement),
        }, status=status.HTTP_201_CREATED)


class BatchMovementsView(StockPermissionView):
    required_permission = "inventory.view_movements"

    def get(self, request, batch_id):
        tenant, error = self._authorize_permission(request)
        if error:
            return error
        batch = self._get_batch(tenant, batch_id)
        if not batch:
            return Response({"detail": "Batch not found."}, status=status.HTTP_404_NOT_FOUND)
        movements = batch.movements.select_related("created_by")
        return Response({"results": [serialize_movement(m) for m in movements]})


class BatchAdjustView(StockPermissionView):
    """Add or remove stock on a batch (count corrections, damage, losses)."""

    required_permission = "inventory.adjust"

    def post(self, request, batch_id):
        tenant, error = self._authorize_permission(request)
        if error:
            return error
        quantity = _whole_number(request.data.get("quantity"))
        reason = str(request.data.get("reason") or "").strip()
        if not quantity:
            return Response({"detail": "Adjustment must be a whole number other than 0."}, status=status.HTTP_400_BAD_REQUEST)
        if not reason:
            return Response({"detail": "A reason is required."}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            batch = self._get_batch(tenant, batch_id, lock=True)
            if not batch:
                return Response({"detail": "Batch not found."}, status=status.HTTP_404_NOT_FOUND)
            if batch.quantity + quantity < 0:
                return Response(
                    {"detail": f"Cannot remove {-quantity}; only {batch.quantity} in this batch."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            batch.quantity += quantity
            batch.save(update_fields=["quantity"])
            movement = record_movement(
                batch, tenant, request.user, "ADJUSTMENT", quantity,
                reason=reason, reference=str(request.data.get("reference") or "").strip(),
            )

        return Response({
            "message": "Batch stock adjusted.",
            "batch": {"id": str(batch.id), "quantity": batch.quantity},
            "movement": serialize_movement(movement),
        })


class BatchTransferView(StockPermissionView):
    """Move stock out of a batch to another location (recorded, not tracked at the destination)."""

    required_permission = "inventory.transfer"

    def post(self, request, batch_id):
        tenant, error = self._authorize_permission(request)
        if error:
            return error
        quantity = _whole_number(request.data.get("quantity"))
        destination = str(request.data.get("destination") or "").strip()
        if quantity is None or quantity <= 0:
            return Response({"detail": "Quantity must be a whole number above 0."}, status=status.HTTP_400_BAD_REQUEST)
        if not destination:
            return Response({"detail": "Destination is required."}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            batch = self._get_batch(tenant, batch_id, lock=True)
            if not batch:
                return Response({"detail": "Batch not found."}, status=status.HTTP_404_NOT_FOUND)
            if quantity > batch.quantity:
                return Response(
                    {"detail": f"Cannot transfer {quantity}; only {batch.quantity} in this batch."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            batch.quantity -= quantity
            batch.save(update_fields=["quantity"])
            movement = record_movement(
                batch, tenant, request.user, "TRANSFER_OUT", -quantity,
                destination=destination,
                reference=str(request.data.get("reference") or "").strip(),
                reason=str(request.data.get("reason") or "").strip(),
            )

        return Response({
            "message": "Stock transfer recorded.",
            "batch": {"id": str(batch.id), "quantity": batch.quantity},
            "movement": serialize_movement(movement),
        })
