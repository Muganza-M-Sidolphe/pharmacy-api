from django.db import transaction
import logging
from urllib.parse import urlencode
from django.conf import settings
from django.core.mail import send_mail
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from django.shortcuts import get_object_or_404
from django.db.models import Count, Q
from django.core.paginator import Paginator
from ...models import User, UserTenant, Tenant
from ...serializers import CreateUserSerializer, UserUpdateSerializer
from ...permissions import IsOwner
from ...utils.password import generate_temp_password
from ...utils.subscription_access import authorize_tenant_access, get_subscription_limit
from drf_spectacular.utils import extend_schema

logger = logging.getLogger(__name__)


def _build_frontend_url(path, params=None):
    base_url = getattr(settings, "FRONTEND_BASE_URL", "http://localhost:5173").rstrip("/")
    query = f"?{urlencode(params)}" if params else ""
    return f"{base_url}{path}{query}"


def _send_new_user_credentials_email(user, tenant, temp_password):
    login_url = _build_frontend_url("/login", {"email": user.email})
    change_password_url = _build_frontend_url("/change-password", {"email": user.email})
    subject = f"Your account was created for {tenant.name}"
    message = (
        f"Hello {user.name},\n\n"
        f"Your account has been created for {tenant.name}.\n\n"
        f"Email: {user.email}\n"
        f"Temporary password: {temp_password}\n\n"
        "Please sign in, then change your password immediately.\n"
        f"Login: {login_url}\n"
        f"Change password: {change_password_url}\n\n"
        "For security, this temporary password should be used only once."
    )
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", None) or "no-reply@pharmacy.local"
    send_mail(subject, message, from_email, [user.email], fail_silently=False)
    return {"loginUrl": login_url, "changePasswordUrl": change_password_url}


class CreateUserView(APIView):
    permission_classes = [IsAuthenticated, IsOwner]

    @transaction.atomic
    def post(self, request):
        serializer = CreateUserSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data
        tenant_id = data["tenantId"]

        # Ensure owner belongs to tenant
        tenant, error_message, error_status = authorize_tenant_access(
            request,
            tenant_id,
            required_role="OWNER",
        )
        if error_message:
            return Response(
                {"error": error_message},
                status=error_status,
            )

        max_users = get_subscription_limit(tenant, "users")
        current_user_count = UserTenant.objects.filter(tenant_id=tenant_id).count()
        if max_users is not None and current_user_count >= max_users:
            return Response(
                {"error": f"Current subscription plan allows only {max_users} users for this tenant"},
                status=status.HTTP_403_FORBIDDEN,
            )

        temp_password = generate_temp_password()

        user, created = User.objects.get_or_create(
            email=data["email"],
            defaults={
                "name": data["name"],
                "department": data["department"],
                "must_change_password": True
            }
        )

        if created:
            user.set_password(temp_password)
            user.save()
        else:
            return Response(
                {"error": "User with this email already exists"},
                status=status.HTTP_400_BAD_REQUEST
            )

        UserTenant.objects.create(
            user=user,
            tenant_id=tenant_id,
            role=data["role"]
        )
        tenant = tenant or Tenant.objects.only("id", "name").filter(id=tenant_id).first()

        email_sent = True
        email_links = {}
        email_error = None
        try:
            email_links = _send_new_user_credentials_email(
                user=user,
                tenant=tenant or Tenant(id=tenant_id, name="your pharmacy"),
                temp_password=temp_password,
            )
        except Exception as exc:  # pragma: no cover - depends on email backend configuration
            email_sent = False
            email_error = str(exc)
            logger.exception("Failed to send new user credentials email to %s", user.email)

        response_payload = {
            "message": "User created successfully",
            "data": {
                "id": str(user.id),
                "name": user.name,
                "email": user.email,
                "department": user.department,
                "role": data["role"],
                "tenantId": str(tenant_id),
                "temporaryPassword": temp_password,
            },
            "credentialsEmailSent": email_sent,
            "links": email_links,
        }
        if not email_sent:
            response_payload["warning"] = "User created, but credentials email was not sent"
            response_payload["emailError"] = email_error

        return Response(response_payload, status=status.HTTP_201_CREATED)


class OwnerUserListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        tenant_id = request.query_params.get("tenantId")

        if not tenant_id:
            return Response(
                {"detail": "tenantId is required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # validate tenant belongs to owner
        try:
            user_tenant = UserTenant.objects.get(
                user=request.user,
                tenant_id=tenant_id,
                role="OWNER"
            )
        except UserTenant.DoesNotExist:
            return Response(
                {"detail": "Unauthorized tenant access"},
                status=status.HTTP_403_FORBIDDEN
            )

        # Get all users for this tenant
        user_tenants = UserTenant.objects.filter(
            tenant_id=tenant_id
        ).select_related("user")

        users_data = []
        for ut in user_tenants:
            users_data.append({
                "id": str(ut.user.id),
                "name": ut.user.name,
                "email": ut.user.email,
                "department": ut.user.department,
                "role": ut.role,
                "created_at": ut.user.created_at
            })

        return Response(users_data, status=status.HTTP_200_OK)


class OwnerUpdateUserView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, user_id):
        tenant_id = request.query_params.get("tenantId")

        if not tenant_id:
            return Response(
                {"detail": "tenantId is required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            user_tenant = UserTenant.objects.get(
                user_id=user_id,
                tenant_id=tenant_id
            )
            user = user_tenant.user
        except UserTenant.DoesNotExist:
            return Response(
                {"detail": "User not found"},
                status=status.HTTP_404_NOT_FOUND
            )

        if user_tenant.role == "OWNER":
            return Response(
                {"detail": "Cannot modify OWNER"},
                status=status.HTTP_403_FORBIDDEN
            )

        serializer = UserUpdateSerializer(
            user,
            data=request.data,
            partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()

        return Response(
            {"message": "User updated successfully"},
            status=status.HTTP_200_OK
        )


class OwnerUserStatusView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, user_id):
        tenant_id = request.query_params.get("tenantId")
        is_active = request.data.get("is_active")

        if is_active is None:
            return Response(
                {"detail": "is_active is required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            user_tenant = UserTenant.objects.get(
                user_id=user_id,
                tenant_id=tenant_id
            )
            user = user_tenant.user
        except UserTenant.DoesNotExist:
            return Response(
                {"detail": "User not found"},
                status=status.HTTP_404_NOT_FOUND
            )

        if user_tenant.role == "OWNER":
            return Response(
                {"detail": "Cannot deactivate OWNER"},
                status=status.HTTP_403_FORBIDDEN
            )

        user.is_active = is_active
        user.save()

        return Response(
            {"message": "User status updated"},
            status=status.HTTP_200_OK
        )


class OwnerResetUserPasswordView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, user_id):
        tenant_id = request.query_params.get("tenantId")
        password = request.data.get("password")
        confirm = request.data.get("confirmPassword")

        if password != confirm:
            return Response(
                {"detail": "Passwords do not match"},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            user_tenant = UserTenant.objects.get(
                user_id=user_id,
                tenant_id=tenant_id
            )
            user = user_tenant.user
        except UserTenant.DoesNotExist:
            return Response(
                {"detail": "User not found"},
                status=status.HTTP_404_NOT_FOUND
            )

        user.set_password(password)
        user.must_change_password = True
        user.save()

        return Response(
            {"message": "Password reset successfully"},
            status=status.HTTP_200_OK
        )


class UsersSummaryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        tenant_id = request.query_params.get("tenantId")
        
        if not tenant_id:
            return Response({"detail": "tenantId is required"}, status=400)
            
        tenant = get_object_or_404(Tenant, id=tenant_id)

        # Ensure user belongs to tenant
        if not UserTenant.objects.filter(
            user=request.user,
            tenant=tenant
        ).exists():
            return Response(
                {"detail": "Not authorized for this tenant"},
                status=403
            )

        qs = UserTenant.objects.filter(tenant=tenant)

        total_users = qs.count()
        active_users = qs.filter(user__is_active=True).count()
        inactive_users = qs.filter(user__is_active=False).count()

        roles = (
            qs.values("role")
            .annotate(count=Count("id"))
        )

        by_role = {r["role"]: r["count"] for r in roles}

        return Response({
            "totalUsers": total_users,
            "activeUsers": active_users,
            "inactiveUsers": inactive_users,
            "byRole": by_role
        })


class OwnerUsersDashboardView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description=(
            "Owner users consolidated payload (summary cards + filtered user list + action endpoints)"
        ),
        tags=["owner"],
    )
    def get(self, request):
        tenant_id = request.query_params.get("tenantId")
        if not tenant_id:
            return Response({"detail": "tenantId is required"}, status=status.HTTP_400_BAD_REQUEST)

        if not UserTenant.objects.filter(user=request.user, tenant_id=tenant_id, role="OWNER").exists():
            return Response({"detail": "Unauthorized tenant access"}, status=status.HTTP_403_FORBIDDEN)

        search = request.query_params.get("search", "").strip()
        role = request.query_params.get("role")
        status_filter = request.query_params.get("status")

        try:
            page = int(request.query_params.get("page", 1))
            page_size = int(request.query_params.get("pageSize", 10))
        except ValueError:
            return Response(
                {"detail": "page and pageSize must be integers"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if page <= 0 or page_size <= 0:
            return Response(
                {"detail": "page and pageSize must be greater than 0"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        base_qs = UserTenant.objects.filter(tenant_id=tenant_id).select_related("user")
        role_counts = {
            row["role"]: row["count"]
            for row in base_qs.values("role").annotate(count=Count("id"))
        }

        filtered_qs = base_qs
        if role:
            filtered_qs = filtered_qs.filter(role=role)

        if status_filter:
            if status_filter.lower() == "active":
                filtered_qs = filtered_qs.filter(user__is_active=True)
            elif status_filter.lower() == "inactive":
                filtered_qs = filtered_qs.filter(user__is_active=False)

        if search:
            filtered_qs = filtered_qs.filter(
                Q(user__name__icontains=search)
                | Q(user__email__icontains=search)
                | Q(role__icontains=search)
            )

        paginator = Paginator(filtered_qs.order_by("-user__created_at"), page_size)
        page_obj = paginator.get_page(page)

        results = []
        for membership in page_obj:
            results.append(
                {
                    "id": str(membership.user.id),
                    "name": membership.user.name,
                    "email": membership.user.email,
                    "role": membership.role,
                    "status": "Active" if membership.user.is_active else "Inactive",
                    "createdAt": membership.user.created_at,
                }
            )

        return Response(
            {
                "summary": {
                    "totalUsers": base_qs.count(),
                    "activeUsers": base_qs.filter(user__is_active=True).count(),
                    "inactiveUsers": base_qs.filter(user__is_active=False).count(),
                    "owners": role_counts.get("OWNER", 0),
                    "cashiers": role_counts.get("CASHIER", 0),
                    "storeKeepers": role_counts.get("STORE_KEEPER", 0),
                    "accountants": role_counts.get("ACCOUNTANT", 0),
                    "pharmacists": role_counts.get("PHARMACIST", 0),
                },
                "list": {
                    "count": paginator.count,
                    "next": page + 1 if page_obj.has_next() else None,
                    "previous": page - 1 if page_obj.has_previous() else None,
                    "results": results,
                },
                "actions": {
                    "createUser": "/api/owner/create-user/",
                    "updateUser": "/api/owner/users/{user_id}/?tenantId={tenantId}",
                    "changeStatus": "/api/owner/users/{user_id}/status/?tenantId={tenantId}",
                    "resetPassword": "/api/owner/users/{user_id}/reset-password/?tenantId={tenantId}",
                },
            },
            status=status.HTTP_200_OK,
        )


class SearchUsersView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        tenant_id = request.query_params.get("tenantId")
        
        if not tenant_id:
            return Response({"detail": "tenantId is required"}, status=400)
            
        tenant = Tenant.objects.filter(id=tenant_id, is_active=True).first()
        if not tenant:
            return Response({"detail": "Tenant not found"}, status=404)

        # Check user belongs to tenant
        if not UserTenant.objects.filter(
            user=request.user,
            tenant=tenant
        ).exists():
            return Response({"detail": "Forbidden"}, status=403)

        query = request.GET.get("query", "").strip()
        role = request.GET.get("role")
        page = int(request.GET.get("page", 1))
        page_size = int(request.GET.get("page_size", 10))

        users = User.objects.filter(
            user_tenants__tenant=tenant
        ).distinct()

        # Text search
        if query:
            users = users.filter(
                Q(name__icontains=query) |
                Q(email__icontains=query) |
                Q(user_code__icontains=query)
            )

        # Role filter
        if role:
            users = users.filter(
                user_tenants__role=role
            )

        paginator = Paginator(users.order_by("-created_at"), page_size)
        page_obj = paginator.get_page(page)

        data = []
        for user in page_obj:
            membership = user.user_tenants.filter(
                tenant=tenant
            ).first()

            data.append({
                "id": str(user.id),
                "user_code": user.user_code,
                "name": user.name,
                "email": user.email,
                "role": membership.role if membership else None,
                "is_active": user.is_active,
                "created_at": user.created_at,
            })

        return Response({
            "results": data,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total_pages": paginator.num_pages,
                "total_items": paginator.count
            }
        })


class RolesListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        roles = ["OWNER", "ADMIN", "STAFF"]
        return Response({
            "roles": roles
        })
