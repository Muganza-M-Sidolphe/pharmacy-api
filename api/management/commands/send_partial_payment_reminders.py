from django.core.management.base import BaseCommand
from django.utils import timezone

from api.models import PartialPaymentReminderConfig, PartialPaymentReminderEvent
from api.utils.reminders import send_partial_payment_email, send_partial_payment_sms


class Command(BaseCommand):
    help = "Send automatic reminders for partial invoices based on reminder schedule"

    def handle(self, *args, **options):
        today = timezone.now().date()
        sent_count = 0
        failed_count = 0
        skipped_count = 0

        configs = PartialPaymentReminderConfig.objects.select_related("sale").filter(
            is_active=True,
            auto_send_enabled=True,
            sale__payment_option="PARTIAL",
            sale__due_amount__gt=0,
        )

        for config in configs:
            sale = config.sale
            days_until_due = (config.due_date - today).days

            if days_until_due not in (config.reminder_days_before or []):
                continue

            channels = []
            if config.email_enabled:
                channels.append("EMAIL")
            if config.sms_enabled:
                channels.append("SMS")

            for channel in channels:
                already_sent = PartialPaymentReminderEvent.objects.filter(
                    config=config,
                    channel=channel,
                    mode="AUTO",
                    scheduled_for=today,
                    status="SENT",
                ).exists()
                if already_sent:
                    skipped_count += 1
                    continue

                if channel == "EMAIL":
                    success, error_message = send_partial_payment_email(
                        config.customer_email,
                        sale,
                        config.due_date,
                    )
                    recipient = config.customer_email
                else:
                    success, error_message = send_partial_payment_sms(
                        config.customer_phone or sale.customer_phone,
                        sale,
                        config.due_date,
                    )
                    recipient = config.customer_phone or sale.customer_phone

                event_status = "SENT" if success else "FAILED"
                PartialPaymentReminderEvent.objects.create(
                    tenant=config.tenant,
                    sale=sale,
                    config=config,
                    channel=channel,
                    mode="AUTO",
                    status=event_status,
                    scheduled_for=today,
                    recipient=recipient,
                    error_message=error_message,
                )

                if success:
                    sent_count += 1
                else:
                    failed_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Auto reminders complete. sent={sent_count}, failed={failed_count}, skipped={skipped_count}"
            )
        )
