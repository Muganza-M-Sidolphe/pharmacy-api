import logging
from collections import defaultdict
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.db.models import Q
from django.utils import timezone

from api.models import Notification, StockBatch, UserTenant


logger = logging.getLogger(__name__)


def _expiring_batches_queryset(days=30):
    today = timezone.now().date()
    cutoff = today + timedelta(days=days)
    return (
        StockBatch.objects.select_related("medicine", "medicine__tenant", "created_by")
        .filter(
            quantity__gt=0,
            expiry_date__isnull=False,
            expiry_date__gte=today,
            expiry_date__lte=cutoff,
        )
        .order_by("expiry_date", "medicine__brand_name")
    )


def _users_for_roles(tenant_id, role_department_pairs):
    role_filter = Q()
    for role, department in role_department_pairs:
        clause = Q(role=role)
        if department:
            clause &= Q(user__department=department)
        role_filter |= clause
    memberships = (
        UserTenant.objects.filter(tenant_id=tenant_id)
        .filter(role_filter)
        .select_related("user")
    )
    seen = set()
    users = []
    for membership in memberships:
        user = membership.user
        if not user or not user.is_active or user.id in seen:
            continue
        seen.add(user.id)
        users.append(user)
    return users


def _render_batch_lines(batches, limit=10):
    lines = []
    for batch in list(batches)[:limit]:
        medicine_name = batch.medicine.brand_name if batch.medicine_id else "Unknown medicine"
        lines.append(
            f"- {medicine_name} | batch {batch.batch_number} | qty {batch.quantity} | expires {batch.expiry_date.isoformat()}"
        )
    remaining = max(len(batches) - limit, 0)
    if remaining:
        lines.append(f"- ...and {remaining} more batch(es)")
    return "\n".join(lines)


def _already_notified_today(tenant_id, recipient, title):
    today = timezone.now().date()
    qs = Notification.objects.filter(
        tenant_id=tenant_id,
        title=title,
        created_at__date=today,
    )
    if recipient is None:
        qs = qs.filter(recipient__isnull=True)
    else:
        qs = qs.filter(recipient=recipient)
    return qs.exists()


def _create_notifications(tenant_id, recipients, title, message):
    notifications = []
    seen = set()
    for recipient in recipients:
        if not recipient or recipient.id in seen:
            continue
        seen.add(recipient.id)
        if _already_notified_today(tenant_id, recipient, title):
            continue
        notifications.append(
            Notification(
                tenant_id=tenant_id,
                recipient=recipient,
                title=title,
                message=message,
            )
        )
    if notifications:
        Notification.objects.bulk_create(notifications)
    return len(notifications)


def _send_emails(recipients, subject, message):
    from_email = (
        getattr(settings, "DEFAULT_FROM_EMAIL", None)
        or getattr(settings, "EMAIL_HOST_USER", "")
        or "no-reply@pharmacy.local"
    )
    sent = 0
    failed = 0
    seen = set()
    for recipient in recipients:
        email = (getattr(recipient, "email", None) or "").strip().lower()
        if not email or email in seen:
            continue
        seen.add(email)
        try:
            send_mail(subject, message, from_email, [email], fail_silently=False)
            sent += 1
        except Exception:  # pragma: no cover - depends on mail backend
            failed += 1
            logger.exception("Failed to send expiry alert email to %s", email)
    return sent, failed


def send_expiry_alerts(days=30):
    batches = list(_expiring_batches_queryset(days=days))
    tenant_groups = defaultdict(list)
    retail_groups = defaultdict(list)

    for batch in batches:
        tenant_groups[batch.medicine.tenant_id].append(batch)
        if batch.created_by_id and getattr(batch.created_by, "department", None) == "RETAIL":
            retail_groups[batch.created_by_id].append(batch)

    summary = {
        "tenant_notifications": 0,
        "tenant_emails_sent": 0,
        "tenant_emails_failed": 0,
        "retail_notifications": 0,
        "retail_emails_sent": 0,
        "retail_emails_failed": 0,
    }

    for tenant_id, tenant_batches in tenant_groups.items():
        tenant = tenant_batches[0].medicine.tenant
        recipients = _users_for_roles(
            tenant_id,
            [("OWNER", "WHOLESALE"), ("STORE_KEEPER", "WHOLESALE")],
        )
        if not recipients:
            continue

        title = f"Expiry Alert: {len(tenant_batches)} batch(es) expiring within {days} days"
        message = (
            f"Hello,\n\n"
            f"The tenant {tenant.name} has {len(tenant_batches)} stock batch(es) expiring within the next {days} days.\n\n"
            f"{_render_batch_lines(tenant_batches)}\n\n"
            "Please review the expiry alerts dashboard and take action."
        )
        email_recipients = [
            recipient
            for recipient in recipients
            if not _already_notified_today(tenant_id, recipient, title)
        ]
        summary["tenant_notifications"] += _create_notifications(
            tenant_id,
            recipients,
            title,
            message,
        )
        sent, failed = _send_emails(email_recipients, title, message)
        summary["tenant_emails_sent"] += sent
        summary["tenant_emails_failed"] += failed

    for retail_user_id, user_batches in retail_groups.items():
        retail_user = user_batches[0].created_by
        tenant = user_batches[0].medicine.tenant
        title = f"Expiry Alert: Your retail stock has {len(user_batches)} batch(es) expiring soon"
        message = (
            f"Hello {retail_user.name},\n\n"
            f"You have {len(user_batches)} stock batch(es) in {tenant.name} expiring within the next {days} days.\n\n"
            f"{_render_batch_lines(user_batches)}\n\n"
            "Please review the expiring medicines section in your retail portal."
        )
        should_email = not _already_notified_today(tenant.id, retail_user, title)
        summary["retail_notifications"] += _create_notifications(
            tenant.id,
            [retail_user],
            title,
            message,
        )
        sent, failed = _send_emails([retail_user] if should_email else [], title, message)
        summary["retail_emails_sent"] += sent
        summary["retail_emails_failed"] += failed

    return summary
