import os

from django.apps import apps
from django.db.models.signals import post_migrate
from django.dispatch import receiver


@receiver(post_migrate)
def ensure_default_super_admin(sender, **kwargs):
    """
    Ensure a default super admin exists after migrations.
    This is useful on fresh/reset databases.
    """
    # Run once when api app migrations are applied.
    if sender.label != "api":
        return

    User = apps.get_model("api", "User")

    email = os.getenv("DEFAULT_SUPERADMIN_EMAIL", "admin@pharmacy.local").strip().lower()
    password = os.getenv("DEFAULT_SUPERADMIN_PASSWORD", "Admin12345!")
    name = os.getenv("DEFAULT_SUPERADMIN_NAME", "System Super Admin")

    user = User.objects.filter(email=email).first()
    if not user:
        user = User.objects.create_user(
            email=email,
            name=name,
            password=password,
        )

    # Make sure it is always usable as super admin.
    changed_fields = []
    if not user.is_super_admin:
        user.is_super_admin = True
        changed_fields.append("is_super_admin")
    if not user.is_superuser:
        user.is_superuser = True
        changed_fields.append("is_superuser")
    if not user.is_staff:
        user.is_staff = True
        changed_fields.append("is_staff")
    if not user.is_active:
        user.is_active = True
        changed_fields.append("is_active")

    # Keep login straightforward for bootstrap account.
    if user.must_change_password:
        user.must_change_password = False
        changed_fields.append("must_change_password")

    if changed_fields:
        user.save(update_fields=changed_fields)
