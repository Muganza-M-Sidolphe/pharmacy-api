from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0018_stockbatch_source_request"),
    ]

    operations = [
        migrations.AddField(
            model_name="retailwholesalerequest",
            name="retail_procurement_expense",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="retail_wholesale_source_requests",
                to="api.expense",
            ),
        ),
        migrations.AddField(
            model_name="retailwholesalerequest",
            name="wholesale_sale",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="retail_wholesale_source_requests",
                to="api.sale",
            ),
        ),
    ]
