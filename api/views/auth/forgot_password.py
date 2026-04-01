from datetime import timedelta
import secrets

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from ...models import PasswordResetToken, User


def _reset_frontend_url(token, email):
    base_url = getattr(settings, "FRONTEND_BASE_URL", "http://localhost:5173").rstrip("/")
    return f"{base_url}/reset-password?token={token}&email={email}"


class ForgotPasswordView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        email = (request.data.get("email") or "").strip().lower()
        if not email:
            return Response({"message": "email is required"}, status=status.HTTP_400_BAD_REQUEST)

        user = User.objects.filter(email=email, is_active=True).first()
        if user:
            PasswordResetToken.objects.filter(user=user, used_at__isnull=True).delete()
            token = secrets.token_urlsafe(32)
            expires_at = timezone.now() + timedelta(hours=1)
            PasswordResetToken.objects.create(
                user=user,
                token=token,
                expires_at=expires_at,
            )

            reset_url = _reset_frontend_url(token, user.email)
            subject = "Reset your password"
            message = (
                f"Hello {user.name},\n\n"
                "We received a request to reset your password.\n\n"
                f"Reset link: {reset_url}\n\n"
                "This link expires in 1 hour.\n"
                "If you did not request this, you can ignore this email."
            )
            from_email = getattr(settings, "DEFAULT_FROM_EMAIL", None) or getattr(settings, "EMAIL_HOST_USER", "") or "no-reply@pharmacy.local"
            send_mail(subject, message, from_email, [user.email], fail_silently=False)

        return Response(
            {
                "message": "If an account with that email exists, a password reset link has been sent."
            },
            status=status.HTTP_200_OK,
        )


class ResetPasswordView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        token = (request.data.get("token") or "").strip()
        email = (request.data.get("email") or "").strip().lower()
        new_password = request.data.get("new_password")
        confirm_password = request.data.get("confirm_password")

        if not all([token, email, new_password, confirm_password]):
            return Response({"message": "All fields are required"}, status=status.HTTP_400_BAD_REQUEST)

        if new_password != confirm_password:
            return Response({"message": "Passwords do not match"}, status=status.HTTP_400_BAD_REQUEST)

        reset_token = (
            PasswordResetToken.objects.select_related("user")
            .filter(token=token, user__email=email, used_at__isnull=True)
            .first()
        )
        if not reset_token:
            return Response({"message": "Invalid or expired reset token"}, status=status.HTTP_400_BAD_REQUEST)

        if reset_token.expires_at < timezone.now():
            return Response({"message": "Invalid or expired reset token"}, status=status.HTTP_400_BAD_REQUEST)

        user = reset_token.user
        user.set_password(new_password)
        user.must_change_password = False
        user.save(update_fields=["password", "must_change_password"])

        reset_token.used_at = timezone.now()
        reset_token.save(update_fields=["used_at"])

        PasswordResetToken.objects.filter(user=user, used_at__isnull=True).exclude(id=reset_token.id).delete()

        return Response({"message": "Password reset successfully"}, status=status.HTTP_200_OK)
