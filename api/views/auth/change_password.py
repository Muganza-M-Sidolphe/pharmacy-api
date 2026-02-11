from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.contrib.auth import authenticate
from ...models import User, UserTenant
from ...utils.jwt import generate_token


class ChangePasswordView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        email = request.data.get("email")
        old_password = request.data.get("old_password")
        new_password = request.data.get("new_password")
        confirm_password = request.data.get("confirm_password")

        if not all([email, old_password, new_password, confirm_password]):
            return Response(
                {"message": "All fields are required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        if new_password != confirm_password:
            return Response(
                {"message": "New passwords do not match"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Authenticate with old password
        user = authenticate(email=email, password=old_password)
        if not user:
            return Response(
                {"message": "Invalid credentials"},
                status=status.HTTP_401_UNAUTHORIZED
            )

        # Set new password
        user.set_password(new_password)
        user.must_change_password = False
        user.save()

        # Get user tenant and generate token
        user_tenant = UserTenant.objects.filter(user=user).select_related("tenant").first()
        
        if not user_tenant:
            return Response(
                {"message": "No pharmacy assigned"},
                status=status.HTTP_403_FORBIDDEN
            )

        token = generate_token(
            user=user,
            tenant=user_tenant.tenant,
            role=user_tenant.role
        )

        return Response({
            "message": "Password changed successfully",
            "data": {
                "token": token,
                "user": {
                    "id": str(user.id),
                    "name": user.name,
                    "email": user.email,
                    "role": user_tenant.role
                },
                "tenant": {
                    "id": str(user_tenant.tenant.id),
                    "name": user_tenant.tenant.name
                }
            }
        })
