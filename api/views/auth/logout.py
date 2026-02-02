from rest_framework.views import APIView
from rest_framework.response import Response


class LogoutView(APIView):
    def post(self, request):
        return Response({
            "success": True,
            "message": "Logged out successfully"
        })