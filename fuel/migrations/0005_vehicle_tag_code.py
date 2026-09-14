from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("fuel", "0004_vehicle_region_history"),
    ]

    operations = [
        migrations.AddField(
            model_name="vehicle",
            name="tag_code",
            field=models.CharField(
                blank=True,
                max_length=20,
                null=True,
                unique=True,
                verbose_name="Araç anahtarı / RFID kodu",
            ),
        ),
    ]
