from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Send a test email using current Django EMAIL_* settings"

    def add_arguments(self, parser):
        parser.add_argument(
            "--to",
            dest="to_email",
            required=True,
            help="Recipient email address",
        )
        parser.add_argument(
            "--subject",
            dest="subject",
            default="SMTP Test Email",
            help="Email subject",
        )
        parser.add_argument(
            "--message",
            dest="message",
            default="This is a test email from Pharmacy API SMTP configuration.",
            help="Email body",
        )

    def handle(self, *args, **options):
        to_email = options["to_email"]
        subject = options["subject"]
        message = options["message"]
        from_email = getattr(settings, "DEFAULT_FROM_EMAIL", None) or settings.EMAIL_HOST_USER

        if not from_email:
            raise CommandError(
                "DEFAULT_FROM_EMAIL and EMAIL_HOST_USER are both empty. Set one in your .env."
            )

        try:
            sent_count = send_mail(
                subject=subject,
                message=message,
                from_email=from_email,
                recipient_list=[to_email],
                fail_silently=False,
            )
        except Exception as exc:
            raise CommandError(f"Failed to send test email: {exc}")

        if sent_count != 1:
            raise CommandError(f"Expected to send 1 email, sent {sent_count}.")

        self.stdout.write(
            self.style.SUCCESS(
                f"Test email sent successfully to {to_email} using {settings.EMAIL_BACKEND}."
            )
        )
