from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ..serializers import UserFirebaseTokenSerializer


class UserFirebaseTokenView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request):
        serializer = UserFirebaseTokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        request.user.firebase_token = serializer.validated_data["firebase_token"]
        request.user.save(update_fields=["firebase_token"])  # type: ignore[union-attr]

        return Response({"message": "Firebase token registered successfully"}, status=status.HTTP_200_OK)
