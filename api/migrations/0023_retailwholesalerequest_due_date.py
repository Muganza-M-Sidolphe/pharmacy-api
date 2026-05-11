from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0022_retailwholesalerequest_payment_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="retailwholesalerequest",
            name="due_date",
            field=models.DateField(blank=True, null=True),
        ),
    ]
