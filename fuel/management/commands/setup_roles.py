from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "MVP kullanıcı gruplarını ve yakıt uygulaması izinlerini oluşturur."

    def handle(self, *args, **options):
        view_codenames = {
            "view_region",
            "view_supplier",
            "view_storageunit",
            "view_vehicle",
            "view_fuelmovement",
        }
        fuel_permissions = Permission.objects.filter(content_type__app_label="fuel")

        report_group, _ = Group.objects.get_or_create(name="Rapor Görüntüleme")
        report_group.permissions.set(
            fuel_permissions.filter(codename__in=view_codenames)
        )

        entry_group, _ = Group.objects.get_or_create(name="Veri Girişi")
        entry_group.permissions.set(
            fuel_permissions.filter(
                codename__in=view_codenames | {"add_fuelmovement"}
            )
        )

        operator_group, _ = Group.objects.get_or_create(name="Saha Operatörü")
        operator_group.permissions.set(
            fuel_permissions.filter(
                codename__in={"view_fuelingrequest", "add_fuelingrequest"}
            )
        )

        manager_group, _ = Group.objects.get_or_create(name="Yönetici")
        # En az yetki ilkesi: UI hiçbir yerde silme yapmadığı için delete_*
        # izinleri verilmez; yeni eklenen izinler de otomatik devralınmaz.
        manager_group.permissions.set(
            fuel_permissions.exclude(codename__startswith="delete_")
        )

        self.stdout.write(
            self.style.SUCCESS(
                "Rapor Görüntüleme, Veri Girişi, Saha Operatörü ve Yönetici grupları hazır."
            )
        )
