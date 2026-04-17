from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0021_passwordresettoken"),
    ]

    operations = [
        migrations.AddField(
            model_name="retailwholesalerequest",
            name="paid_amount",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
        migrations.AddField(
            model_name="retailwholesalerequest",
            name="payment_method",
            field=models.CharField(
                choices=[
                    ("CASH", "Cash"),
                    ("CARD", "Card"),
                    ("UPI", "UPI"),
                    ("MOBILE_MONEY", "Mobile Money"),
                    ("BANK_TRANSFER", "Bank Transfer"),
                ],
                default="BANK_TRANSFER",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="retailwholesalerequest",
            name="payment_option",
            field=models.CharField(
                choices=[
                    ("FULL", "Full Payment"),
                    ("PARTIAL", "Partial Payment"),
                    ("CREDIT", "Credit"),
                ],
                default="FULL",
                max_length=20,
            ),
        ),
    ]
