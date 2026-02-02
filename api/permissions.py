from rest_framework.permissions import BasePermission
from .models import UserTenant


class IsOwner(BasePermission):
    """
    Permission to check if user is an owner of a tenant.
    """
    
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        
        return UserTenant.objects.filter(
            user=request.user,
            role="OWNER"
        ).exists()


