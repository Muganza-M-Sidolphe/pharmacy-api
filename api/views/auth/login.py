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

        # Single pharmacy → auto login
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

        # Multiple pharmacies → choose
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