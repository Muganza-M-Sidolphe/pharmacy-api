from django.db.models import Q
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ..models import Notification, UserTenant


class BaseRoleNotificationsView(APIView):
    permission_classes = [IsAuthenticated]
    required_role = None
    role_tag = None

    def _validate_access(self, request):
        tenant_id = request.query_params.get("tenantId")
        if not tenant_id:
            return None, Response({"detail": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        has_access = UserTenant.objects.filter(
            user=request.user,
            tenant_id=tenant_id,
            role=self.required_role,
        ).exists()
        if not has_access:
            return None, Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        return tenant_id, None

    def _scope_notifications(self, tenant_id, user):
        return Notification.objects.filter(
            tenant_id=tenant_id
        ).filter(
            Q(recipient=user) | Q(recipient__isnull=True)
        )


class BaseRoleRecentNotificationsView(BaseRoleNotificationsView):
    @extend_schema(description="Get recent notifications", tags=["notifications"])
    def get(self, request):
        tenant_id, error = self._validate_access(request)
        if error:
            return error

        try:
            limit = int(request.query_params.get("limit", 10))
            if limit <= 0:
                raise ValueError
        except ValueError:
            return Response({"detail": "limit must be a positive integer"}, status=status.HTTP_400_BAD_REQUEST)

        notifications = self._scope_notifications(tenant_id, request.user).order_by("-created_at")[:limit]

        return Response(
            {
                "results": [
                    {
                        "id": str(notification.id),
                        "title": notification.title,
                        "message": notification.message,
                        "isRead": notification.is_read,
                        "createdAt": notification.created_at,
                    }
                    for notification in notifications
                ]
            }
        )


class BaseRoleMarkNotificationAsReadView(BaseRoleNotificationsView):
    @extend_schema(description="Mark notification as read", tags=["notifications"])
    def post(self, request, notification_id):
        tenant_id, error = self._validate_access(request)
        if error:
            return error

        try:
            notification = self._scope_notifications(tenant_id, request.user).get(id=notification_id)
        except Notification.DoesNotExist:
            return Response({"detail": "Notification not found"}, status=status.HTTP_404_NOT_FOUND)

        notification.is_read = True
        notification.save(update_fields=["is_read"])

        return Response(
            {
                "id": str(notification.id),
                "title": notification.title,
                "message": notification.message,
                "isRead": notification.is_read,
                "createdAt": notification.created_at,
            }
        )


class BaseRoleMarkAllNotificationsAsReadView(BaseRoleNotificationsView):
    @extend_schema(description="Mark all notifications as read", tags=["notifications"], responses=None)
    def post(self, request):
        tenant_id, error = self._validate_access(request)
        if error:
            return error

        marked_count = self._scope_notifications(tenant_id, request.user).filter(is_read=False).update(is_read=True)
        return Response({"marked_as_read": marked_count})


class StorekeeperRecentNotificationsView(BaseRoleRecentNotificationsView):
    required_role = "STORE_KEEPER"
    role_tag = "storekeeper"


class StorekeeperMarkNotificationAsReadView(BaseRoleMarkNotificationAsReadView):
    required_role = "STORE_KEEPER"
    role_tag = "storekeeper"


class StorekeeperMarkAllNotificationsAsReadView(BaseRoleMarkAllNotificationsAsReadView):
    required_role = "STORE_KEEPER"
    role_tag = "storekeeper"


class CashierRecentNotificationsView(BaseRoleRecentNotificationsView):
    required_role = "CASHIER"
    role_tag = "cashier"


class CashierMarkNotificationAsReadView(BaseRoleMarkNotificationAsReadView):
    required_role = "CASHIER"
    role_tag = "cashier"


class CashierMarkAllNotificationsAsReadView(BaseRoleMarkAllNotificationsAsReadView):
    required_role = "CASHIER"
    role_tag = "cashier"


class PharmacistRecentNotificationsView(BaseRoleRecentNotificationsView):
    required_role = "PHARMACIST"
    role_tag = "pharmacist"


class PharmacistMarkNotificationAsReadView(BaseRoleMarkNotificationAsReadView):
    required_role = "PHARMACIST"
    role_tag = "pharmacist"


class PharmacistMarkAllNotificationsAsReadView(BaseRoleMarkAllNotificationsAsReadView):
    required_role = "PHARMACIST"
    role_tag = "pharmacist"
