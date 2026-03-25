from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ...models import Medicine, RetailWholesaleRequest, RetailWholesaleRequestItem, StockBatch, UserTenant
from ...serializers import (
    CreateRetailWholesaleRequestSerializer,
    RetailWholesaleRequestSerializer,
)
from ...utils.subscription_access import check_subscription_access


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


def _sync_completed_request_to_retail_inventory(rw_request):
    if not rw_request.requested_by_id:
        return

    if StockBatch.objects.filter(source_request=rw_request, created_by=rw_request.requested_by).exists():
        return

    request_items = rw_request.items.select_related("medicine").all()
    for index, item in enumerate(request_items, start=1):
        source_medicine = item.medicine
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

        latest_source_batch = source_medicine.batches.order_by("-created_at").first()
        StockBatch.objects.create(
            medicine=target_medicine,
            source_request=rw_request,
            created_by=rw_request.requested_by,
            batch_number=f"REQ-{str(rw_request.id)[:8]}-{index}",
            quantity=item.quantity,
            purchase_price=latest_source_batch.purchase_price if latest_source_batch else 0,
            selling_price=latest_source_batch.selling_price if latest_source_batch else 0,
            manufacture_date=latest_source_batch.manufacture_date if latest_source_batch else None,
            expiry_date=latest_source_batch.expiry_date if latest_source_batch else None,
            supplier_name=rw_request.wholesale_tenant.name,
            supplier_phone=rw_request.wholesale_tenant.phone,
            supplier_address=rw_request.wholesale_tenant.address,
        )


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

        rw_request.status = rule["next_status"]
        rw_request.decision_note = request.data.get("note")
        rw_request.decided_by = request.user
        rw_request.decided_at = timezone.now()
        rw_request.save(update_fields=["status", "decision_note", "decided_by", "decided_at", "updated_at"])

        if rw_request.status == "PHARMACIST_APPROVED":
            rw_request.status = "AWAITING_PAYMENT"
            rw_request.save(update_fields=["status", "updated_at"])
        elif rw_request.status == "DELIVERED":
            rw_request.status = "COMPLETED"
            rw_request.save(update_fields=["status", "updated_at"])
            _sync_completed_request_to_retail_inventory(rw_request)

        return Response(RetailWholesaleRequestSerializer(rw_request).data)
