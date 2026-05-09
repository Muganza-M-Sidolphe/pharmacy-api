from django.core.management.base import BaseCommand

from api.utils.expiry_alerts import send_expiry_alerts


class Command(BaseCommand):
    help = "Send expiry alert notifications and emails for batches expiring soon"

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=30,
            help="Number of days ahead to treat as expiring soon (default: 30)",
        )

    def handle(self, *args, **options):
        summary = send_expiry_alerts(days=options["days"])
        self.stdout.write(
            self.style.SUCCESS(
                "Expiry alerts complete. "
                f"tenant_notifications={summary['tenant_notifications']}, "
                f"tenant_emails_sent={summary['tenant_emails_sent']}, "
                f"tenant_emails_failed={summary['tenant_emails_failed']}, "
                f"retail_notifications={summary['retail_notifications']}, "
                f"retail_emails_sent={summary['retail_emails_sent']}, "
                f"retail_emails_failed={summary['retail_emails_failed']}"
            )
        )
