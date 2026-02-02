# views for multi-tenant user management system

from django.shortcuts import render
from django.db import transaction
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.contrib.auth import authenticate
from rest_framework_simplejwt.tokens import RefreshToken
from .models import User, Tenant, UserTenant
from .serializers import RegisterTenantSerializer, UserSerializer,UserListSerializer,UserUpdateSerializer
from .utils.jwt import generate_token
from rest_framework.permissions import IsAuthenticated
from .serializers import CreateUserSerializer
from .permissions import IsOwner
from .utils.password import generate_temp_password
from django.shortcuts import get_object_or_404
from django.db.models import Count
from .utils.constants import ROLE_PERMISSIONS
from .utils.constants import ALL_ROLES
from django.db.models import Q
from django.core.paginator import Paginator
from .utils.permissions import has_permission   
# Register Tenant View

class RegisterTenantView(APIView):
    authentication_classes = []
    permission_classes = []

    @transaction.atomic
    def post(self, request):
        data = request.data

        required_fields = [
            "tenantName", "tenantEmail", "tenantPhone",
            "address", "licenseNumber", "country"
        ]

        for field in required_fields:
            if not data.get(field):
                return Response(
                    {"message": f"{field} is required"},
                    status=status.HTTP_400_BAD_REQUEST
                )

        if Tenant.objects.filter(email=data["tenantEmail"]).exists():
            return Response(
                {"message": "Tenant email already exists"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Auto currency by country (example)
        country_currency = {
            "RW": "RWF",
            "KE": "KES",
            "UG": "UGX"
        }

        currency = data.get("currency") or country_currency.get(data["country"], "USD")

        tenant = Tenant.objects.create(
            name=data["tenantName"],
            email=data["tenantEmail"],
            phone=data["tenantPhone"],
            address=data["address"],
            license_number=data["licenseNumber"],
            country=data["country"],
            currency=currency
        )

        return Response({
            "message": "Tenant created successfully",
            "data": {
                "tenantId": str(tenant.id),
                "tenantName": tenant.name,
                "currency": tenant.currency
            }
        }, status=status.HTTP_201_CREATED)
    
    # Register owner view

class RegisterOwnerView(APIView):
    authentication_classes = []
    permission_classes = []

    @transaction.atomic
    def post(self, request):
        data = request.data

        required_fields = [
            "tenantId", "ownerName",
            "ownerEmail", "password", "confirmPassword"
        ]

        for field in required_fields:
            if not data.get(field):
                return Response(
                    {"message": f"{field} is required"},
                    status=status.HTTP_400_BAD_REQUEST
                )

        if data["password"] != data["confirmPassword"]:
            return Response(
                {"message": "Passwords do not match"},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            tenant = Tenant.objects.get(id=data["tenantId"])
        except Tenant.DoesNotExist:
            return Response(
                {"message": "Invalid tenant"},
                status=status.HTTP_404_NOT_FOUND
            )

        if User.objects.filter(email=data["ownerEmail"]).exists():
            return Response(
                {"message": "User email already exists"},
                status=status.HTTP_400_BAD_REQUEST
            )

        user = User.objects.create_user(
            email=data["ownerEmail"],
            name=data["ownerName"],
            password=data["password"]
        )

        UserTenant.objects.create(
            user=user,
            tenant=tenant,
            role="OWNER"
        )

        token = generate_token(
            user=user,
            tenant=tenant,
            role="OWNER"
        )

        return Response({
            "message": "Owner registered successfully",
            "data": {
                "user": {
                    "id": str(user.id),
                    "name": user.name,
                    "email": user.email,
                    "role": "OWNER",
                    "tenant_id": str(tenant.id),
                    "tenant_name": tenant.name
                },
                "token": token
            }
        }, status=status.HTTP_201_CREATED)
    

# Create user view

class CreateUserView(APIView):
    permission_classes = [IsAuthenticated, IsOwner]

    @transaction.atomic
    def post(self, request):
        serializer = CreateUserSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data
        tenant_id = data["tenantId"]

        # Ensure owner belongs to tenant
        if not UserTenant.objects.filter(
            user=request.user,
            tenant_id=tenant_id,
            role="OWNER"
        ).exists():
            return Response(
                {"error": "You do not own this pharmacy"},
                status=status.HTTP_403_FORBIDDEN
            )

        temp_password = generate_temp_password()

        user, created = User.objects.get_or_create(
            email=data["email"],
            defaults={
                "name": data["name"],
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

        return Response({
            "message": "User created successfully",
            "data": {
                "id": str(user.id),
                "name": user.name,
                "email": user.email,
                "role": data["role"],
                "tenantId": str(tenant_id)
            },
            "temporaryPassword": temp_password
        }, status=status.HTTP_201_CREATED)

 

  # Login View  


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

        # Owner only
        user_tenants = UserTenant.objects.filter(
            user=user,
            role="OWNER"
        ).select_related("tenant")

        if not user_tenants.exists():
            return Response(
                {"message": "No pharmacy assigned"},
                status=status.HTTP_403_FORBIDDEN
            )

        # 🔹 Single pharmacy → auto login
        if user_tenants.count() == 1:
            ut = user_tenants.first()
            token = generate_token(
                user=user,
                tenant=ut.tenant,
                role="OWNER"
            )

            return Response({
                "status": "OK",
                "mode": "AUTO",
                "data": {
                    "token": token,
                    "tenant": {
                        "id": str(ut.tenant.id),
                        "name": ut.tenant.name
                    },
                    "role": "OWNER"
                }
            })

        # 🔹 Multiple pharmacies → choose
        temp_token = generate_token(user=user)  # no tenant

        return Response({
            "status": "CHOOSE_TENANT",
            "tenants": [
                {
                    "id": str(ut.tenant.id),
                    "name": ut.tenant.name
                }
                for ut in user_tenants
            ],
            "tempToken": temp_token
        })

# Select tenant after login

class SelectTenantView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        tenant_id = request.data.get("tenantId")

        if not tenant_id:
            return Response(
                {"message": "tenantId is required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            user_tenant = UserTenant.objects.select_related("tenant").get(
                user=request.user,
                tenant_id=tenant_id
            )
        except UserTenant.DoesNotExist:
            return Response(
                {"message": "You do not have access to this pharmacy"},
                status=status.HTTP_403_FORBIDDEN
            )

        tenant = user_tenant.tenant

        token = generate_token(
            user=request.user,
            tenant=tenant,
            role=user_tenant.role
        )

        return Response({
            "message": "Tenant selected successfully",
            "data": {
                "user": {
                    "id": str(request.user.id),
                    "name": request.user.name,
                    "email": request.user.email,
                    "role": user_tenant.role,
                    "tenant_id": str(tenant.id),
                    "tenant_name": tenant.name
                },
                "token": token
            }
        })

# User list View


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
                "role": ut.role,
                "created_at": ut.user.created_at
            })

        return Response(users_data, status=status.HTTP_200_OK)
    
# Update User info View  


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
            user = User.objects.get(
                id=user_id,
                tenant_id=tenant_id
            )
        except User.DoesNotExist:
            return Response(
                {"detail": "User not found"},
                status=status.HTTP_404_NOT_FOUND
            )

        if user.role == "OWNER":
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


# User status view

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
            user = User.objects.get(
                id=user_id,
                tenant_id=tenant_id
            )
        except User.DoesNotExist:
            return Response(
                {"detail": "User not found"},
                status=status.HTTP_404_NOT_FOUND
            )

        if user.role == "OWNER":
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

# Reset User Password View

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
            user = User.objects.get(
                id=user_id,
                tenant_id=tenant_id
            )
        except User.DoesNotExist:
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

# User summary View

class UsersSummaryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, tenant_id):
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


# Permission classes



class MyPermissionsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, tenant_id):
        try:
            tenant = Tenant.objects.get(id=tenant_id, is_active=True)
        except Tenant.DoesNotExist:
            return Response(
                {"detail": "Tenant not found"},
                status=status.HTTP_404_NOT_FOUND
            )

        membership = UserTenant.objects.filter(
            user=request.user,
            tenant=tenant,
            is_active=True
        ).first()

        if not membership:
            return Response(
                {"detail": "Access denied for this tenant"},
                status=status.HTTP_403_FORBIDDEN
            )

        permissions = ROLE_PERMISSIONS.get(membership.role, [])

        return Response({
            "tenant_id": str(tenant.id),
            "role": membership.role,
            "permissions": permissions
        })



# Roles List View

class RolesListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({
            "roles": ALL_ROLES
        })


# Search Users View

class SearchUsersView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, tenant_id):
        # 🔐 Permission check
        tenant = Tenant.objects.filter(id=tenant_id, is_active=True).first()
        if not tenant:
            return Response({"detail": "Tenant not found"}, status=404)

        if not has_permission(request.user, tenant, "manage_users"):
            return Response({"detail": "Forbidden"}, status=403)

        query = request.GET.get("query", "").strip()
        role = request.GET.get("role")
        page = int(request.GET.get("page", 1))
        page_size = int(request.GET.get("page_size", 10))

        users = User.objects.filter(
            user_tenants__tenant=tenant,
            user_tenants__is_active=True
        ).distinct()

        # 🔎 Text search
        if query:
            users = users.filter(
                Q(name__icontains=query) |
                Q(email__icontains=query) |
                Q(user_code__icontains=query)
            )

        # 🎭 Role filter
        if role:
            users = users.filter(
                user_tenants__role=role
            )

        paginator = Paginator(users.order_by("-created_at"), page_size)
        page_obj = paginator.get_page(page)

        data = []
        for user in page_obj:
            membership = user.user_tenants.filter(
                tenant=tenant,
                is_active=True
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
#logout View


class LogoutView(APIView):
    def post(self, request):
        return Response({
            "success": True,
            "message": "Logged out successfully"
        })
    

