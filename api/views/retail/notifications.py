from django.db.models import Q
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ...models import Notification, UserTenant


class RetailNotificationsBaseView(APIView):
    permission_classes = [IsAuthenticated]

    def _validate_access(self, request):
        tenant_id = request.query_params.get("tenantId")
        if not tenant_id:
            return None, Response({"detail": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        has_access = UserTenant.objects.filter(
            user=request.user,
            tenant_id=tenant_id,
        ).exists()
        if not has_access or request.user.department != "RETAIL":
            return None, Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        return tenant_id, None

    def _scope_notifications(self, tenant_id, user):
        return Notification.objects.filter(tenant_id=tenant_id).filter(
            Q(recipient=user) | Q(recipient__isnull=True)
        )


class RetailRecentNotificationsView(RetailNotificationsBaseView):
    @extend_schema(description="Get recent notifications for retail portal", tags=["notifications", "retail"])
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


class RetailMarkNotificationAsReadView(RetailNotificationsBaseView):
    @extend_schema(description="Mark retail notification as read", tags=["notifications", "retail"])
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


class RetailMarkAllNotificationsAsReadView(RetailNotificationsBaseView):
    @extend_schema(
        description="Mark all retail notifications as read",
        tags=["notifications", "retail"],
        responses=None,
    )
    def post(self, request):
        tenant_id, error = self._validate_access(request)
        if error:
            return error

        marked_count = self._scope_notifications(tenant_id, request.user).filter(is_read=False).update(is_read=True)
        return Response({"marked_as_read": marked_count})
