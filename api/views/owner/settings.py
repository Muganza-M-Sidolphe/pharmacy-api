from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from django.db import transaction
from drf_spectacular.utils import extend_schema

from ...models import Notification, Tenant, UserTenant
from ...permissions import IsOwner


COUNTRY_CURRENCY = {
    "RW": "RWF",
    "KE": "KES",
    "UG": "UGX",
}


class OwnerSettingsBaseView(APIView):
    permission_classes = [IsAuthenticated, IsOwner]

    def _owner_tenants(self, user):
        return UserTenant.objects.filter(
            user=user,
            role="OWNER",
            tenant__is_active=True,
        ).select_related("tenant")

    def _get_owned_tenant(self, user, tenant_id):
        if not tenant_id:
            return None

        return (
            UserTenant.objects.filter(
                user=user,
                tenant_id=tenant_id,
                role="OWNER",
                tenant__is_active=True,
            )
            .select_related("tenant")
            .first()
        )

    def _tenant_payload(self, tenant):
        return {
            "id": str(tenant.id),
            "name": tenant.name,
            "email": tenant.email,
            "phone": tenant.phone,
            "address": tenant.address,
            "licenseNumber": tenant.license_number,
            "country": tenant.country,
            "currency": tenant.currency,
            "taxRate": "0.00",
            "isActive": tenant.is_active,
            "createdAt": tenant.created_at,
        }

    def _resolve_selected_owner_tenant(self, request):
        owner_tenants = list(self._owner_tenants(request.user).order_by("tenant__name"))
        if not owner_tenants:
            return None, None, Response(
                {"detail": "No owned pharmacies found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        tenant_id = request.query_params.get("tenantId")
        if tenant_id:
            selected = next((ut for ut in owner_tenants if str(ut.tenant.id) == tenant_id), None)
            if not selected:
                return None, None, Response(
                    {"detail": "Unauthorized access to this pharmacy"},
                    status=status.HTTP_403_FORBIDDEN,
                )
            return owner_tenants, selected, None

        return owner_tenants, owner_tenants[0], None

    def _parse_positive_int(self, raw_value, default_value):
        if raw_value is None:
            return default_value, None
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            return None, Response(
                {"detail": "limit values must be valid integers"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if value <= 0:
            return None, Response(
                {"detail": "limit values must be greater than 0"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return value, None


class OwnerPharmaciesView(OwnerSettingsBaseView):
    """List all owner pharmacies and create a new pharmacy."""

    @extend_schema(description="List all pharmacies owned by current user", tags=["owner"])
    def get(self, request):
        owner_tenants = self._owner_tenants(request.user).order_by("tenant__name")

        results = []
        for owner_tenant in owner_tenants:
            tenant = owner_tenant.tenant
            results.append(
                {
                    "tenantId": str(tenant.id),
                    "tenantName": tenant.name,
                    "role": owner_tenant.role,
                    "address": tenant.address,
                    "email": tenant.email,
                    "phone": tenant.phone,
                    "country": tenant.country,
                    "currency": tenant.currency,
                    "isActive": tenant.is_active,
                }
            )

        return Response({"count": len(results), "results": results}, status=status.HTTP_200_OK)

    @transaction.atomic
    @extend_schema(description="Create a new pharmacy and assign current user as OWNER", tags=["owner"])
    def post(self, request):
        required_fields = ["name", "email", "phone", "address", "licenseNumber"]
        missing_fields = [field for field in required_fields if not request.data.get(field)]

        if missing_fields:
            return Response(
                {"detail": f"Missing required fields: {', '.join(missing_fields)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        tenant_email = request.data.get("email")
        if Tenant.objects.filter(email=tenant_email).exists():
            return Response(
                {"detail": "A pharmacy with this email already exists"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        country = request.data.get("country", "RW")
        currency = request.data.get("currency") or COUNTRY_CURRENCY.get(country, "USD")

        tenant = Tenant.objects.create(
            name=request.data.get("name"),
            email=tenant_email,
            phone=request.data.get("phone"),
            address=request.data.get("address"),
            license_number=request.data.get("licenseNumber"),
            country=country,
            currency=currency,
        )

        UserTenant.objects.create(
            user=request.user,
            tenant=tenant,
            role="OWNER",
        )

        return Response(
            {
                "message": "Pharmacy created successfully",
                "data": self._tenant_payload(tenant),
            },
            status=status.HTTP_201_CREATED,
        )


class PharmacySettingsView(OwnerSettingsBaseView):
    """Get and update one pharmacy settings."""

    @extend_schema(description="Get one pharmacy settings by tenantId", tags=["owner"])
    def get(self, request):
        tenant_id = request.query_params.get("tenantId")
        if not tenant_id:
            return Response(
                {"detail": "tenantId is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user_tenant = self._get_owned_tenant(request.user, tenant_id)
        if not user_tenant:
            return Response(
                {"detail": "Unauthorized access to this pharmacy"},
                status=status.HTTP_403_FORBIDDEN,
            )

        return Response(self._tenant_payload(user_tenant.tenant), status=status.HTTP_200_OK)

    @transaction.atomic
    @extend_schema(description="Update pharmacy settings by tenantId", tags=["owner"])
    def patch(self, request):
        tenant_id = request.query_params.get("tenantId")
        if not tenant_id:
            return Response(
                {"detail": "tenantId is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user_tenant = self._get_owned_tenant(request.user, tenant_id)
        if not user_tenant:
            return Response(
                {"detail": "Unauthorized access to this pharmacy"},
                status=status.HTTP_403_FORBIDDEN,
            )

        tenant = user_tenant.tenant

        updatable_fields = {
            "name": "name",
            "email": "email",
            "phone": "phone",
            "address": "address",
            "licenseNumber": "license_number",
            "country": "country",
            "currency": "currency",
        }

        # email uniqueness check when changing email
        new_email = request.data.get("email")
        if new_email and Tenant.objects.exclude(id=tenant.id).filter(email=new_email).exists():
            return Response(
                {"detail": "A pharmacy with this email already exists"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        for request_field, model_field in updatable_fields.items():
            if request_field in request.data and request.data.get(request_field) is not None:
                setattr(tenant, model_field, request.data.get(request_field))

        if "country" in request.data and "currency" not in request.data:
            tenant.currency = COUNTRY_CURRENCY.get(tenant.country, tenant.currency)

        try:
            tenant.save()
        except Exception as exc:
            return Response(
                {"detail": f"Error updating settings: {str(exc)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                "message": "Pharmacy settings updated successfully",
                "data": self._tenant_payload(tenant),
            },
            status=status.HTTP_200_OK,
        )


class OwnerSettingsOverviewView(OwnerSettingsBaseView):
    """Settings page API: pharmacies list + selected pharmacy settings in one response."""

    @extend_schema(description="Owner settings overview for pharmacy tab", tags=["owner"])
    def get(self, request):
        owner_tenants, selected, error = self._resolve_selected_owner_tenant(request)
        if error:
            return error

        pharmacies = []
        for owner_tenant in owner_tenants:
            tenant = owner_tenant.tenant
            pharmacies.append(
                {
                    "tenantId": str(tenant.id),
                    "tenantName": tenant.name,
                    "role": owner_tenant.role,
                    "address": tenant.address,
                    "email": tenant.email,
                    "phone": tenant.phone,
                    "country": tenant.country,
                    "currency": tenant.currency,
                    "isSelected": str(tenant.id) == str(selected.tenant.id),
                }
            )

        return Response(
            {
                "pharmacies": pharmacies,
                "selectedPharmacy": self._tenant_payload(selected.tenant),
            },
            status=status.HTTP_200_OK,
        )


class OwnerSettingsConsolidatedView(OwnerSettingsBaseView):
    """Single API for Settings tabs: Pharmacy, Users, Notifications, Security."""

    @extend_schema(description="Owner settings consolidated payload for all tabs", tags=["owner"])
    def get(self, request):
        owner_tenants, selected, error = self._resolve_selected_owner_tenant(request)
        if error:
            return error

        users_limit, users_limit_error = self._parse_positive_int(
            request.query_params.get("usersLimit"), 10
        )
        if users_limit_error:
            return users_limit_error

        notifications_limit, notifications_limit_error = self._parse_positive_int(
            request.query_params.get("notificationsLimit"), 10
        )
        if notifications_limit_error:
            return notifications_limit_error

        tenant = selected.tenant
        pharmacies = []
        for owner_tenant in owner_tenants:
            member_tenant = owner_tenant.tenant
            pharmacies.append(
                {
                    "tenantId": str(member_tenant.id),
                    "tenantName": member_tenant.name,
                    "role": owner_tenant.role,
                    "address": member_tenant.address,
                    "email": member_tenant.email,
                    "phone": member_tenant.phone,
                    "country": member_tenant.country,
                    "currency": member_tenant.currency,
                    "isSelected": str(member_tenant.id) == str(tenant.id),
                }
            )

        user_tenants_qs = UserTenant.objects.filter(tenant=tenant).select_related("user")
        users_results = []
        for user_tenant in user_tenants_qs.order_by("-user__created_at")[:users_limit]:
            users_results.append(
                {
                    "id": str(user_tenant.user.id),
                    "name": user_tenant.user.name,
                    "email": user_tenant.user.email,
                    "role": user_tenant.role,
                    "isActive": user_tenant.user.is_active,
                    "createdAt": user_tenant.user.created_at,
                }
            )

        by_role = {}
        for user_tenant in user_tenants_qs:
            by_role[user_tenant.role] = by_role.get(user_tenant.role, 0) + 1

        notifications_qs = Notification.objects.filter(tenant=tenant).order_by("-created_at")
        notification_filter = request.query_params.get("notificationFilter", "all").lower()
        if notification_filter == "read":
            notifications_qs = notifications_qs.filter(is_read=True)
        elif notification_filter == "unread":
            notifications_qs = notifications_qs.filter(is_read=False)

        notifications_results = []
        for notification in notifications_qs[:notifications_limit]:
            notifications_results.append(
                {
                    "id": str(notification.id),
                    "title": notification.title,
                    "message": notification.message,
                    "isRead": notification.is_read,
                    "recipientId": str(notification.recipient_id) if notification.recipient_id else None,
                    "createdAt": notification.created_at,
                }
            )

        total_notifications = Notification.objects.filter(tenant=tenant).count()
        unread_notifications = Notification.objects.filter(tenant=tenant, is_read=False).count()

        return Response(
            {
                "pharmacy": {
                    "pharmacies": pharmacies,
                    "selectedPharmacy": self._tenant_payload(tenant),
                },
                "users": {
                    "summary": {
                        "totalUsers": user_tenants_qs.count(),
                        "activeUsers": user_tenants_qs.filter(user__is_active=True).count(),
                        "inactiveUsers": user_tenants_qs.filter(user__is_active=False).count(),
                        "byRole": by_role,
                    },
                    "results": users_results,
                    "endpoints": {
                        "create": "/api/owner/create-user/",
                        "list": "/api/owner/users/",
                        "search": "/api/users/search/",
                    },
                },
                "notifications": {
                    "summary": {
                        "total": total_notifications,
                        "unread": unread_notifications,
                        "read": total_notifications - unread_notifications,
                    },
                    "results": notifications_results,
                    "endpoints": {
                        "listCreate": "/api/owner/notifications/",
                        "updateDelete": "/api/owner/notifications/{notification_id}/",
                    },
                },
                "security": {
                    "owner": {
                        "id": str(request.user.id),
                        "name": request.user.name,
                        "email": request.user.email,
                        "isActive": request.user.is_active,
                        "mustChangePassword": request.user.must_change_password,
                        "createdAt": request.user.created_at,
                    },
                    "passwordPolicy": {
                        "minLength": 8,
                        "requiresUppercase": False,
                        "requiresNumber": False,
                        "requiresSpecialCharacter": False,
                    },
                    "endpoints": {
                        "changePassword": "/api/change-password/",
                        "logout": "/api/logout/",
                    },
                },
            },
            status=status.HTTP_200_OK,
        )
