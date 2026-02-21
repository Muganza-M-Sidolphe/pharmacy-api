import os
import uuid
from datetime import timedelta
from decimal import Decimal

from django.http import HttpResponse
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ...models import (
    Medicine,
    Sale,
    SubscriptionEvent,
    SubscriptionPaymentTransaction,
    TenantSubscription,
    UserTenant,
)
from ...utils.subscription_catalog import PLAN_CATALOG, get_plan_by_id
from ...utils.mtn_momo import MtnMomoError, get_collection_status, initiate_collection


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


def _resolve_plan_pricing(plan, billing_cycle):
    normalized_cycle = billing_cycle if billing_cycle in {"monthly", "annual"} else "monthly"
    pricing = plan.get("pricing", {}).get(normalized_cycle, {})
    amount = Decimal(str(pricing.get("amount", 0)))
    currency = pricing.get("currency", "RWF")
    return normalized_cycle, amount, currency


def _apply_successful_subscription_payment(transaction, source="payment"):
    subscription = _ensure_subscription(transaction.tenant)
    previous_plan_id = subscription.plan_id
    today = timezone.now().date()
    duration_days = 365 if transaction.billing_cycle == "annual" else 30

    subscription.plan_id = transaction.plan_id
    subscription.status = "ACTIVE"
    subscription.billing_cycle = transaction.billing_cycle
    subscription.subscription_start_date = today
    subscription.subscription_end_date = today + timedelta(days=duration_days)
    subscription.cancelled_at = None
    subscription.save(
        update_fields=[
            "plan_id",
            "status",
            "billing_cycle",
            "subscription_start_date",
            "subscription_end_date",
            "cancelled_at",
            "updated_at",
        ]
    )

    if transaction.event_id:
        return subscription

    event = SubscriptionEvent.objects.create(
        tenant=transaction.tenant,
        action="PAYMENT",
        from_plan_id=previous_plan_id,
        to_plan_id=transaction.plan_id,
        payment_method=transaction.payment_method,
        amount=transaction.amount,
        metadata={
            "provider": transaction.provider,
            "referenceId": transaction.reference_id,
            "providerStatus": transaction.provider_status,
            "source": source,
            "billingCycle": transaction.billing_cycle,
            "currency": transaction.currency,
        },
        created_by=transaction.created_by,
    )
    transaction.event = event
    transaction.save(update_fields=["event", "updated_at"])
    return subscription


def _normalize_provider_status(raw_status):
    value = (raw_status or "").upper()
    if value in {"SUCCESSFUL", "SUCCESS", "COMPLETED"}:
        return "SUCCESS"
    if value in {"FAILED", "FAIL", "REJECTED", "TIMEOUT", "CANCELLED"}:
        return "FAILED"
    if value in {"PENDING", "PROCESSING"}:
        return "PENDING"
    return "PENDING"


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
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        target_plan_id = request.query_params.get("targetPlanId")
        target_plan = get_plan_by_id(target_plan_id)
        if not target_plan:
            return Response(
                {"success": False, "message": "Invalid target plan"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        current_plan_id = request.query_params.get("currentPlanId")
        tenant = _tenant_from_request(request)
        if tenant:
            subscription = _ensure_subscription(tenant)
            current_plan = get_plan_by_id(subscription.plan_id) or PLAN_CATALOG[0]
        else:
            current_plan = get_plan_by_id(current_plan_id or "starter") or PLAN_CATALOG[0]

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
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        tenant = _tenant_from_request(request)
        if not tenant:
            default_plan = get_plan_by_id("starter") or PLAN_CATALOG[0]
            return Response(
                {
                    "success": True,
                    "data": {
                        "plan": default_plan,
                        "status": "public",
                        "trialDaysRemaining": 0,
                        "trialEndDate": None,
                        "subscriptionStartDate": None,
                        "subscriptionEndDate": None,
                        "features": default_plan.get("features", {}),
                        "limits": default_plan.get("limits", {}),
                        "plans": PLAN_CATALOG,
                    },
                }
            )

        subscription = _ensure_subscription(tenant)
        return Response({"success": True, "data": _serialize_subscription(subscription)})


class SubscriptionPlanDetailView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

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
        billing_cycle = request.data.get("billingCycle", "monthly")
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

        billing_cycle, amount, currency = _resolve_plan_pricing(plan, billing_cycle)

        if payment_method == "mtn_momo":
            phone_number = str(request.data.get("phoneNumber", "")).strip()
            if not phone_number:
                return Response(
                    {"success": False, "message": "phoneNumber is required for MTN MoMo payments"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            callback_url = (
                request.data.get("callbackUrl")
                or os.getenv("MTN_MOMO_CALLBACK_URL")
                or request.build_absolute_uri(reverse("subscriptions-payment-mtn-webhook"))
            )
            external_id = (
                request.data.get("externalId")
                or f"SUB-{tenant.id}-{uuid.uuid4().hex[:8]}"
            )

            transaction = SubscriptionPaymentTransaction.objects.create(
                tenant=tenant,
                plan_id=plan_id,
                billing_cycle=billing_cycle,
                amount=amount,
                currency=currency,
                payment_method=payment_method,
                provider="MTN_MOMO",
                phone_number=phone_number,
                external_id=external_id,
                status="PENDING",
                created_by=request.user,
            )

            try:
                provider_response = initiate_collection(
                    amount=amount,
                    currency=currency,
                    phone_number=phone_number,
                    external_id=external_id,
                    callback_url=callback_url,
                    payer_message=f"Subscription {plan_id}",
                    payee_note=f"{billing_cycle} subscription payment",
                )
            except MtnMomoError as exc:
                transaction.status = "FAILED"
                transaction.failure_reason = str(exc)
                transaction.save(update_fields=["status", "failure_reason", "updated_at"])
                return Response(
                    {"success": False, "message": str(exc)},
                    status=status.HTTP_502_BAD_GATEWAY,
                )

            transaction.reference_id = provider_response["reference_id"]
            transaction.provider_status = "PENDING"
            transaction.provider_payload = provider_response.get("response_payload") or {}
            transaction.save(update_fields=["reference_id", "provider_status", "provider_payload", "updated_at"])

            return Response(
                {
                    "success": True,
                    "message": "MTN payment initiated. Customer should approve the request on phone.",
                    "data": {
                        "transactionId": str(transaction.id),
                        "referenceId": transaction.reference_id,
                        "status": transaction.status,
                        "providerStatus": transaction.provider_status,
                        "amount": str(transaction.amount),
                        "currency": transaction.currency,
                        "planId": plan_id,
                        "billingCycle": billing_cycle,
                    },
                },
                status=status.HTTP_202_ACCEPTED,
            )

        # Fallback non-mobile methods remain immediate
        event = SubscriptionEvent.objects.create(
            tenant=tenant,
            action="PAYMENT",
            from_plan_id=plan_id,
            to_plan_id=plan_id,
            payment_method=payment_method,
            amount=amount,
            metadata={"provider": request.data.get("provider"), "reference": request.data.get("reference")},
            created_by=request.user,
        )
        return Response({"success": True, "data": {"invoiceId": str(event.id)}})


class SubscriptionPaymentStatusView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, transaction_id):
        tenant = _tenant_from_request(request)
        if not tenant:
            return Response(
                {"success": False, "message": "No tenant context found"},
                status=status.HTTP_403_FORBIDDEN,
            )

        transaction = SubscriptionPaymentTransaction.objects.filter(
            id=transaction_id,
            tenant=tenant,
        ).first()
        if not transaction:
            return Response(
                {"success": False, "message": "Transaction not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        if transaction.provider == "MTN_MOMO" and transaction.status == "PENDING" and transaction.reference_id:
            try:
                provider_payload = get_collection_status(transaction.reference_id)
                provider_status = provider_payload.get("status")
                normalized = _normalize_provider_status(provider_status)
                transaction.provider_payload = provider_payload
                transaction.provider_status = provider_status

                if normalized == "SUCCESS":
                    transaction.status = "SUCCESS"
                    transaction.failure_reason = None
                    transaction.paid_at = transaction.paid_at or timezone.now()
                    transaction.provider_transaction_id = (
                        provider_payload.get("financialTransactionId")
                        or transaction.provider_transaction_id
                    )
                    transaction.save(
                        update_fields=[
                            "provider_payload",
                            "provider_status",
                            "status",
                            "failure_reason",
                            "paid_at",
                            "provider_transaction_id",
                            "updated_at",
                        ]
                    )
                    _apply_successful_subscription_payment(transaction, source="status_poll")
                elif normalized == "FAILED":
                    transaction.status = "FAILED"
                    transaction.failure_reason = (
                        provider_payload.get("reason")
                        or provider_payload.get("reasonCode")
                        or "Payment failed"
                    )
                    transaction.provider_transaction_id = (
                        provider_payload.get("financialTransactionId")
                        or transaction.provider_transaction_id
                    )
                    transaction.save(
                        update_fields=[
                            "provider_payload",
                            "provider_status",
                            "status",
                            "failure_reason",
                            "provider_transaction_id",
                            "updated_at",
                        ]
                    )
                else:
                    transaction.save(update_fields=["provider_payload", "provider_status", "updated_at"])
            except MtnMomoError:
                # Keep transaction pending if provider status cannot be fetched.
                pass

        return Response(
            {
                "success": True,
                "data": {
                    "transactionId": str(transaction.id),
                    "status": transaction.status,
                    "provider": transaction.provider,
                    "providerStatus": transaction.provider_status,
                    "referenceId": transaction.reference_id,
                    "planId": transaction.plan_id,
                    "billingCycle": transaction.billing_cycle,
                    "amount": str(transaction.amount),
                    "currency": transaction.currency,
                    "paidAt": transaction.paid_at.isoformat() if transaction.paid_at else None,
                    "failureReason": transaction.failure_reason,
                },
            }
        )


class SubscriptionMtnWebhookView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        payload = request.data if isinstance(request.data, dict) else {}
        reference_id = (
            request.headers.get("X-Reference-Id")
            or payload.get("referenceId")
            or request.query_params.get("referenceId")
        )
        if not reference_id:
            return Response({"success": False, "message": "referenceId is required"}, status=status.HTTP_400_BAD_REQUEST)

        transaction = SubscriptionPaymentTransaction.objects.filter(reference_id=reference_id).first()
        if not transaction:
            return Response({"success": True, "message": "No matching transaction"})

        provider_status = payload.get("status") or payload.get("providerStatus")
        normalized = _normalize_provider_status(provider_status)

        transaction.callback_payload = payload
        transaction.provider_status = provider_status or transaction.provider_status
        transaction.provider_transaction_id = (
            payload.get("financialTransactionId")
            or payload.get("providerTransactionId")
            or transaction.provider_transaction_id
        )

        if normalized == "SUCCESS":
            if transaction.status != "SUCCESS":
                transaction.status = "SUCCESS"
                transaction.failure_reason = None
                transaction.paid_at = transaction.paid_at or timezone.now()
                transaction.save(
                    update_fields=[
                        "callback_payload",
                        "provider_status",
                        "provider_transaction_id",
                        "status",
                        "failure_reason",
                        "paid_at",
                        "updated_at",
                    ]
                )
                _apply_successful_subscription_payment(transaction, source="webhook")
            else:
                transaction.save(update_fields=["callback_payload", "provider_status", "provider_transaction_id", "updated_at"])
        elif normalized == "FAILED":
            transaction.status = "FAILED"
            transaction.failure_reason = (
                payload.get("reason")
                or payload.get("reasonCode")
                or transaction.failure_reason
                or "Payment failed"
            )
            transaction.save(
                update_fields=[
                    "callback_payload",
                    "provider_status",
                    "provider_transaction_id",
                    "status",
                    "failure_reason",
                    "updated_at",
                ]
            )
        else:
            transaction.status = "PENDING"
            transaction.save(
                update_fields=[
                    "callback_payload",
                    "provider_status",
                    "provider_transaction_id",
                    "status",
                    "updated_at",
                ]
            )

        return Response({"success": True, "message": "Webhook processed"})


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
        response["Access-Control-Expose-Headers"] = "Content-Disposition"
        return response
