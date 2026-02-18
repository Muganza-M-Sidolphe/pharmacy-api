import uuid
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0007_alter_usertenant_role_expensecategory_expense"),
    ]

    operations = [
        migrations.CreateModel(
            name="TenantSubscription",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("plan_id", models.CharField(default="starter", max_length=50)),
                (
                    "status",
                    models.CharField(
                        choices=[("TRIAL", "Trial"), ("ACTIVE", "Active"), ("EXPIRED", "Expired"), ("CANCELLED", "Cancelled")],
                        default="TRIAL",
                        max_length=20,
                    ),
                ),
                (
                    "billing_cycle",
                    models.CharField(
                        choices=[("monthly", "Monthly"), ("annual", "Annual")],
                        default="monthly",
                        max_length=20,
                    ),
                ),
                ("trial_end_date", models.DateField(blank=True, null=True)),
                ("subscription_start_date", models.DateField(blank=True, null=True)),
                ("subscription_end_date", models.DateField(blank=True, null=True)),
                ("cancelled_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "tenant",
                    models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="subscription", to="api.tenant"),
                ),
            ],
        ),
        migrations.CreateModel(
            name="SubscriptionEvent",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                (
                    "action",
                    models.CharField(
                        choices=[
                            ("UPGRADE", "Upgrade"),
                            ("DOWNGRADE", "Downgrade"),
                            ("CANCEL", "Cancel"),
                            ("RENEW_TRIAL", "Renew Trial"),
                            ("PAYMENT", "Payment"),
                        ],
                        max_length=30,
                    ),
                ),
                ("from_plan_id", models.CharField(blank=True, max_length=50, null=True)),
                ("to_plan_id", models.CharField(blank=True, max_length=50, null=True)),
                ("payment_method", models.CharField(blank=True, max_length=50, null=True)),
                ("promo_code", models.CharField(blank=True, max_length=100, null=True)),
                ("amount", models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "created_by",
                    models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to="api.user"),
                ),
                (
                    "tenant",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="subscription_events", to="api.tenant"),
                ),
            ],
        ),
    ]
