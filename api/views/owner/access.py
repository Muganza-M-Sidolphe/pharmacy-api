"""Users & Roles: list users and roles, customize roles, assign roles and per-user permissions."""

import uuid

from django.db import IntegrityError, transaction
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ...models import TenantRole, User, UserTenant
from ...utils.permission_codes import (
    ALL_PERMISSION_CODES,
    PERMISSION_GROUPS,
    SYSTEM_ROLES,
    clean_permission_codes,
    membership_permissions,
    role_permissions,
)
from ...utils.subscription_access import authorize_tenant_access

ASSIGNABLE_SYSTEM_ROLES = ["PHARMACIST", "ACCOUNTANT", "STORE_KEEPER", "CASHIER"]


def _forbidden(message):
    return Response({"detail": message}, status=status.HTTP_403_FORBIDDEN)


def _bad_request(message):
    return Response({"detail": message}, status=status.HTTP_400_BAD_REQUEST)


def _infer_base_role(codes):
    """The system role whose default permissions are closest to these codes (decides the dashboard)."""
    wanted = set(codes)

    def overlap(role):
        defaults = set(SYSTEM_ROLES[role][2])
        union = wanted | defaults
        return len(wanted & defaults) / len(union) if union else 0

    return max(ASSIGNABLE_SYSTEM_ROLES, key=overlap)


class AccessBaseView(APIView):
    permission_classes = [IsAuthenticated]
    required_permission = None

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self.tenant = self.actor = None
        self.access_error = None
        tenant_id = request.query_params.get("tenantId") or request.data.get("tenantId")
        tenant, message, error_status = authorize_tenant_access(request, tenant_id)
        if message:
            self.access_error = Response({"detail": message}, status=error_status)
            return
        self.tenant = tenant
        self.actor = UserTenant.objects.select_related("custom_role").get(user=request.user, tenant=tenant)
        self.actor_permissions = membership_permissions(self.actor)
        if self.required_permission and self.required_permission not in self.actor_permissions:
            self.access_error = _forbidden(f"You do not have the {self.required_permission} permission.")

    @property
    def actor_is_owner(self):
        return self.actor.role in ("OWNER", "ADMIN")

    def _membership(self, user_id):
        return (
            UserTenant.objects.select_related("user", "custom_role")
            .filter(tenant=self.tenant, user_id=user_id)
            .first()
        )

    def _check_can_change(self, membership):
        """Owners' access is fixed; non-owners cannot change their own access."""
        if membership.role in ("OWNER", "ADMIN"):
            return _forbidden("The Owner's access cannot be changed.")
        if membership.user_id == self.actor.user_id and not self.actor_is_owner:
            return _forbidden("You cannot change your own access.")
        return None

    def _check_can_grant(self, codes):
        """Non-owners can only give out permissions they have themselves."""
        if self.actor_is_owner:
            return None
        extra = sorted(set(codes) - set(self.actor_permissions))
        if extra:
            return _forbidden(f"You cannot grant permissions you do not have: {', '.join(extra)}.")
        return None

    # -- serializers -------------------------------------------------------

    def _role_name(self, membership):
        if membership.custom_role_id:
            return membership.custom_role.name
        return SYSTEM_ROLES.get(membership.role, (membership.role.title(),))[0]

    def _user_payload(self, membership):
        user = membership.user
        return {
            "id": str(user.id),
            "name": user.name,
            "email": user.email,
            "phone": user.phone,
            "role": self._role_name(membership),
            "roleId": str(membership.custom_role_id) if membership.custom_role_id else membership.role.lower(),
            "baseRole": membership.role,
            "status": "Active" if user.is_active else "Inactive",
            "permissions": membership_permissions(membership),
            "hasCustomPermissions": membership.permission_overrides is not None,
        }

    def _roles_payload(self):
        counts = {}
        for membership in UserTenant.objects.filter(tenant=self.tenant):
            key = str(membership.custom_role_id) if membership.custom_role_id else membership.role.lower()
            counts[key] = counts.get(key, 0) + 1
        customized = {r.base_role: r for r in TenantRole.objects.filter(tenant=self.tenant, is_system=True)}
        roles = []
        for key, (name, description, _) in SYSTEM_ROLES.items():
            custom = customized.get(key)
            roles.append({
                "id": key.lower(),
                "name": name,
                "description": (custom.description if custom and custom.description else description),
                "isSystem": True,
                "baseRole": key,
                "permissions": role_permissions(self.tenant.id, key),
                "userCount": counts.get(key.lower(), 0),
            })
        for role in TenantRole.objects.filter(tenant=self.tenant, is_system=False).order_by("name"):
            roles.append({
                "id": str(role.id),
                "name": role.name,
                "description": role.description,
                "isSystem": False,
                "baseRole": role.base_role,
                "permissions": clean_permission_codes(role.permissions),
                "userCount": counts.get(str(role.id), 0),
            })
        return roles


class AccessPermissionsView(AccessBaseView):
    required_permission = "permissions.view"

    def get(self, request):
        if self.access_error:
            return self.access_error
        return Response({"results": [
            {"name": group, "permissions": [{"code": code, "label": label} for code, label in permissions]}
            for group, permissions in PERMISSION_GROUPS
        ]})


class AccessRolesView(AccessBaseView):
    def initial(self, request, *args, **kwargs):
        self.required_permission = "roles.manage" if request.method == "POST" else "roles.view"
        super().initial(request, *args, **kwargs)

    def get(self, request):
        if self.access_error:
            return self.access_error
        return Response({"results": self._roles_payload()})

    def post(self, request):
        """Create a custom role. Its base role (dashboard) is chosen from its permissions unless given."""
        if self.access_error:
            return self.access_error
        name = str(request.data.get("name") or "").strip()
        if not name:
            return _bad_request("Role name is required.")
        if name.lower() in {display.lower() for display, _, _ in SYSTEM_ROLES.values()}:
            return _bad_request(f"'{name}' is a system role. Edit it instead of creating a new one.")
        if TenantRole.objects.filter(tenant=self.tenant, name__iexact=name).exists():
            return _bad_request(f"A role named '{name}' already exists.")
        codes = clean_permission_codes(request.data.get("permissions"))
        denied = self._check_can_grant(codes)
        if denied:
            return denied
        base_role = str(request.data.get("baseRole") or "").upper() or _infer_base_role(codes)
        if base_role not in ASSIGNABLE_SYSTEM_ROLES:
            return _bad_request(f"baseRole must be one of: {', '.join(ASSIGNABLE_SYSTEM_ROLES)}.")
        try:
            role = TenantRole.objects.create(
                tenant=self.tenant,
                name=name[:100],
                description=str(request.data.get("description") or "").strip(),
                base_role=base_role,
                permissions=codes,
            )
        except IntegrityError:
            return _bad_request(f"A role named '{name}' already exists.")
        return Response(
            next(item for item in self._roles_payload() if item["id"] == str(role.id)),
            status=status.HTTP_201_CREATED,
        )


class AccessRoleDetailView(AccessBaseView):
    required_permission = "roles.manage"

    def patch(self, request, role_id):
        """Customize a system role for this pharmacy, or edit a custom role."""
        if self.access_error:
            return self.access_error
        data = request.data
        key = str(role_id).upper()
        if key in ("OWNER", "ADMIN"):
            return _forbidden("The Owner role cannot be customized.")

        with transaction.atomic():
            if key in SYSTEM_ROLES:
                role, _ = TenantRole.objects.get_or_create(
                    tenant=self.tenant, is_system=True, base_role=key,
                    defaults={"name": SYSTEM_ROLES[key][0], "permissions": SYSTEM_ROLES[key][2]},
                )
            else:
                role = TenantRole.objects.filter(tenant=self.tenant, is_system=False, id=_uuid_or_none(role_id)).first()
                if not role:
                    return Response({"detail": "Role not found."}, status=status.HTTP_404_NOT_FOUND)
                name = str(data.get("name") or role.name).strip()
                if name.lower() != role.name.lower() and TenantRole.objects.filter(
                    tenant=self.tenant, name__iexact=name
                ).exclude(id=role.id).exists():
                    return _bad_request(f"A role named '{name}' already exists.")
                role.name = name[:100]
                base_role = str(data.get("baseRole") or role.base_role).upper()
                if base_role not in ASSIGNABLE_SYSTEM_ROLES:
                    return _bad_request(f"baseRole must be one of: {', '.join(ASSIGNABLE_SYSTEM_ROLES)}.")
                if base_role != role.base_role:
                    role.base_role = base_role
                    role.memberships.update(role=base_role)

            if "description" in data:
                role.description = str(data.get("description") or "").strip()
            if "permissions" in data:
                codes = clean_permission_codes(data.get("permissions"))
                denied = self._check_can_grant(codes)
                if denied:
                    return denied
                role.permissions = codes
            role.save()

        role_key = key.lower() if role.is_system else str(role.id)
        return Response(next(item for item in self._roles_payload() if item["id"] == role_key))


class AccessUsersView(AccessBaseView):
    required_permission = "users.view"

    def get(self, request):
        if self.access_error:
            return self.access_error
        memberships = (
            UserTenant.objects.filter(tenant=self.tenant)
            .select_related("user", "custom_role")
            .order_by("user__name")
        )
        return Response({"results": [self._user_payload(m) for m in memberships]})


class AccessUserDetailView(AccessBaseView):
    required_permission = "users.edit"

    def patch(self, request, user_id):
        """Edit a user's name, email and phone."""
        if self.access_error:
            return self.access_error
        membership = self._membership(user_id)
        if not membership:
            return Response({"detail": "User not found in this pharmacy."}, status=status.HTTP_404_NOT_FOUND)
        if membership.role in ("OWNER", "ADMIN") and membership.user_id != self.actor.user_id:
            return _forbidden("Only the owner can edit the owner's details.")

        user = membership.user
        fields = []
        if "name" in request.data:
            name = str(request.data.get("name") or "").strip()
            if not name:
                return _bad_request("Name is required.")
            user.name, fields = name[:255], fields + ["name"]
        if "email" in request.data:
            email = str(request.data.get("email") or "").strip().lower()
            if not email:
                return _bad_request("Email is required: it is used to sign in.")
            if User.objects.filter(email__iexact=email).exclude(id=user.id).exists():
                return _bad_request("Another account already uses this email.")
            user.email, fields = email, fields + ["email"]
        if "phone" in request.data:
            user.phone, fields = str(request.data.get("phone") or "").strip()[:30], fields + ["phone"]
        if fields:
            user.save(update_fields=fields)
        return Response(self._user_payload(membership))


class AccessUserPermissionsView(AccessBaseView):
    def initial(self, request, *args, **kwargs):
        self.required_permission = "users.manage_permissions" if request.method == "PUT" else "permissions.view"
        super().initial(request, *args, **kwargs)

    def get(self, request, user_id):
        if self.access_error:
            return self.access_error
        membership = self._membership(user_id)
        if not membership:
            return Response({"detail": "User not found in this pharmacy."}, status=status.HTTP_404_NOT_FOUND)
        return Response({
            "permissions": membership_permissions(membership),
            "hasCustomPermissions": membership.permission_overrides is not None,
        })

    def put(self, request, user_id):
        """Set this user's own permission list (replaces their role's). Send null to go back to the role."""
        if self.access_error:
            return self.access_error
        membership = self._membership(user_id)
        if not membership:
            return Response({"detail": "User not found in this pharmacy."}, status=status.HTTP_404_NOT_FOUND)
        denied = self._check_can_change(membership)
        if denied:
            return denied
        codes = request.data.get("permissions")
        if codes is None:
            membership.permission_overrides = None
        else:
            if not isinstance(codes, list):
                return _bad_request("permissions must be a list of permission codes.")
            unknown = sorted(set(codes) - set(ALL_PERMISSION_CODES))
            if unknown:
                return _bad_request(f"Unknown permissions: {', '.join(unknown)}.")
            codes = clean_permission_codes(codes)
            denied = self._check_can_grant(codes)
            if denied:
                return denied
            membership.permission_overrides = codes
        membership.save(update_fields=["permission_overrides"])
        return Response(self._user_payload(membership))


class AccessUserRoleView(AccessBaseView):
    required_permission = "users.assign_role"

    def post(self, request, user_id):
        """Assign a role by name or id. This also clears permissions set for the user only."""
        if self.access_error:
            return self.access_error
        membership = self._membership(user_id)
        if not membership:
            return Response({"detail": "User not found in this pharmacy."}, status=status.HTTP_404_NOT_FOUND)
        denied = self._check_can_change(membership)
        if denied:
            return denied

        wanted = str(request.data.get("role") or request.data.get("roleId") or "").strip()
        system_key = next(
            (key for key, (name, _, _) in SYSTEM_ROLES.items() if wanted.lower() in (key.lower(), name.lower())),
            None,
        )
        if system_key in ("OWNER", "ADMIN"):
            return _forbidden("The Owner role cannot be assigned here.")
        custom = None
        if not system_key:
            custom = (
                TenantRole.objects.filter(tenant=self.tenant, is_system=False, name__iexact=wanted).first()
                or TenantRole.objects.filter(tenant=self.tenant, is_system=False, id=_uuid_or_none(wanted)).first()
            )
            if not custom:
                return _bad_request(f"Role '{wanted}' was not found.")
        new_codes = clean_permission_codes(custom.permissions) if custom else role_permissions(self.tenant.id, system_key)
        denied = self._check_can_grant(new_codes)
        if denied:
            return denied

        membership.role = custom.base_role if custom else system_key
        membership.custom_role = custom
        membership.permission_overrides = None
        membership.save(update_fields=["role", "custom_role", "permission_overrides"])
        return Response(self._user_payload(membership))


def _uuid_or_none(value):
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None
