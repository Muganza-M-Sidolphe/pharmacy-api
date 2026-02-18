import uuid

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0008_tenantsubscription_subscriptionevent"),
    ]

    operations = [
        migrations.CreateModel(
            name="SubscriptionPlan",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("code", models.CharField(max_length=50, unique=True)),
                ("name", models.CharField(max_length=120)),
                ("description", models.TextField(blank=True, null=True)),
                ("price", models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                (
                    "business_type",
                    models.CharField(
                        choices=[("RETAIL", "Retail"), ("WHOLESALE", "Wholesale"), ("BOTH", "Both")],
                        default="BOTH",
                        max_length=20,
                    ),
                ),
                ("max_users", models.IntegerField(default=1)),
                ("max_branches", models.IntegerField(default=1)),
                ("features", models.JSONField(blank=True, default=dict)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.CreateModel(
            name="AuditLog",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                (
                    "action",
                    models.CharField(
                        choices=[("VIEW", "View"), ("CREATE", "Create"), ("UPDATE", "Update"), ("DELETE", "Delete")],
                        max_length=20,
                    ),
                ),
                ("entity", models.CharField(max_length=100)),
                (
                    "status",
                    models.CharField(
                        choices=[("SUCCESS", "Success"), ("ERROR", "Error"), ("FAILED", "Failed")],
                        default="SUCCESS",
                        max_length=20,
                    ),
                ),
                ("details", models.JSONField(blank=True, default=dict)),
                ("ip_address", models.CharField(blank=True, max_length=64, null=True)),
                ("status_code", models.IntegerField(blank=True, null=True)),
                ("error_message", models.TextField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "tenant",
                    models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to="api.tenant"),
                ),
                (
                    "user",
                    models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to="api.user"),
                ),
            ],
        ),
        migrations.CreateModel(
            name="SystemSetting",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("key", models.CharField(max_length=100, unique=True)),
                ("data", models.JSONField(blank=True, default=dict)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "updated_by",
                    models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to="api.user"),
                ),
            ],
        ),
    ]
