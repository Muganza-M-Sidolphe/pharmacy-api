# Legacy views for multi-tenant user management system

from django.shortcuts import render
from django.db import transaction
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.contrib.auth import authenticate
from rest_framework_simplejwt.tokens import RefreshToken
from .models import User, Tenant, UserTenant
from .serializers import RegisterTenantSerializer
from .utils.jwt import generate_token


class RegisterTenantView(APIView):
    """Combined registration for tenant and owner/pharmacist in one API."""
    authentication_classes = []
    permission_classes = []

    @transaction.atomic
    def post(self, request):
        data = request.data

        required_fields = [
            "tenantName", "tenantEmail", "tenantPhone",
            "address", "licenseNumber", "country",
            "ownerName", "ownerEmail", "password", "confirmPassword",
            "pharmacyType"  # "WHOLESALE" or "RETAIL"
        ]

        for field in required_fields:
            if not data.get(field):
                return Response(
                    {"message": f"{field} is required"},
                    status=status.HTTP_400_BAD_REQUEST
                )

        # Validate pharmacy type
        pharmacy_type = data.get("pharmacyType", "").upper()
        if pharmacy_type not in ["WHOLESALE", "RETAIL"]:
            return Response(
                {"message": "pharmacyType must be either WHOLESALE or RETAIL"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Validate passwords match
        if data["password"] != data["confirmPassword"]:
            return Response(
                {"message": "Passwords do not match"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Check if tenant email exists
        if Tenant.objects.filter(email=data["tenantEmail"]).exists():
            return Response(
                {"message": "Tenant email already exists"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Check if user email exists
        if User.objects.filter(email=data["ownerEmail"]).exists():
            return Response(
                {"message": "User email already exists"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Auto currency by country
        country_currency = {
            "RW": "RWF",
            "KE": "KES",
            "UG": "UGX"
        }
        currency = data.get("currency") or country_currency.get(data["country"], "USD")

        # Create tenant
        tenant = Tenant.objects.create(
            name=data["tenantName"],
            email=data["tenantEmail"],
            phone=data["tenantPhone"],
            address=data["address"],
            license_number=data["licenseNumber"],
            country=data["country"],
            currency=currency
        )

        # Create user
        user = User.objects.create_user(
            email=data["ownerEmail"],
            name=data["ownerName"],
            password=data["password"],
            department=pharmacy_type,
        )

        # Assign role based on pharmacy type
        # WHOLESALE -> OWNER (full access)
        # RETAIL -> PHARMACIST (limited access)
        role = "OWNER" if pharmacy_type == "WHOLESALE" else "PHARMACIST"

        UserTenant.objects.create(
            user=user,
            tenant=tenant,
            role=role
        )

        # Generate JWT token
        token = generate_token(
            user=user,
            tenant=tenant,
            role=role
        )

        return Response({
            "message": "Registration successful",
            "data": {
                "user": {
                    "id": str(user.id),
                    "name": user.name,
                    "email": user.email,
                    "department": user.department,
                    "role": role
                },
                "tenant": {
                    "id": str(tenant.id),
                    "name": tenant.name,
                    "pharmacyType": pharmacy_type,
                    "currency": tenant.currency
                },
                "token": token
            }
        }, status=status.HTTP_201_CREATED)


class RegisterOwnerView(APIView):
    """Deprecated: Use RegisterTenantView instead."""
    authentication_classes = []
    permission_classes = []

    @transaction.atomic
    def post(self, request):
        return Response(
            {"message": "This endpoint is deprecated. Use /api/register-tenant/ instead."},
            status=status.HTTP_410_GONE
        )
