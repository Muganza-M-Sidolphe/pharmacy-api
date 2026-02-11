from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.contrib.auth import authenticate
from ...models import UserTenant
from ...utils.jwt import generate_token


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

        # Check if user must change password (first login with temp password)
        if user.must_change_password:
            return Response({
                "status": "MUST_CHANGE_PASSWORD",
                "message": "You must change your password before continuing",
                "userId": str(user.id),
                "email": user.email
            })

        # Get all user tenants (not just OWNER)
        user_tenants = UserTenant.objects.filter(
            user=user
        ).select_related("tenant")

        if not user_tenants.exists():
            return Response(
                {"message": "No pharmacy assigned"},
                status=status.HTTP_403_FORBIDDEN
            )

        # Single tenant → auto login
        if user_tenants.count() == 1:
            ut = user_tenants.first()
            token = generate_token(
                user=user,
                tenant=ut.tenant,
                role=ut.role
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
                    "role": ut.role
                }
            })

        # Multiple tenants → choose
        temp_token = generate_token(user=user)  # no tenant

        return Response({
            "status": "CHOOSE_TENANT",
            "tenants": [
                {
                    "id": str(ut.tenant.id),
                    "name": ut.tenant.name,
                    "role": ut.role
                }
                for ut in user_tenants
            ],
            "tempToken": temp_token
        })