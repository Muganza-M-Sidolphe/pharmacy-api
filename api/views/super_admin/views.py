from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ...models import (
    AuditLog,
    Sale,
    SubscriptionPlan,
    SystemSetting,
    Tenant,
    TenantSubscription,
    User,
    UserTenant,
)
from ...utils.subscription_catalog import PLAN_CATALOG


def _get_ip(request):
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    return xff.split(",")[0].strip() if xff else request.META.get("REMOTE_ADDR")


def _ensure_default_plans():
    for plan in PLAN_CATALOG:
        features = plan.get("features", {})
        limits = plan.get("limits", {})
        business_type = "WHOLESALE" if plan.get("type") == "wholesale" else "RETAIL"
        SubscriptionPlan.objects.get_or_create(
            code=plan["id"],
            defaults={
                "name": plan["name"],
                "description": plan.get("description"),
                "price": Decimal(str(plan.get("pricing", {}).get("monthly", {}).get("amount", 0))),
                "business_type": business_type,
                "max_users": limits.get("users", 1),
                "max_branches": limits.get("branches", 1),
                "features": features,
                "is_active": True,
            },
        )


def _tenant_business_type(tenant):
    owner_roles = UserTenant.objects.filter(tenant=tenant).values_list("role", flat=True)
    if "OWNER" in owner_roles:
        return "WHOLESALE"
    if "PHARMACIST" in owner_roles:
        return "RETAIL"
    return "BOTH"


def _ensure_subscriptions_for_tenants():
    today = timezone.now().date()
    for tenant in Tenant.objects.all():
        TenantSubscription.objects.get_or_create(
            tenant=tenant,
            defaults={
                "plan_id": "starter",
                "status": "TRIAL",
                "billing_cycle": "monthly",
                "trial_end_date": today + timedelta(days=30),
            },
        )


def _plan_payload(plan):
    return {
        "id": str(plan.id),
        "code": plan.code,
        "name": plan.name,
        "description": plan.description,
        "price": str(plan.price),
        "business_type": plan.business_type,
        "max_users": plan.max_users,
        "max_branches": plan.max_branches,
        "features": plan.features or {},
        "is_active": plan.is_active,
        "createdAt": plan.created_at.isoformat(),
        "updatedAt": plan.updated_at.isoformat(),
    }


def _subscription_payload(subscription):
    plan = (
        SubscriptionPlan.objects.filter(code=subscription.plan_id).first()
        or SubscriptionPlan.objects.filter(id=subscription.plan_id).first()
    )
    amount_paid = (
        Sale.objects.filter(tenant=subscription.tenant)
        .aggregate(total=Coalesce(Sum("paid_amount"), Decimal("0.00")))
        .get("total")
        or Decimal("0.00")
    )
    return {
        "id": subscription.id,
        "subscription_number": f"SUB-{subscription.id}",
        "status": subscription.status,
        "billing_cycle": subscription.billing_cycle,
        "trial_start_date": subscription.created_at.date().isoformat(),
        "trial_end_date": subscription.trial_end_date.isoformat() if subscription.trial_end_date else None,
        "amount_paid": str(amount_paid),
        "createdAt": subscription.created_at.isoformat(),
        "Tenant": {
            "id": str(subscription.tenant.id),
            "name": subscription.tenant.name,
            "business_type": _tenant_business_type(subscription.tenant),
        },
        "Plan": _plan_payload(plan) if plan else None,
    }


def _tenant_payload(tenant):
    subscription = getattr(tenant, "subscription", None)
    plan = None
    if subscription:
        plan = SubscriptionPlan.objects.filter(code=subscription.plan_id).first()

    return {
        "id": str(tenant.id),
        "name": tenant.name,
        "email": tenant.email,
        "phone": tenant.phone,
        "address": tenant.address,
        "license_number": tenant.license_number,
        "business_type": _tenant_business_type(tenant),
        "is_active": tenant.is_active,
        "createdAt": tenant.created_at.isoformat(),
        "updatedAt": tenant.created_at.isoformat(),
        "user_count": UserTenant.objects.filter(tenant=tenant).count(),
        "product_count": tenant.medicine_set.count(),
        "Subscription": (
            {
                "id": subscription.id,
                "status": subscription.status,
                "billing_cycle": subscription.billing_cycle,
                "trial_end_date": subscription.trial_end_date.isoformat() if subscription.trial_end_date else None,
                "createdAt": subscription.created_at.isoformat(),
                "Plan": _plan_payload(plan) if plan else None,
            }
            if subscription
            else None
        ),
    }


class SuperAdminBaseView(APIView):
    permission_classes = [IsAuthenticated]

    def _check(self, request):
        if not request.user.is_super_admin:
            return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)
        return None

    def _log(self, request, action, entity, status_value="SUCCESS", details=None, tenant=None, status_code=None, error_message=None):
        AuditLog.objects.create(
            user=request.user,
            tenant=tenant,
            action=action,
            entity=entity,
            status=status_value,
            details=details or {},
            ip_address=_get_ip(request),
            status_code=status_code,
            error_message=error_message,
        )


class SuperAdminMetricsView(SuperAdminBaseView):
    def get(self, request):
        denied = self._check(request)
        if denied:
            return denied

        _ensure_default_plans()
        _ensure_subscriptions_for_tenants()
        total_tenants = Tenant.objects.count()
        active_tenants = Tenant.objects.filter(is_active=True).count()
        inactive_tenants = total_tenants - active_tenants

        subs = TenantSubscription.objects.all()
        trials = subs.filter(status="TRIAL").count()
        active_subs = subs.filter(status="ACTIVE").count()
        expired_subs = subs.filter(status="EXPIRED").count()

        recent_activity = AuditLog.objects.filter(created_at__gte=timezone.now() - timedelta(hours=24)).count()
        total_users = User.objects.count()

        subscription_breakdown = [
            {"status": "TRIAL", "count": str(trials)},
            {"status": "ACTIVE", "count": str(active_subs)},
            {"status": "EXPIRED", "count": str(expired_subs)},
            {"status": "CANCELLED", "count": str(subs.filter(status='CANCELLED').count())},
        ]

        business_type_distribution = list(
            UserTenant.objects.values("role").annotate(count=Count("id")).order_by("-count")
        )
        trial_metrics = [{"label": "Trials ending in 7 days", "count": subs.filter(status="TRIAL", trial_end_date__lte=timezone.now().date() + timedelta(days=7)).count()}]

        payload = {
            "summary": {
                "totalTenants": total_tenants,
                "activeTenants": active_tenants,
                "inactiveTenants": inactive_tenants,
                "trialsCount": trials,
                "activeSubscriptions": active_subs,
                "expiredSubscriptions": expired_subs,
                "totalUsers": total_users,
                "recentActivity24h": recent_activity,
            },
            "subscriptionBreakdown": subscription_breakdown,
            "businessTypeDistribution": business_type_distribution,
            "trialMetrics": trial_metrics,
            "timestamp": timezone.now().isoformat(),
        }
        self._log(request, "VIEW", "METRICS")
        return Response({"data": payload})


class SuperAdminTenantsView(SuperAdminBaseView):
    def get(self, request):
        denied = self._check(request)
        if denied:
            return denied

        _ensure_subscriptions_for_tenants()
        page = max(1, int(request.query_params.get("page", 1)))
        limit = max(1, int(request.query_params.get("limit", 20)))
        search = request.query_params.get("search", "").strip()

        qs = Tenant.objects.all().order_by("-created_at")
        if search:
            qs = qs.filter(
                Q(name__icontains=search)
                | Q(email__icontains=search)
                | Q(phone__icontains=search)
                | Q(license_number__icontains=search)
            )

        total = qs.count()
        start = (page - 1) * limit
        end = start + limit
        data = [_tenant_payload(t) for t in qs[start:end]]
        self._log(request, "VIEW", "TENANT_LIST", details={"page": page, "search": search})
        return Response({"data": data, "pagination": {"total": total, "page": page, "limit": limit, "pages": (total + limit - 1) // limit}})


class SuperAdminTenantDetailView(SuperAdminBaseView):
    def get(self, request, tenant_id):
        denied = self._check(request)
        if denied:
            return denied
        _ensure_subscriptions_for_tenants()
        tenant = Tenant.objects.filter(id=tenant_id).first()
        if not tenant:
            return Response({"detail": "Tenant not found"}, status=404)
        self._log(request, "VIEW", "TENANT_DETAIL", tenant=tenant, details={"tenant_id": tenant_id})
        return Response({"data": _tenant_payload(tenant)})


class SuperAdminTenantStatusView(SuperAdminBaseView):
    def patch(self, request, tenant_id):
        denied = self._check(request)
        if denied:
            return denied
        tenant = Tenant.objects.filter(id=tenant_id).first()
        if not tenant:
            return Response({"detail": "Tenant not found"}, status=404)
        is_active = bool(request.data.get("is_active", tenant.is_active))
        tenant.is_active = is_active
        tenant.save(update_fields=["is_active"])
        self._log(request, "UPDATE", "TENANT_STATUS", tenant=tenant, details={"is_active": is_active})
        return Response({"data": _tenant_payload(tenant)})


class SuperAdminSubscriptionsView(SuperAdminBaseView):
    def get(self, request):
        denied = self._check(request)
        if denied:
            return denied

        _ensure_subscriptions_for_tenants()
        page = max(1, int(request.query_params.get("page", 1)))
        limit = max(1, int(request.query_params.get("limit", 20)))
        status_filter = request.query_params.get("status")
        qs = TenantSubscription.objects.select_related("tenant").order_by("-created_at")
        if status_filter:
            qs = qs.filter(status=status_filter.upper())
        total = qs.count()
        start = (page - 1) * limit
        end = start + limit
        data = [_subscription_payload(s) for s in qs[start:end]]
        self._log(request, "VIEW", "SUBSCRIPTION_LIST", details={"status": status_filter})
        return Response({"data": data, "pagination": {"total": total, "page": page, "limit": limit, "pages": (total + limit - 1) // limit}})


class SuperAdminSubscriptionDetailView(SuperAdminBaseView):
    def get(self, request, subscription_id):
        denied = self._check(request)
        if denied:
            return denied
        sub = TenantSubscription.objects.select_related("tenant").filter(id=subscription_id).first()
        if not sub:
            return Response({"detail": "Subscription not found"}, status=404)
        self._log(request, "VIEW", "SUBSCRIPTION_DETAIL", tenant=sub.tenant, details={"subscription_id": subscription_id})
        return Response({"data": _subscription_payload(sub)})


class SuperAdminSubscriptionPlanView(SuperAdminBaseView):
    def patch(self, request, subscription_id):
        denied = self._check(request)
        if denied:
            return denied
        sub = TenantSubscription.objects.select_related("tenant").filter(id=subscription_id).first()
        if not sub:
            return Response({"detail": "Subscription not found"}, status=404)

        plan_identifier = request.data.get("plan_id")
        plan = SubscriptionPlan.objects.filter(Q(id=plan_identifier) | Q(code=plan_identifier)).first()
        if not plan:
            return Response({"detail": "Plan not found"}, status=404)

        sub.plan_id = plan.code
        sub.status = "ACTIVE"
        sub.save(update_fields=["plan_id", "status", "updated_at"])
        self._log(request, "UPDATE", "SUBSCRIPTION_PLAN", tenant=sub.tenant, details={"subscription_id": subscription_id, "plan_code": plan.code})
        return Response({"data": _subscription_payload(sub)})


class SuperAdminExtendTrialView(SuperAdminBaseView):
    def patch(self, request, subscription_id):
        denied = self._check(request)
        if denied:
            return denied
        sub = TenantSubscription.objects.select_related("tenant").filter(id=subscription_id).first()
        if not sub:
            return Response({"detail": "Subscription not found"}, status=404)
        days = max(1, int(request.data.get("days", 7)))
        base = sub.trial_end_date or timezone.now().date()
        sub.status = "TRIAL"
        sub.trial_end_date = base + timedelta(days=days)
        sub.save(update_fields=["status", "trial_end_date", "updated_at"])
        self._log(request, "UPDATE", "SUBSCRIPTION_TRIAL_EXTEND", tenant=sub.tenant, details={"days": days})
        return Response({"data": _subscription_payload(sub)})


class SuperAdminExpireSubscriptionView(SuperAdminBaseView):
    def post(self, request, subscription_id):
        denied = self._check(request)
        if denied:
            return denied
        sub = TenantSubscription.objects.select_related("tenant").filter(id=subscription_id).first()
        if not sub:
            return Response({"detail": "Subscription not found"}, status=404)
        sub.status = "EXPIRED"
        sub.subscription_end_date = timezone.now().date()
        sub.save(update_fields=["status", "subscription_end_date", "updated_at"])
        self._log(request, "UPDATE", "SUBSCRIPTION_EXPIRE", tenant=sub.tenant)
        return Response({"data": _subscription_payload(sub)})


class SuperAdminCancelSubscriptionView(SuperAdminBaseView):
    def patch(self, request, subscription_id):
        denied = self._check(request)
        if denied:
            return denied
        sub = TenantSubscription.objects.select_related("tenant").filter(id=subscription_id).first()
        if not sub:
            return Response({"detail": "Subscription not found"}, status=404)
        sub.status = "CANCELLED"
        sub.cancelled_at = timezone.now()
        sub.save(update_fields=["status", "cancelled_at", "updated_at"])
        self._log(request, "UPDATE", "SUBSCRIPTION_CANCEL", tenant=sub.tenant)
        return Response({"data": _subscription_payload(sub)})


class SuperAdminUsersView(SuperAdminBaseView):
    def get(self, request):
        denied = self._check(request)
        if denied:
            return denied
        users = User.objects.all().order_by("-created_at")
        role_map = {
            str(item["user_id"]): item["role"]
            for item in UserTenant.objects.values("user_id", "role")
        }
        data = [
            {
                "id": str(u.id),
                "user_code": u.user_code,
                "name": u.name,
                "email": u.email,
                "role": "SUPER_ADMIN" if u.is_super_admin else role_map.get(str(u.id)),
                "is_super_admin": u.is_super_admin,
                "is_active": u.is_active,
                "must_change_password": u.must_change_password,
                "remember_me": False,
                "createdAt": u.created_at.isoformat(),
                "updatedAt": u.created_at.isoformat(),
            }
            for u in users
        ]
        self._log(request, "VIEW", "USER_LIST")
        return Response(data)


class SuperAdminPlansView(SuperAdminBaseView):
    def get(self, request):
        denied = self._check(request)
        if denied:
            return denied
        _ensure_default_plans()
        plans = SubscriptionPlan.objects.all().order_by("name")
        self._log(request, "VIEW", "PLAN_LIST")
        return Response({"data": [_plan_payload(p) for p in plans]})

    def post(self, request):
        denied = self._check(request)
        if denied:
            return denied
        name = request.data.get("name")
        if not name:
            return Response({"detail": "name is required"}, status=400)
        code = (request.data.get("code") or name.lower().replace(" ", "-")).strip()
        plan = SubscriptionPlan.objects.create(
            code=code,
            name=name,
            description=request.data.get("description"),
            price=Decimal(str(request.data.get("price", 0))),
            business_type=request.data.get("business_type", "BOTH"),
            max_users=int(request.data.get("max_users", 1)),
            max_branches=int(request.data.get("max_branches", 1)),
            features=request.data.get("features", {}),
            is_active=bool(request.data.get("is_active", True)),
        )
        self._log(request, "CREATE", "PLAN", details={"plan_id": str(plan.id)})
        return Response({"data": _plan_payload(plan)}, status=201)


class SuperAdminPlanDetailView(SuperAdminBaseView):
    def get(self, request, plan_id):
        denied = self._check(request)
        if denied:
            return denied
        plan = SubscriptionPlan.objects.filter(id=plan_id).first()
        if not plan:
            return Response({"detail": "Plan not found"}, status=404)
        self._log(request, "VIEW", "PLAN_DETAIL", details={"plan_id": plan_id})
        return Response({"data": _plan_payload(plan)})

    def put(self, request, plan_id):
        denied = self._check(request)
        if denied:
            return denied
        plan = SubscriptionPlan.objects.filter(id=plan_id).first()
        if not plan:
            return Response({"detail": "Plan not found"}, status=404)
        plan.name = request.data.get("name", plan.name)
        plan.description = request.data.get("description", plan.description)
        plan.price = Decimal(str(request.data.get("price", plan.price)))
        plan.business_type = request.data.get("business_type", plan.business_type)
        plan.max_users = int(request.data.get("max_users", plan.max_users))
        plan.max_branches = int(request.data.get("max_branches", plan.max_branches))
        if "features" in request.data:
            plan.features = request.data.get("features") or {}
        if "is_active" in request.data:
            plan.is_active = bool(request.data.get("is_active"))
        plan.save()
        self._log(request, "UPDATE", "PLAN", details={"plan_id": plan_id})
        return Response({"data": _plan_payload(plan)})

    def delete(self, request, plan_id):
        denied = self._check(request)
        if denied:
            return denied
        plan = SubscriptionPlan.objects.filter(id=plan_id).first()
        if not plan:
            return Response({"detail": "Plan not found"}, status=404)
        plan.is_active = False
        plan.save(update_fields=["is_active", "updated_at"])
        self._log(request, "DELETE", "PLAN", details={"plan_id": plan_id})
        return Response({"success": True})


class SuperAdminAuditLogsView(SuperAdminBaseView):
    def get(self, request):
        denied = self._check(request)
        if denied:
            return denied
        page = max(1, int(request.query_params.get("page", 1)))
        limit = max(1, int(request.query_params.get("limit", 20)))
        search = request.query_params.get("search", "").strip()
        entity = request.query_params.get("entity")
        status_filter = request.query_params.get("status")

        qs = AuditLog.objects.select_related("user", "tenant").order_by("-created_at")
        if search:
            qs = qs.filter(
                Q(user__name__icontains=search)
                | Q(user__email__icontains=search)
                | Q(ip_address__icontains=search)
            )
        if entity:
            qs = qs.filter(entity=entity)
        if status_filter:
            qs = qs.filter(status=status_filter)

        total = qs.count()
        start = (page - 1) * limit
        end = start + limit
        rows = qs[start:end]
        data = [
            {
                "id": str(r.id),
                "action": r.action,
                "entity": r.entity,
                "status": r.status,
                "details": r.details,
                "ip_address": r.ip_address,
                "status_code": r.status_code,
                "error_message": r.error_message,
                "createdAt": r.created_at.isoformat(),
                "User": {"id": str(r.user.id), "name": r.user.name, "email": r.user.email} if r.user else None,
                "Tenant": {"id": str(r.tenant.id), "name": r.tenant.name} if r.tenant else None,
            }
            for r in rows
        ]
        return Response({"data": data, "pagination": {"total": total, "page": page, "limit": limit, "pages": (total + limit - 1) // limit}})


class SuperAdminSettingsView(SuperAdminBaseView):
    def get(self, request):
        denied = self._check(request)
        if denied:
            return denied
        settings_obj, _ = SystemSetting.objects.get_or_create(key="system", defaults={"data": {}})
        self._log(request, "VIEW", "SYSTEM_SETTINGS")
        return Response(settings_obj.data)

    def put(self, request):
        denied = self._check(request)
        if denied:
            return denied
        settings_obj, _ = SystemSetting.objects.get_or_create(key="system", defaults={"data": {}})
        settings_obj.data = request.data if isinstance(request.data, dict) else {}
        settings_obj.updated_by = request.user
        settings_obj.save()
        self._log(request, "UPDATE", "SYSTEM_SETTINGS")
        return Response(settings_obj.data)
