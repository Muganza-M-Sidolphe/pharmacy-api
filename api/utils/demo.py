from django.conf import settings


def is_demo_user(user):
    """Demo accounts (DEMO_ROLE_SWITCH_EMAILS) can switch between all roles."""
    return bool(user and user.email and user.email.lower() in settings.DEMO_ROLE_SWITCH_EMAILS)
