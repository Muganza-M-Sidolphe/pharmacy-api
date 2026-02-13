from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from django.shortcuts import get_object_or_404
from django.core.paginator import Paginator
from django.db.models import Count
from ...models import Notification, Tenant, UserTenant, User
from ...serializers import NotificationSerializer, NotificationModelSerializer, NotificationMarkSerializer
from drf_spectacular.utils import extend_schema


class OwnerNotificationsView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        responses=NotificationModelSerializer(many=True),
        description="List notifications for the tenant. Use `filter=read|unread` to filter.")
    def get(self, request):
        tenant_id = request.query_params.get("tenantId")
        if not tenant_id:
            return Response({"detail": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        # ensure requester belongs to tenant and is owner
        if not UserTenant.objects.filter(user=request.user, tenant_id=tenant_id, role="OWNER").exists():
            return Response({"detail": "Unauthorized tenant access"}, status=status.HTTP_403_FORBIDDEN)

        filter_by = request.query_params.get("filter", "all").lower()
        qs = Notification.objects.filter(tenant_id=tenant_id)

        if filter_by == "unread":
            qs = qs.filter(is_read=False)
        elif filter_by == "read":
            qs = qs.filter(is_read=True)

        page = int(request.GET.get("page", 1))
        page_size = int(request.GET.get("page_size", 10))

        paginator = Paginator(qs.order_by("-created_at"), page_size)
        page_obj = paginator.get_page(page)

        data = [NotificationSerializer().to_representation(n) for n in page_obj]

        return Response({
            "results": data,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total_pages": paginator.num_pages,
                "total_items": paginator.count
            }
        })

    @extend_schema(request=NotificationModelSerializer, responses=NotificationModelSerializer, description="Create a notification for the tenant (recipient optional).")
    def post(self, request):
        # owner can create a notification for a tenant (or a specific recipient)
        tenant_id = request.data.get("tenantId")
        if not tenant_id:
            return Response({"detail": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        if not UserTenant.objects.filter(user=request.user, tenant_id=tenant_id, role="OWNER").exists():
            return Response({"detail": "Unauthorized tenant access"}, status=status.HTTP_403_FORBIDDEN)

        serializer = NotificationModelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        # Need to construct Notification object manually to bind tenant and optional recipient
        tenant_id = serializer.validated_data.get('tenant', {}).get('id') if isinstance(serializer.validated_data.get('tenant'), dict) else None
        # serializer expects tenant to be read-only; create using serializer.validated_data fields directly
        from ...models import Notification as _Notification
        recipient = None
        if serializer.validated_data.get('recipient'):
            recipient = User.objects.filter(id=serializer.validated_data['recipient'].get('id')).first()
        notification = _Notification.objects.create(
            tenant_id=request.data.get('tenantId'),
            title=serializer.validated_data['title'],
            message=serializer.validated_data['message'],
            recipient=recipient
        )

        return Response(NotificationModelSerializer(notification).data, status=status.HTTP_201_CREATED)


class OwnerNotificationDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=NotificationMarkSerializer, responses=NotificationModelSerializer)
    def patch(self, request, notification_id):
        tenant_id = request.query_params.get("tenantId")
        if not tenant_id:
            return Response({"detail": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        if not UserTenant.objects.filter(user=request.user, tenant_id=tenant_id, role="OWNER").exists():
            return Response({"detail": "Unauthorized tenant access"}, status=status.HTTP_403_FORBIDDEN)

        notification = get_object_or_404(Notification, id=notification_id, tenant_id=tenant_id)

        serializer = NotificationMarkSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        notification.is_read = serializer.validated_data['isRead']
        notification.save()

        return Response(NotificationModelSerializer(notification).data, status=status.HTTP_200_OK)

    @extend_schema(responses=None, description="Delete a notification")
    def delete(self, request, notification_id):
        tenant_id = request.query_params.get("tenantId")
        if not tenant_id:
            return Response({"detail": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        if not UserTenant.objects.filter(user=request.user, tenant_id=tenant_id, role="OWNER").exists():
            return Response({"detail": "Unauthorized tenant access"}, status=status.HTTP_403_FORBIDDEN)

        notification = get_object_or_404(Notification, id=notification_id, tenant_id=tenant_id)
        notification.delete()
        return Response({"message": "Notification deleted"}, status=status.HTTP_200_OK)


class OwnerNotificationsDashboardView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Owner notifications consolidated payload (summary + breakdown + recent list)",
        tags=["owner"],
    )
    def get(self, request):
        tenant_id = request.query_params.get("tenantId")
        if not tenant_id:
            return Response({"detail": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        if not UserTenant.objects.filter(user=request.user, tenant_id=tenant_id, role="OWNER").exists():
            return Response({"detail": "Unauthorized tenant access"}, status=status.HTTP_403_FORBIDDEN)

        try:
            limit = int(request.query_params.get("limit", 10))
        except ValueError:
            return Response({"detail": "limit must be an integer"}, status=status.HTTP_400_BAD_REQUEST)

        if limit <= 0:
            return Response({"detail": "limit must be greater than 0"}, status=status.HTTP_400_BAD_REQUEST)

        qs = Notification.objects.filter(tenant_id=tenant_id).order_by("-created_at")

        total_count = qs.count()
        read_count = qs.filter(is_read=True).count()
        unread_count = qs.filter(is_read=False).count()

        recipient_breakdown = {}
        for row in qs.values("recipient_id").annotate(count=Count("id")):
            key = str(row["recipient_id"]) if row["recipient_id"] else "BROADCAST"
            recipient_breakdown[key] = row["count"]

        recent = [NotificationSerializer().to_representation(item) for item in qs[:limit]]

        return Response(
            {
                "summary": {
                    "total": total_count,
                    "read": read_count,
                    "unread": unread_count,
                },
                "readStatusDistribution": [
                    {"status": "READ", "count": read_count},
                    {"status": "UNREAD", "count": unread_count},
                ],
                "recipientBreakdown": [
                    {"recipientId": key, "count": value}
                    for key, value in recipient_breakdown.items()
                ],
                "recentNotifications": {
                    "count": total_count,
                    "results": recent,
                },
            },
            status=status.HTTP_200_OK,
        )
