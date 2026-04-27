from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.contrib.auth import authenticate
from ...models import UserTenant, TenantSubscription, SubscriptionPlan
from ...utils.jwt import generate_token


def _tenant_business_type(tenant):
    # Prefer tenant role mapping because plan defaults can be generic/mismatched.
    tenant_roles = UserTenant.objects.filter(tenant=tenant).values_list("role", flat=True)
    if "OWNER" in tenant_roles:
        return "WHOLESALE"
    if "PHARMACIST" in tenant_roles:
        return "RETAIL"

    # Fallback to subscription plan business type if no role signal is available.
    subscription = TenantSubscription.objects.filter(tenant=tenant).first()
    if not subscription:
        return None
    plan = (
        SubscriptionPlan.objects.filter(code=subscription.plan_id).first()
        or SubscriptionPlan.objects.filter(id=subscription.plan_id).first()
    )
    if not plan:
        return None
    return plan.business_type


def _tenant_pharmacy_type(tenant, business_type=None):
    if business_type == "WHOLESALE":
        return "wholesale"
    if business_type == "RETAIL":
        return "retail"

    tenant_roles = UserTenant.objects.filter(tenant=tenant).values_list("role", flat=True)
    if "OWNER" in tenant_roles:
        return "wholesale"
    if "PHARMACIST" in tenant_roles:
        return "retail"
    return None


def _is_collaborative_retail(user, tenant, business_type=None):
    resolved_business_type = business_type or _tenant_business_type(tenant)
    return user.department == "RETAIL" and resolved_business_type == "WHOLESALE"


class LoginView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        email = request.data.get("email")
        password = request.data.get("password")

        user = authenticate(email=email, password=password)

        if not user:
            return Response(
                {"message": "Invalid credentials"},
                status=status.HTTP_401_UNAUTHORIZED
            )

        # Check if user must change password (first login with temp password)
        if user.must_change_password:
            return Response({
                "status": "MUST_CHANGE_PASSWORD",
                "message": "You must change your password before continuing",
                "userId": str(user.id),
                "name":user.name,
                "email": user.email,
                "department": user.department,
                "isCollaborativeRetail": False,
            })

        # Get all user tenants (not just OWNER)
        user_tenants = UserTenant.objects.filter(
            user=user
        ).select_related("tenant")

        # Super admin can login without tenant assignment
        if user.is_super_admin and not user_tenants.exists():
            token = generate_token(user=user, role="SUPER_ADMIN")
            return Response({
                "status": "OK",
                "mode": "SUPER_ADMIN",
                "data": {
                    "token": token,
                    "name":user.name,
                    "role": "SUPER_ADMIN",
                    "department": user.department,
                    "isCollaborativeRetail": False,
                }
            })

        if not user_tenants.exists():
            return Response(
                {"message": "No pharmacy assigned"},
                status=status.HTTP_403_FORBIDDEN
            )

        # Single tenant → auto login
        if user_tenants.count() == 1:
            ut = user_tenants.first()
            business_type = _tenant_business_type(ut.tenant)
            pharmacy_type = _tenant_pharmacy_type(ut.tenant, business_type=business_type)
            token = generate_token(
                user=user,
                tenant=ut.tenant,
                role=ut.role
            )
        
            return Response({
                "status": "OK",
                "mode": "AUTO",
                "data": {
                    "token": token,
                    "name":user.name,
                    "tenant": {
                        "id": str(ut.tenant.id),
                        "name": ut.tenant.name,
                        "currency": ut.tenant.currency,
                        "businessType": business_type,
                        "pharmacyType": pharmacy_type
                    },
                    "role": ut.role,
                    "department": user.department,
                    "isCollaborativeRetail": _is_collaborative_retail(user, ut.tenant, business_type=business_type),
                }
            })

        # Multiple tenants → choose
        temp_token = generate_token(user=user)  # no tenant

        tenants_payload = []
        for ut in user_tenants:
            business_type = _tenant_business_type(ut.tenant)
            tenants_payload.append({
                "id": str(ut.tenant.id),
                "name": ut.tenant.name,
                "currency": ut.tenant.currency,
                "role": ut.role,
                "businessType": business_type,
                "pharmacyType": _tenant_pharmacy_type(ut.tenant, business_type=business_type),
                "isCollaborativeRetail": _is_collaborative_retail(user, ut.tenant, business_type=business_type),
            })

        return Response({
            "status": "CHOOSE_TENANT",
            "name":user.name,
            "department": user.department,
            "isCollaborativeRetail": False,
            "tenants": tenants_payload,
            "tempToken": temp_token
        })
