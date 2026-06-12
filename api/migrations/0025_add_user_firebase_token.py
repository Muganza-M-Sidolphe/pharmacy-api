from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0024_retailwholesalerequest_declared_paid_amount"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="firebase_token",
            field=models.CharField(max_length=1024, null=True, blank=True),
        ),
    ]
