from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0010_partialpaymentreminderconfig_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="sale",
            name="due_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="sale",
            name="owner_approval_status",
            field=models.CharField(
                choices=[("PENDING", "Pending"), ("APPROVED", "Approved"), ("REJECTED", "Rejected")],
                default="PENDING",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="sale",
            name="owner_approved_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="sale",
            name="owner_approved_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="owner_approved_sales",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="sale",
            name="pharmacist_approval_status",
            field=models.CharField(
                choices=[("PENDING", "Pending"), ("APPROVED", "Approved"), ("REJECTED", "Rejected")],
                default="PENDING",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="sale",
            name="pharmacist_approved_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="sale",
            name="pharmacist_approved_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="pharmacist_approved_sales",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
