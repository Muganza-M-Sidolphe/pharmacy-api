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