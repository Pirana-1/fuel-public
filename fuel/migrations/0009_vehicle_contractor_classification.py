from django.db import migrations, models


def move_contractor_meter_type(apps, schema_editor):
    Vehicle = apps.get_model("fuel", "Vehicle")
    Vehicle.objects.filter(meter_type="TASERON").update(
        is_contractor=True,
        meter_type="NONE",
    )


def restore_contractor_meter_type(apps, schema_editor):
    Vehicle = apps.get_model("fuel", "Vehicle")
    Vehicle.objects.filter(is_contractor=True).update(meter_type="TASERON")


class Migration(migrations.Migration):
    dependencies = [
        ("fuel", "0008_alter_vehicle_meter_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="vehicle",
            name="is_contractor",
            field=models.BooleanField(default=False, verbose_name="Taşeron araç/makine"),
        ),
        migrations.RunPython(
            move_contractor_meter_type,
            restore_contractor_meter_type,
        ),
        migrations.AlterField(
            model_name="vehicle",
            name="meter_type",
            field=models.CharField(
                choices=[
                    ("KM", "Kilometre"),
                    ("HOUR", "Çalışma saati"),
                    ("NONE", "Sayaç yok"),
                ],
                default="NONE",
                max_length=10,
                verbose_name="Sayaç türü",
            ),
        ),
    ]
