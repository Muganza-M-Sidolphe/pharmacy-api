from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0019_retailwholesalerequest_sale_and_expense_links"),
    ]

    operations = [
        migrations.CreateModel(
            name="SupportTicket",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("title", models.CharField(max_length=255)),
                ("description", models.TextField()),
                ("type", models.CharField(choices=[("bug", "Bug"), ("feature", "Feature")], max_length=20)),
                ("priority", models.CharField(choices=[("low", "Low"), ("medium", "Medium"), ("high", "High")], max_length=20)),
                ("status", models.CharField(choices=[("open", "Open"), ("in_progress", "In Progress"), ("resolved", "Resolved")], default="open", max_length=20)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("created_by", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="support_tickets", to="api.user")),
            ],
            options={
                "ordering": ("-created_at",),
            },
        ),
    ]
