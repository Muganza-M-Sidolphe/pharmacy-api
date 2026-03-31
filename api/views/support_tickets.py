from rest_framework import permissions, status, viewsets
from rest_framework.response import Response

from ..models import SupportTicket, UserTenant
from ..serializers import SupportTicketSerializer


class SupportTicketViewSet(viewsets.ModelViewSet):
    serializer_class = SupportTicketSerializer
    permission_classes = [permissions.IsAuthenticated]
    queryset = SupportTicket.objects.select_related("created_by").all().order_by("-created_at")

    def _is_admin_user(self):
        user = self.request.user
        return bool(user.is_super_admin or user.is_staff)

    def _is_owner_user(self):
        return UserTenant.objects.filter(user=self.request.user, role="OWNER").exists()

    def _is_standalone_retail_user(self):
        retail_tenant_ids = set(
            UserTenant.objects.filter(role="PHARMACIST").values_list("tenant_id", flat=True)
        )
        wholesale_tenant_ids = set(
            UserTenant.objects.filter(role="OWNER").values_list("tenant_id", flat=True)
        )
        standalone_retail_tenant_ids = retail_tenant_ids - wholesale_tenant_ids
        if not standalone_retail_tenant_ids:
            return False
        return UserTenant.objects.filter(
            user=self.request.user,
            tenant_id__in=standalone_retail_tenant_ids,
        ).exists()

    def _can_create_own_ticket(self):
        return self._is_owner_user() or self._is_standalone_retail_user()

    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user

        if self._is_admin_user():
            ticket_type = self.request.query_params.get("type")
            ticket_status = self.request.query_params.get("status")
            priority = self.request.query_params.get("priority")

            if ticket_type:
                queryset = queryset.filter(type=ticket_type)
            if ticket_status:
                queryset = queryset.filter(status=ticket_status)
            if priority:
                queryset = queryset.filter(priority=priority)
            return queryset

        return queryset.filter(created_by=user)

    def list(self, request, *args, **kwargs):
        if not (self._is_admin_user() or self._can_create_own_ticket()):
            return Response({"detail": "Only owners and standalone retail users can access support tickets"}, status=status.HTTP_403_FORBIDDEN)
        return super().list(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        if not self._can_create_own_ticket():
            return Response({"detail": "Only owners and standalone retail users can create support tickets"}, status=status.HTTP_403_FORBIDDEN)
        return super().create(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        if not (self._is_admin_user() or self._can_create_own_ticket()):
            return Response({"detail": "Only owners and standalone retail users can access support tickets"}, status=status.HTTP_403_FORBIDDEN)
        return super().retrieve(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        if not self._is_admin_user():
            return Response({"detail": "Only admin users can update support tickets"}, status=status.HTTP_403_FORBIDDEN)
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        if not self._is_admin_user():
            return Response({"detail": "Only admin users can update support tickets"}, status=status.HTTP_403_FORBIDDEN)
        return super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        if not self._is_admin_user():
            return Response({"detail": "Only admin users can delete support tickets"}, status=status.HTTP_403_FORBIDDEN)
        return super().destroy(request, *args, **kwargs)

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)
