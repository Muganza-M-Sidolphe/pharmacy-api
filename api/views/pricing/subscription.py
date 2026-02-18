from datetime import timedelta
from decimal import Decimal

from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ...models import Medicine, Sale, SubscriptionEvent, TenantSubscription, UserTenant
from ...utils.subscription_catalog import PLAN_CATALOG, get_plan_by_id


PRICING_FAQS = [
    {
        "question": "Can I change my plan?",
        "answer": "Yes. You can upgrade or downgrade anytime from the pricing page.",
    },
    {
        "question": "Is there a trial period?",
        "answer": "New pharmacies start with a 30-day free trial by default.",
    },
    {
        "question": "Do annual plans include discount?",
        "answer": "Yes. Annual billing applies a 20% discount compared to monthly.",
    },
]


def _tenant_from_request(request):
    tenant_id = request.query_params.get("tenantId") or request.data.get("tenantId")
    if not tenant_id:
        token = getattr(request, "auth", None)
        if token and hasattr(token, "get"):
            tenant_id = token.get("tenant_id")

    if tenant_id:
        relation = UserTenant.objects.filter(user=request.user, tenant_id=tenant_id).select_related("tenant").first()
        if relation:
            return relation.tenant
        return None

    first_relation = UserTenant.objects.filter(user=request.user).select_related("tenant").first()
    return first_relation.tenant if first_relation else None


def _ensure_subscription(tenant):
    subscription, _ = TenantSubscription.objects.get_or_create(
        tenant=tenant,
        defaults={
            "plan_id": "starter",
            "status": "TRIAL",
            "billing_cycle": "monthly",
            "trial_end_date": timezone.now().date() + timedelta(days=30),
        },
    )
    return subscription


def _serialize_subscription(subscription):
    plan = get_plan_by_id(subscription.plan_id) or PLAN_CATALOG[0]
    today = timezone.now().date()
    status_value = subscription.status

    if status_value == "TRIAL" and subscription.trial_end_date and subscription.trial_end_date < today:
        status_value = "EXPIRED"

    trial_days_remaining = 0
    if subscription.trial_end_date and status_value == "TRIAL":
        trial_days_remaining = max(0, (subscription.trial_end_date - today).days)

    return {
        "plan": plan,
        "status": status_value.lower(),
        "trialDaysRemaining": trial_days_remaining,
        "trialEndDate": subscription.trial_end_date.isoformat() if subscription.trial_end_date else None,
        "subscriptionStartDate": (
            subscription.subscription_start_date.isoformat()
            if subscription.subscription_start_date
            else None
        ),
        "subscriptionEndDate": (
            subscription.subscription_end_date.isoformat()
            if subscription.subscription_end_date
            else None
        ),
        "features": plan.get("features", {}),
        "limits": plan.get("limits", {}),
        "plans": PLAN_CATALOG,
    }


class PricingPlansView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        return Response(
            {
                "success": True,
                "data": {
                    "plans": PLAN_CATALOG,
                },
            }
        )


class PricingCompareView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        return Response(
            {
                "success": True,
                "data": {
                    "plans": PLAN_CATALOG,
                },
            }
        )


class PricingFAQView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        return Response({"success": True, "data": PRICING_FAQS})


class PricingCalculateUpgradeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        target_plan_id = request.query_params.get("targetPlanId")
        target_plan = get_plan_by_id(target_plan_id)
        if not target_plan:
            return Response(
                {"success": False, "message": "Invalid target plan"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        tenant = _tenant_from_request(request)
        if not tenant:
            return Response(
                {"success": False, "message": "No tenant context found"},
                status=status.HTTP_403_FORBIDDEN,
            )

        subscription = _ensure_subscription(tenant)
        current_plan = get_plan_by_id(subscription.plan_id) or PLAN_CATALOG[0]

        current_price = Decimal(str(current_plan["pricing"]["monthly"]["amount"]))
        target_price = Decimal(str(target_plan["pricing"]["monthly"]["amount"]))
        difference = target_price - current_price

        return Response(
            {
                "success": True,
                "data": {
                    "currentPlanId": current_plan["id"],
                    "targetPlanId": target_plan["id"],
                    "amountDifference": str(difference),
                    "currency": target_plan["pricing"]["monthly"]["currency"],
                    "isUpgrade": difference > 0,
                },
            }
        )


class PricingRecommendationView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        branch_count = int(request.query_params.get("branches", 1))
        user_count = int(request.query_params.get("users", 3))

        if branch_count > 5 or user_count > 20:
            recommended = get_plan_by_id("enterprise")
        elif branch_count > 1 or user_count > 5:
            recommended = get_plan_by_id("growth")
        else:
            recommended = get_plan_by_id("starter")

        return Response({"success": True, "data": recommended})


class SubscriptionPlansView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        tenant = _tenant_from_request(request)
        if not tenant:
            return Response(
                {"success": False, "message": "No tenant context found"},
                status=status.HTTP_403_FORBIDDEN,
            )

        subscription = _ensure_subscription(tenant)
        return Response({"success": True, "data": _serialize_subscription(subscription)})


class SubscriptionPlanDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, plan_id):
        plan = get_plan_by_id(plan_id)
        if not plan:
            return Response(
                {"success": False, "message": "Plan not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response({"success": True, "data": plan})


class SubscriptionHistoryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        tenant = _tenant_from_request(request)
        if not tenant:
            return Response({"success": True, "data": []})

        events = (
            SubscriptionEvent.objects.filter(tenant=tenant)
            .order_by("-created_at")
            .values(
                "id",
                "action",
                "from_plan_id",
                "to_plan_id",
                "payment_method",
                "promo_code",
                "amount",
                "created_at",
            )
        )
        data = []
        for event in events:
            data.append(
                {
                    "id": str(event["id"]),
                    "action": event["action"],
                    "fromPlanId": event["from_plan_id"],
                    "toPlanId": event["to_plan_id"],
                    "paymentMethod": event["payment_method"],
                    "promoCode": event["promo_code"],
                    "amount": str(event["amount"]),
                    "createdAt": event["created_at"].isoformat(),
                }
            )
        return Response({"success": True, "data": data})


class SubscriptionUpgradeView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        plan_id = request.data.get("planId")
        payment_method = request.data.get("paymentMethod", "card")
        promo_code = request.data.get("promoCode")
        if payment_method not in {"card", "cash", "bank", "mtn_momo", "airtel_money"}:
            promo_code = promo_code or payment_method
            payment_method = "card"
        target_plan = get_plan_by_id(plan_id)
        if not target_plan:
            return Response(
                {"success": False, "message": "Invalid planId"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        tenant = _tenant_from_request(request)
        if not tenant:
            return Response(
                {"success": False, "message": "No tenant context found"},
                status=status.HTTP_403_FORBIDDEN,
            )

        subscription = _ensure_subscription(tenant)
        previous_plan_id = subscription.plan_id
        today = timezone.now().date()

        subscription.plan_id = plan_id
        subscription.status = "ACTIVE"
        subscription.subscription_start_date = today
        subscription.subscription_end_date = today + timedelta(days=365)
        subscription.cancelled_at = None
        subscription.save(update_fields=["plan_id", "status", "subscription_start_date", "subscription_end_date", "cancelled_at", "updated_at"])

        SubscriptionEvent.objects.create(
            tenant=tenant,
            action="UPGRADE",
            from_plan_id=previous_plan_id,
            to_plan_id=plan_id,
            payment_method=payment_method,
            promo_code=promo_code,
            amount=Decimal(str(target_plan["pricing"]["monthly"]["amount"])),
            created_by=request.user,
        )

        return Response(
            {
                "success": True,
                "message": "Subscription upgraded successfully",
                "data": _serialize_subscription(subscription),
            }
        )


class SubscriptionDowngradeView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        plan_id = request.data.get("planId")
        target_plan = get_plan_by_id(plan_id)
        if not target_plan:
            return Response(
                {"success": False, "message": "Invalid planId"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        tenant = _tenant_from_request(request)
        if not tenant:
            return Response(
                {"success": False, "message": "No tenant context found"},
                status=status.HTTP_403_FORBIDDEN,
            )

        subscription = _ensure_subscription(tenant)
        previous_plan_id = subscription.plan_id
        today = timezone.now().date()

        subscription.plan_id = plan_id
        subscription.status = "ACTIVE"
        subscription.subscription_start_date = subscription.subscription_start_date or today
        subscription.subscription_end_date = subscription.subscription_end_date or (today + timedelta(days=365))
        subscription.cancelled_at = None
        subscription.save(update_fields=["plan_id", "status", "subscription_start_date", "subscription_end_date", "cancelled_at", "updated_at"])

        SubscriptionEvent.objects.create(
            tenant=tenant,
            action="DOWNGRADE",
            from_plan_id=previous_plan_id,
            to_plan_id=plan_id,
            created_by=request.user,
        )

        return Response(
            {
                "success": True,
                "message": "Subscription downgraded successfully",
                "data": _serialize_subscription(subscription),
            }
        )


class SubscriptionCancelView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        reason = request.data.get("reason", "")
        tenant = _tenant_from_request(request)
        if not tenant:
            return Response(
                {"success": False, "message": "No tenant context found"},
                status=status.HTTP_403_FORBIDDEN,
            )

        subscription = _ensure_subscription(tenant)
        subscription.status = "CANCELLED"
        subscription.cancelled_at = timezone.now()
        subscription.save(update_fields=["status", "cancelled_at", "updated_at"])

        SubscriptionEvent.objects.create(
            tenant=tenant,
            action="CANCEL",
            from_plan_id=subscription.plan_id,
            to_plan_id=subscription.plan_id,
            metadata={"reason": reason},
            created_by=request.user,
        )

        return Response({"success": True, "message": "Subscription cancelled"})


class SubscriptionTrialRenewView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        tenant = _tenant_from_request(request)
        if not tenant:
            return Response(
                {"success": False, "message": "No tenant context found"},
                status=status.HTTP_403_FORBIDDEN,
            )

        subscription = _ensure_subscription(tenant)
        subscription.status = "TRIAL"
        subscription.trial_end_date = timezone.now().date() + timedelta(days=30)
        subscription.subscription_start_date = None
        subscription.subscription_end_date = None
        subscription.save(
            update_fields=[
                "status",
                "trial_end_date",
                "subscription_start_date",
                "subscription_end_date",
                "updated_at",
            ]
        )

        SubscriptionEvent.objects.create(
            tenant=tenant,
            action="RENEW_TRIAL",
            from_plan_id=subscription.plan_id,
            to_plan_id=subscription.plan_id,
            created_by=request.user,
        )

        return Response({"success": True, "data": _serialize_subscription(subscription)})


class SubscriptionTrialStatusView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        tenant = _tenant_from_request(request)
        if not tenant:
            return Response({"success": True, "data": {"isTrial": False, "daysRemaining": 0}})

        subscription = _ensure_subscription(tenant)
        payload = _serialize_subscription(subscription)
        return Response(
            {
                "success": True,
                "data": {
                    "isTrial": payload["status"] == "trial",
                    "daysRemaining": payload["trialDaysRemaining"],
                    "trialEndDate": payload["trialEndDate"],
                },
            }
        )


class SubscriptionLimitsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        tenant = _tenant_from_request(request)
        if not tenant:
            return Response({"success": True, "data": {}})

        subscription = _ensure_subscription(tenant)
        plan = get_plan_by_id(subscription.plan_id) or PLAN_CATALOG[0]
        return Response({"success": True, "data": plan.get("limits", {})})


class SubscriptionFeatureView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, feature_name):
        tenant = _tenant_from_request(request)
        if not tenant:
            return Response({"success": True, "data": {"available": False}})

        subscription = _ensure_subscription(tenant)
        plan = get_plan_by_id(subscription.plan_id) or PLAN_CATALOG[0]
        available = bool(plan.get("features", {}).get(feature_name, False))
        return Response(
            {
                "success": True,
                "data": {"feature": feature_name, "available": available},
            }
        )


class SubscriptionUsageView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        tenant = _tenant_from_request(request)
        if not tenant:
            return Response({"success": True, "data": {}})

        usage = {
            "users": UserTenant.objects.filter(tenant=tenant).count(),
            "branches": 1,
            "medicines": Medicine.objects.filter(tenant=tenant).count(),
            "transactions": Sale.objects.filter(tenant=tenant).count(),
        }
        return Response({"success": True, "data": usage})


class SubscriptionPaymentView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        plan_id = request.data.get("planId")
        payment_method = request.data.get("paymentMethod", "card")
        plan = get_plan_by_id(plan_id)
        if not plan:
            return Response(
                {"success": False, "message": "Invalid planId"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        tenant = _tenant_from_request(request)
        if not tenant:
            return Response(
                {"success": False, "message": "No tenant context found"},
                status=status.HTTP_403_FORBIDDEN,
            )

        event = SubscriptionEvent.objects.create(
            tenant=tenant,
            action="PAYMENT",
            from_plan_id=plan_id,
            to_plan_id=plan_id,
            payment_method=payment_method,
            amount=Decimal(str(plan["pricing"]["monthly"]["amount"])),
            metadata={"provider": request.data.get("provider"), "reference": request.data.get("reference")},
            created_by=request.user,
        )
        return Response({"success": True, "data": {"invoiceId": str(event.id)}})


class SubscriptionInvoicesView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        tenant = _tenant_from_request(request)
        if not tenant:
            return Response({"success": True, "data": []})

        payments = (
            SubscriptionEvent.objects.filter(tenant=tenant, action__in=["PAYMENT", "UPGRADE"])
            .order_by("-created_at")
            .values("id", "action", "amount", "payment_method", "created_at")
        )

        data = [
            {
                "id": str(p["id"]),
                "type": p["action"],
                "amount": str(p["amount"]),
                "paymentMethod": p["payment_method"],
                "issuedAt": p["created_at"].isoformat(),
            }
            for p in payments
        ]
        return Response({"success": True, "data": data})


class SubscriptionInvoiceDownloadView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, invoice_id):
        content = f"Invoice {invoice_id}\nGenerated at: {timezone.now().isoformat()}\n"
        response = HttpResponse(content, content_type="text/plain")
        response["Content-Disposition"] = f'attachment; filename="invoice-{invoice_id}.txt"'
        return response
