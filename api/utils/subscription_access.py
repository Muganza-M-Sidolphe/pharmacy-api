from datetime import timedelta
import uuid

from django.utils import timezone

from ..models import SubscriptionPlan, Tenant, TenantSubscription, UserTenant
from .subscription_catalog import get_plan_by_id


def ensure_subscription(tenant):
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


def get_subscription_context(tenant):
    subscription = ensure_subscription(tenant)
    plan_record = SubscriptionPlan.objects.filter(code=subscription.plan_id).first()
    if not plan_record:
        try:
            plan_uuid = uuid.UUID(str(subscription.plan_id))
        except (TypeError, ValueError, AttributeError):
            plan_uuid = None
        if plan_uuid:
            plan_record = SubscriptionPlan.objects.filter(id=plan_uuid).first()
    catalog_plan = get_plan_by_id(subscription.plan_id) or get_plan_by_id("starter") or {}

    features = {}
    features.update(catalog_plan.get("features", {}))
    if plan_record and plan_record.features:
        features.update(plan_record.features)

    if plan_record:
        business_type = plan_record.business_type
    else:
        catalog_type = str(catalog_plan.get("type", "retail")).upper()
        business_type = "WHOLESALE" if catalog_type == "WHOLESALE" else "RETAIL"

    return {
        "subscription": subscription,
        "plan_record": plan_record,
        "catalog_plan": catalog_plan,
        "features": features,
        "business_type": business_type,
    }


def check_subscription_access(tenant, required_feature=None, allowed_business_types=None):
    context = get_subscription_context(tenant)
    subscription = context["subscription"]
    today = timezone.now().date()

    if subscription.status in {"EXPIRED", "CANCELLED"}:
        return False, "Subscription is not active for this tenant."

    if subscription.status == "TRIAL" and subscription.trial_end_date and subscription.trial_end_date < today:
        return False, "Trial subscription has expired."

    if subscription.status == "ACTIVE" and subscription.subscription_end_date and subscription.subscription_end_date < today:
        return False, "Subscription has expired."

    business_type = context["business_type"]
    if allowed_business_types and business_type not in allowed_business_types and business_type != "BOTH":
        return False, "Current subscription plan does not allow this business type."

    if required_feature and not bool(context["features"].get(required_feature, False)):
        return False, f"Current subscription plan does not include '{required_feature}'."

    return True, context


def get_subscription_limit(tenant, limit_name):
    context = get_subscription_context(tenant)
    plan_record = context["plan_record"]
    catalog_plan = context["catalog_plan"]
    limits = dict(catalog_plan.get("limits", {}))

    if plan_record:
        limits["users"] = plan_record.max_users
        limits["branches"] = plan_record.max_branches

    return limits.get(limit_name)


def authorize_tenant_access(
    request,
    tenant_id,
    *,
    required_role=None,
    required_feature=None,
    allowed_business_types=None,
):
    if not tenant_id:
        return None, "tenantId is required", 400

    membership_qs = UserTenant.objects.filter(user=request.user, tenant_id=tenant_id).select_related("tenant")
    if required_role:
        membership_qs = membership_qs.filter(role=required_role)

    membership = membership_qs.first()
    if not membership:
        return None, "Unauthorized tenant access", 403

    tenant = membership.tenant or Tenant.objects.filter(id=tenant_id).first()
    if not tenant:
        return None, "Tenant not found", 404

    allowed, details = check_subscription_access(
        tenant,
        required_feature=required_feature,
        allowed_business_types=allowed_business_types,
    )
    if not allowed:
        return None, details, 403

    return tenant, None, None
