from rest_framework import serializers
from .models import User, Tenant
from django.contrib.auth import get_user_model

User = get_user_model()

class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ('id', 'email', 'name', 'password')
        

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


# serializer for creating a user

class CreateUserSerializer(serializers.Serializer):
    tenantId = serializers.UUIDField()
    name = serializers.CharField()
    email = serializers.EmailField()
    role = serializers.ChoiceField(choices=[
        "CASHIER",
        "STORE_KEEPER",
        "ACCOUNTANT",
        "PHARMACIST"
    ])

#user serializer for listing users

class UserListSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'email', 'name', 'role', 'is_active', 'created_at']


# users/serializers.py

class UserUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["name", "email", "role"]

