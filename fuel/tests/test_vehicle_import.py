from io import BytesIO
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from openpyxl import Workbook

from fuel.forms import VehicleForm, VehicleFuelingForm
from fuel.models import FuelMovement, Region, StorageUnit, Vehicle
from fuel.vehicle_import import parse_description


class VehicleImportTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="admin",
            password="test-password",
            email="admin@example.test",
        )

    def _workbook_upload(self, rows, headers=None):
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = "Günlük Dolum"
        worksheet.append(
            headers
            or [
                "Plaka",
                "Araç Anahtarı",
                "Açıklama",
                "Makina Kodu",
                "saat / km / taseron",
            ]
        )
        for row in rows:
            row = list(row)
            if len(row) < 5:
                row.append("taseron")
            worksheet.append(row)
        stream = BytesIO()
        workbook.save(stream)
        workbook.close()
        return SimpleUploadedFile(
            "tag-list.xlsx",
            stream.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    def _preview(self):
        return self.client.post(
            reverse("vehicle-import"),
            {
                "action": "preview",
                "workbook": self._workbook_upload(
                    [
                        ["06 ABC 123", 4202521459, "YHT 3. BÖLGE", "FORD TRANSİT"],
                        [None, 29389483, "2. BÖLGE ACME İNŞAAT", "MAK-01"],
                        ["ERG-1", 3472936563, "ERG DEMİRYOLU", None],
                    ]
                ),
            },
        )

    def test_description_parser_separates_region_and_organization(self):
        numbered = parse_description("YHT 3. BÖLGE KİRALIK")
        self.assertEqual(numbered["region_code"], "3.BOLGE")
        self.assertEqual(numbered["organization"], "YHT · KİRALIK")
        self.assertFalse(numbered["region_was_inferred"])

        contractor = parse_description("2. BÖLGE ACME İNŞAAT")
        self.assertEqual(contractor["region_code"], "2.BOLGE")
        self.assertEqual(contractor["organization"], "ACME İNŞAAT")

        unknown = parse_description("ERG DEMİRYOLU")
        self.assertEqual(unknown["region_code"], "BELIRSIZ")
        self.assertTrue(unknown["region_was_inferred"])

    def test_preview_separates_meter_type_and_contractor_classification(self):
        self.client.force_login(self.user)
        preview = self.client.post(
            reverse("vehicle-import"),
            {
                "action": "preview",
                "workbook": self._workbook_upload(
                    [
                        ["06 KM 001", 11111111, "1. BÖLGE", "MAK-KM", "km"],
                        ["06 SAAT 002", 22222222, "2. BÖLGE", "MAK-SAAT", "saat"],
                        ["06 TAS 003", 33333333, "3. BÖLGE", "MAK-TAS", "taşeron"],
                    ]
                ),
            },
        )

        self.assertEqual(preview.status_code, 200)
        rows = preview.context["preview"]["sample_rows"]
        self.assertEqual(
            [row["meter_type"] for row in rows],
            [Vehicle.MeterType.KM, Vehicle.MeterType.HOUR, Vehicle.MeterType.NONE],
        )
        self.assertEqual(
            [row["meter_type_label"] for row in rows],
            ["Kilometre", "Çalışma saati", "Sayaç yok"],
        )
        self.assertEqual(
            [row["is_contractor"] for row in rows],
            [False, False, True],
        )
        self.assertEqual(
            [row["vehicle_class_label"] for row in rows],
            ["Kurum aracı", "Kurum aracı", "Taşeron"],
        )

        response = self.client.post(
            reverse("vehicle-import"),
            {
                "action": "import",
                "preview_token": preview.context["preview_token"],
            },
        )

        self.assertRedirects(response, reverse("vehicle-list"))
        self.assertEqual(
            list(
                Vehicle.objects.order_by("code").values_list("meter_type", flat=True)
            ),
            [Vehicle.MeterType.KM, Vehicle.MeterType.HOUR, Vehicle.MeterType.NONE],
        )
        self.assertEqual(
            list(
                Vehicle.objects.order_by("code").values_list(
                    "is_contractor", flat=True
                )
            ),
            [False, False, True],
        )

    def test_existing_vehicle_meter_type_is_updated_from_excel(self):
        region = Region.objects.create(name="1. Bölge", code="1.BOLGE")
        Vehicle.objects.create(
            name="FORD TRANSİT",
            code="06 ABC 123",
            tag_code="4202521459",
            region=region,
            meter_type=Vehicle.MeterType.NONE,
        )
        self.client.force_login(self.user)

        preview = self.client.post(
            reverse("vehicle-import"),
            {
                "action": "preview",
                "workbook": self._workbook_upload(
                    [["06 ABC 123", 4202521459, "1. BÖLGE", "FORD TRANSİT", "km"]]
                ),
            },
        )

        self.assertEqual(preview.context["preview"]["create_count"], 0)
        self.assertEqual(preview.context["preview"]["update_count"], 1)
        self.assertEqual(preview.context["preview"]["skip_count"], 0)
        self.assertEqual(preview.context["preview"]["import_count"], 1)

        response = self.client.post(
            reverse("vehicle-import"),
            {
                "action": "import",
                "preview_token": preview.context["preview_token"],
            },
        )

        self.assertRedirects(response, reverse("vehicle-list"))
        existing = Vehicle.objects.get(code="06 ABC 123")
        self.assertEqual(existing.meter_type, Vehicle.MeterType.KM)

    def test_existing_vehicle_with_history_keeps_meter_and_updates_contractor_class(self):
        region = Region.objects.create(name="1. Bölge", code="1.BOLGE")
        storage = StorageUnit.objects.create(
            name="Test sabit tank",
            code="TEST-TANK",
            kind=StorageUnit.Kind.FIXED_TANK,
            region=region,
            capacity_liters=Decimal("1000"),
        )
        vehicle = Vehicle.objects.create(
            name="FORD TRANSİT",
            code="06 ABC 123",
            tag_code="4202521459",
            region=region,
            meter_type=Vehicle.MeterType.KM,
        )
        FuelMovement.objects.create(
            movement_type=FuelMovement.MovementType.VEHICLE_FUELING,
            occurred_at=timezone.now(),
            source_storage=storage,
            source_region=region,
            vehicle=vehicle,
            vehicle_region=region,
            liters=Decimal("10"),
            meter_value=Decimal("100"),
            created_by=self.user,
        )
        self.client.force_login(self.user)

        preview = self.client.post(
            reverse("vehicle-import"),
            {
                "action": "preview",
                "workbook": self._workbook_upload(
                    [["06 ABC 123", 4202521459, "1. BÖLGE", "FORD TRANSİT", "taseron"]]
                ),
            },
        )

        self.assertEqual(preview.context["preview"]["conflicts"], [])
        self.assertEqual(preview.context["preview"]["protected_count"], 1)
        self.assertEqual(preview.context["preview"]["update_count"], 1)
        self.assertTrue(preview.context["preview"]["warnings"])
        self.assertTrue(preview.context["preview_token"])

        response = self.client.post(
            reverse("vehicle-import"),
            {
                "action": "import",
                "preview_token": preview.context["preview_token"],
            },
        )

        self.assertRedirects(response, reverse("vehicle-list"))
        vehicle.refresh_from_db()
        self.assertEqual(vehicle.meter_type, Vehicle.MeterType.KM)
        self.assertTrue(vehicle.is_contractor)

    def test_preview_does_not_write_and_confirmation_imports_rows(self):
        self.client.force_login(self.user)

        preview = self._preview()

        self.assertEqual(preview.status_code, 200)
        self.assertEqual(Vehicle.objects.count(), 0)
        self.assertEqual(Region.objects.count(), 0)
        self.assertEqual(preview.context["preview"]["create_count"], 3)
        self.assertEqual(preview.context["preview"]["unassigned_region_count"], 1)
        self.assertTrue(preview.context["preview_token"])

        response = self.client.post(
            reverse("vehicle-import"),
            {
                "action": "import",
                "preview_token": preview.context["preview_token"],
            },
        )

        self.assertRedirects(response, reverse("vehicle-list"))
        self.assertEqual(Vehicle.objects.count(), 3)
        self.assertEqual(Region.objects.count(), 3)
        tagged = Vehicle.objects.get(code="06 ABC 123")
        self.assertEqual(tagged.tag_code, "4202521459")
        self.assertEqual(tagged.organization, "YHT")
        self.assertEqual(tagged.region.code, "3.BOLGE")
        no_plate = Vehicle.objects.get(code="MAK-01")
        self.assertEqual(no_plate.name, "MAK-01")
        no_machine_code = Vehicle.objects.get(code="ERG-1")
        self.assertEqual(no_machine_code.name, "ERG-1")
        self.assertEqual(no_machine_code.region.code, "BELIRSIZ")

    def test_reimport_of_identical_file_is_idempotent(self):
        self.client.force_login(self.user)
        first_preview = self._preview()
        self.client.post(
            reverse("vehicle-import"),
            {
                "action": "import",
                "preview_token": first_preview.context["preview_token"],
            },
        )

        second_preview = self._preview()

        self.assertEqual(second_preview.context["preview"]["create_count"], 0)
        self.assertEqual(second_preview.context["preview"]["skip_count"], 3)
        response = self.client.post(
            reverse("vehicle-import"),
            {
                "action": "import",
                "preview_token": second_preview.context["preview_token"],
            },
        )
        self.assertRedirects(response, reverse("vehicle-list"))
        self.assertEqual(Vehicle.objects.count(), 3)

    def test_invalid_headers_do_not_create_preview_or_records(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("vehicle-import"),
            {
                "action": "preview",
                "workbook": self._workbook_upload(
                    [["06ABC123", 12345678, "1. BÖLGE", "FORD"]],
                    headers=["Araç", "Etiket", "Bölge", "Model"],
                ),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Excel başlıkları şu sırada olmalıdır")
        self.assertIsNone(response.context["preview"])
        self.assertEqual(Vehicle.objects.count(), 0)

    def test_vehicle_list_searches_rfid_code(self):
        region = Region.objects.create(name="1. Bölge", code="1.BOLGE")
        vehicle = Vehicle.objects.create(
            name="Test aracı",
            code="06TEST01",
            tag_code="4202521459",
            region=region,
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse("vehicle-list"), {"q": "4202521459"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, vehicle.code)
        self.assertContains(response, vehicle.tag_code)
        self.assertContains(response, "Plaka, RFID veya açıklama ara")
        self.assertContains(response, "Açıklama")

        fueling_form = VehicleFuelingForm()
        label = fueling_form.fields["vehicle"].label_from_instance(vehicle)
        self.assertEqual(
            label,
            f"{vehicle.code} · {vehicle.name} · {region.name} · Sayaç yok",
        )
        self.assertNotIn("RFID", label)
        rendered_options = str(fueling_form["vehicle"])
        self.assertIn("RFID 4202521459", rendered_options)

        vehicle_form = VehicleForm()
        self.assertEqual(vehicle_form.fields["organization"].label, "Açıklama")

    def test_vehicle_list_filters_region_meter_type_class_and_status(self):
        first_region = Region.objects.create(name="1. Bölge", code="1.BOLGE")
        second_region = Region.objects.create(name="2. Bölge", code="2.BOLGE")
        matching = Vehicle.objects.create(
            name="Ekskavatör",
            code="MAK-002",
            region=second_region,
            meter_type=Vehicle.MeterType.HOUR,
            is_contractor=True,
            organization="Operatör Ahmet Usta",
            is_active=False,
        )
        Vehicle.objects.create(
            name="Kamyon",
            code="06TEST02",
            region=first_region,
            meter_type=Vehicle.MeterType.KM,
        )
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("vehicle-list"),
            {
                "region": second_region.pk,
                "meter_type": Vehicle.MeterType.HOUR,
                "vehicle_class": "contractor",
                "status": "passive",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["items"]), [matching])
        self.assertEqual(response.context["region_filter"], str(second_region.pk))
        self.assertEqual(response.context["meter_type_filter"], Vehicle.MeterType.HOUR)
        self.assertEqual(response.context["vehicle_class_filter"], "contractor")
        self.assertEqual(response.context["status_filter"], "passive")

        description_response = self.client.get(
            reverse("vehicle-list"), {"q": "Ahmet Usta"}
        )
        self.assertContains(description_response, matching.code)

    def test_vehicle_list_paginates_by_50_100_or_500(self):
        region = Region.objects.create(name="1. Bölge", code="1.BOLGE")
        Vehicle.objects.bulk_create(
            [
                Vehicle(name=f"Araç {index}", code=f"TEST-{index:03}", region=region)
                for index in range(55)
            ]
        )
        self.client.force_login(self.user)

        default_response = self.client.get(reverse("vehicle-list"))
        self.assertEqual(default_response.context["page_size"], 50)
        self.assertEqual(len(default_response.context["items"]), 50)
        self.assertEqual(default_response.context["page_obj"].paginator.num_pages, 2)
        self.assertEqual(default_response.context["page_size_options"], (50, 100, 500))

        expanded_response = self.client.get(
            reverse("vehicle-list"), {"page_size": "100"}
        )
        self.assertEqual(expanded_response.context["page_size"], 100)
        self.assertEqual(len(expanded_response.context["items"]), 55)
