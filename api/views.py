# views for multi-tenant user management system

from django.shortcuts import render
from django.db import transaction
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from .models import User, Tenant, UserTenant
from .serializers import RegisterTenantSerializer, LoginSerializer
from .utils.jwt import generate_token


class RegisterTenantView(APIView):

    @transaction.atomic
    def post(self, request):
        serializer = RegisterTenantSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        if Tenant.objects.filter(email=data["tenantEmail"]).exists():
            return Response(
                {"error": "Tenant email already exists"},
                status=status.HTTP_400_BAD_REQUEST
            )

        tenant = Tenant.objects.create(
            name=data["tenantName"],
            email=data["tenantEmail"],
            phone=data["tenantPhone"],
            address=data["address"],
            license_number=data["licenseNumber"]
        )

        user = User.objects.create(
            name=data["ownerName"],
            email=data["ownerEmail"],
            password=data["password"]
        )

        UserTenant.objects.create(
            user=user,
            tenant=tenant,
            role="OWNER"
        )

        token = generate_token(
            user=user,
            tenant_id=tenant.id,
            role="OWNER"
        )       

        return Response({
            "message": "Tenant registered successfully",
            "data": {
                "tenant": {
                    "id": str(tenant.id),
                    "name": tenant.name,
                    "email": tenant.email
                },
                "user": {
                    "id": str(user.id),
                    "name": user.name,
                    "email": user.email,
                    "role": "OWNER"
                },
                "token": token
            }
        }, status=status.HTTP_201_CREATED)
    
  # Login View  

class LoginView(APIView):

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            user = User.objects.prefetch_related("user_tenants__tenant").get(email=data["email"])
        except User.DoesNotExist:
            return Response({"error": "Invalid credentials"}, status=401)

        if not user.is_active:
            return Response({"error": "Account is inactive"}, status=403)

        if not user.compare_password(data["password"]):
            return Response({"error": "Invalid credentials"}, status=401)

        # SUPER ADMIN
        if user.is_super_admin:
            tenants = Tenant.objects.values("id", "name", "email", "is_active")
            token = generate_token({
                "user": user,
                "role": "SUPER_ADMIN",
                "is_super_admin": True
            })

            return Response({
                "message": "Super admin login successful",
                "data": {
                    "user": {
                        "id": user.id,
                        "name": user.name,
                        "email": user.email,
                        "role": "SUPER_ADMIN",
                        "is_super_admin": True,
                        "must_change_password": user.must_change_password,
                        "all_tenants": list(tenants)
                    },
                    "token": token
                }
            })

        user_tenants = user.user_tenants.all()
        if not user_tenants.exists():
            return Response({"error": "No pharmacy access"}, status=401)

        pharmacy_id = data.get("pharmacyId")

        if pharmacy_id:
            user_tenant = user_tenants.filter(tenant_id=pharmacy_id).first()
            if not user_tenant:
                return Response({"error": "Access denied to this pharmacy"}, status=403)
        else:
            if user_tenants.count() > 1:
                pharmacies = [
                    {
                        "id": ut.tenant.id,
                        "name": ut.tenant.name,
                        "role": ut.role
                    } for ut in user_tenants
                ]
                return Response({
                    "message": "Multiple pharmacies found",
                    "requiresPharmacySelection": True,
                    "pharmacies": pharmacies
                })
            user_tenant = user_tenants.first()

        token = generate_token({
            "user": user,
            "tenantId": user_tenant.tenant.id,
            "role": user_tenant.role
        })

        response = {
            "message": "Login successful",
            "data": {
                "user": {
                    "id": user.id,
                    "name": user.name,
                    "email": user.email,
                    "role": user_tenant.role,
                    "tenant_id": user_tenant.tenant.id,
                    "must_change_password": user.must_change_password
                },
                "token": token
            }
        }

        if user.must_change_password:
            response["message"] = "Password change required"

        return Response(response)
    
#logout View


class LogoutView(APIView):
    def post(self, request):
        return Response({
            "success": True,
            "message": "Logged out successfully"
        })
    

