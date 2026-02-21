from django.conf import settings
from django.core.mail import send_mail


def build_partial_payment_reminder_message(sale, due_date, custom_message=""):
    due_amount = str(sale.due_amount)
    customer_name = sale.customer_name or "Customer"
    tenant_name = sale.tenant.name if sale.tenant_id and sale.tenant else "your pharmacy"
    base_message = (
        f"Dear {customer_name}, this is {tenant_name}. "
        f"Your balance for invoice {sale.invoice_number} is {due_amount}. "
        f"Please pay by {due_date.isoformat()}."
    )
    if custom_message:
        return f"{base_message} {custom_message}".strip()
    return base_message


def send_partial_payment_email(recipient_email, sale, due_date, custom_message=""):
    if not recipient_email:
        return False, "Customer email is missing"

    tenant_name = sale.tenant.name if sale.tenant_id and sale.tenant else "Pharmacy"
    subject = f"Payment Reminder - {tenant_name} - Invoice {sale.invoice_number}"
    message = build_partial_payment_reminder_message(sale, due_date, custom_message=custom_message)
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", None) or "no-reply@pharmacy.local"

    try:
        send_mail(subject, message, from_email, [recipient_email], fail_silently=False)
        return True, None
    except Exception as exc:  # pragma: no cover - depends on configured email backend
        return False, str(exc)


def send_partial_payment_sms(recipient_phone, sale, due_date, custom_message=""):
    # Placeholder SMS sender: integrate Twilio/Africa's Talking/etc. in production.
    if not recipient_phone:
        return False, "Customer phone is missing"

    _ = build_partial_payment_reminder_message(sale, due_date, custom_message=custom_message)
    return True, None
