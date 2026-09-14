from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("fuel", "0006_fuelingrequest_operatorstorageaccess"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="fuelmovement",
            constraint=models.CheckConstraint(
                condition=~models.Q(
                    source_storage=models.F("destination_storage")
                ),
                name="movement_source_differs_from_destination",
            ),
        ),
    ]
