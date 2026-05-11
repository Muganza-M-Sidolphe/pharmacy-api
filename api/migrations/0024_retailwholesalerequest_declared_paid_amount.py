from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0023_retailwholesalerequest_due_date"),
    ]

    operations = [
        migrations.AddField(
            model_name="retailwholesalerequest",
            name="declared_paid_amount",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
    ]
