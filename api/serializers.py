from rest_framework import serializers
from .models import User, Tenant


class RegisterTenantSerializer(serializers.Serializer):
    tenantName = serializers.CharField()
    tenantEmail = serializers.EmailField()
    tenantPhone = serializers.CharField()
    address = serializers.CharField()
    licenseNumber = serializers.CharField()
    ownerName = serializers.CharField()
    ownerEmail = serializers.EmailField()
    password = serializers.CharField(write_only=True)


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)
    pharmacyId = serializers.UUIDField(required=False)
