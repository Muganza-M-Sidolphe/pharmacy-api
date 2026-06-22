from decimal import Decimal

from django.utils import timezone


MONEY_ZERO = Decimal("0.00")


def money(value):
    return str((value or MONEY_ZERO).quantize(Decimal("0.01")))


def percent(value):
    return round(float(value or Decimal("0.00")), 2)


def display_label(value, fallback="Uncategorized"):
    value = (value or "").strip()
    return value or fallback


def report_period(start_date=None, end_date=None):
    if start_date and end_date:
        return f"For {start_date} to {end_date}"
    if start_date:
        return f"From {start_date}"
    if end_date:
        return f"Until {end_date}"
    return f"For {timezone.now().strftime('%B %Y')}"


def report_branding(tenant):
    return {
        "name": tenant.name,
        "address": tenant.address,
        "phone": tenant.phone,
        "email": tenant.email,
        "licenseNumber": tenant.license_number,
        "country": tenant.country,
        "currency": tenant.currency,
    }


def report_theme():
    return {
        "primaryColor": "#0f8f3a",
        "accentColor": "#eaf6ed",
        "borderColor": "#a9d2b3",
        "textColor": "#1f2a24",
        "mutedTextColor": "#52645a",
        "pageSize": "A4",
    }
