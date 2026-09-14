from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from fuel.models import (
    ContractorFuelPrice,
    FuelMovement,
    FuelingRequest,
    OperatorStorageAccess,
    Region,
    StorageUnit,
    Vehicle,
)
from fuel.services import (
    approve_fueling_request,
    create_movement,
    stock_for,
    submit_fueling_request,
)


class FuelingRequestTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.manager = user_model.objects.create_superuser(
            username="manager",
            password="test-password",
            email="manager@example.test",
        )
        self.operator = user_model.objects.create_user(
            username="operator",
            password="test-password",
        )
        self.other_operator = user_model.objects.create_user(
            username="operator-2",
            password="test-password",
        )
        self.operator.user_permissions.add(
            Permission.objects.get(codename="add_fuelingrequest")
        )
        self.manager.user_permissions.add(
            Permission.objects.get(codename="approve_fuelingrequest")
        )
        self.region = Region.objects.create(name="1. Bölge", code="B1")
        self.other_region = Region.objects.create(name="2. Bölge", code="B2")
        self.storage = StorageUnit.objects.create(
            name="Ana Tank",
            code="TANK-1",
            kind=StorageUnit.Kind.FIXED_TANK,
            region=self.region,
            capacity_liters=Decimal("1000"),
        )
        self.other_storage = StorageUnit.objects.create(
            name="Mobil Tanker",
            code="MOBIL-2",
            kind=StorageUnit.Kind.MOBILE_TANKER,
            region=self.other_region,
            capacity_liters=Decimal("500"),
        )
        self.vehicle = Vehicle.objects.create(
            name="Başka Bölge Aracı",
            code="06TEST01",
            region=self.other_region,
            meter_type=Vehicle.MeterType.KM,
        )
        OperatorStorageAccess.objects.create(
            user=self.operator,
            storage=self.storage,
        )
        create_movement(
            user=self.manager,
            movement_type=FuelMovement.MovementType.OPENING_BALANCE,
            data={
                "occurred_at": timezone.now(),
                "destination_storage": self.storage,
                "liters": Decimal("200"),
                "note": "Test stoğu",
            },
        )

    def request_data(self, **overrides):
        data = {
            "occurred_at": timezone.now(),
            "source_storage": self.storage,
            "vehicle": self.vehicle,
            "liters": Decimal("50"),
            "meter_value": Decimal("1000"),
            "note": "Saha dolumu",
        }
        data.update(overrides)
        return data

    def test_operator_can_submit_for_vehicle_in_another_region_without_stock_effect(self):
        before = stock_for(self.storage)

        request_record = submit_fueling_request(
            user=self.operator,
            data=self.request_data(),
        )

        self.assertEqual(request_record.status, FuelingRequest.Status.PENDING)
        self.assertEqual(request_record.source_region, self.region)
        self.assertEqual(request_record.vehicle_region, self.other_region)
        self.assertEqual(stock_for(self.storage), before)
        self.assertEqual(
            FuelMovement.objects.filter(
                movement_type=FuelMovement.MovementType.VEHICLE_FUELING
            ).count(),
            0,
        )

    def test_contractor_vehicle_rejects_meter_value(self):
        self.vehicle.is_contractor = True
        self.vehicle.save(update_fields=["is_contractor"])
        ContractorFuelPrice.objects.create(
            effective_from=timezone.now() - timedelta(days=1),
            reference_unit_price=Decimal("42.0000"),
            contractor_unit_price=Decimal("44.5000"),
            created_by=self.manager,
        )

        with self.assertRaisesMessage(
            ValidationError,
            "Taşeron araçlarda sayaç değeri girilmez",
        ):
            submit_fueling_request(
                user=self.operator,
                data=self.request_data(meter_value=Decimal("1000")),
            )

        request_record = submit_fueling_request(
            user=self.operator,
            data=self.request_data(meter_value=None),
        )
        self.assertIsNone(request_record.meter_value)
        self.assertEqual(
            request_record.contractor_reference_unit_price,
            Decimal("42.0000"),
        )
        self.assertEqual(request_record.contractor_unit_price, Decimal("44.5000"))

        approved = approve_fueling_request(
            request_id=request_record.pk,
            user=self.manager,
        )
        self.assertEqual(
            approved.movement.contractor_reference_unit_price,
            Decimal("42.0000"),
        )
        self.assertEqual(
            approved.movement.contractor_unit_price,
            Decimal("44.5000"),
        )

    def test_operator_cannot_submit_from_unassigned_storage(self):
        with self.assertRaisesMessage(Exception, "işlem yetkiniz bulunmuyor"):
            submit_fueling_request(
                user=self.operator,
                data=self.request_data(source_storage=self.other_storage),
            )

    def test_approval_creates_vehicle_fueling_and_deducts_stock(self):
        request_record = submit_fueling_request(
            user=self.operator,
            data=self.request_data(),
        )

        approved = approve_fueling_request(
            request_id=request_record.pk,
            user=self.manager,
        )

        self.assertEqual(approved.status, FuelingRequest.Status.APPROVED)
        self.assertEqual(approved.movement.created_by, self.operator)
        self.assertEqual(approved.decided_by, self.manager)
        self.assertEqual(stock_for(self.storage), Decimal("150"))

    def test_operator_dashboard_redirects_to_mobile_entry_screen(self):
        self.client.force_login(self.operator)

        response = self.client.get(reverse("dashboard"))

        self.assertRedirects(response, reverse("operator-fueling-request"))

    def test_operator_form_lists_only_assigned_storages_and_all_active_vehicles(self):
        self.client.force_login(self.operator)

        response = self.client.get(reverse("operator-fueling-request"))

        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertQuerySetEqual(
            form.fields["source_storage"].queryset,
            [self.storage],
        )
        self.assertIn(self.vehicle, form.fields["vehicle"].queryset)
        self.assertNotIn("contractor_reference_unit_price", form.fields)
        self.assertNotIn("contractor_unit_price", form.fields)
        self.assertNotContains(response, 'id="contractor-price-definitions"')

    def test_operator_form_hides_and_automatically_snapshots_contractor_prices(self):
        self.vehicle.is_contractor = True
        self.vehicle.save(update_fields=["is_contractor"])
        ContractorFuelPrice.objects.create(
            effective_from=timezone.now() - timedelta(days=1),
            reference_unit_price=Decimal("42.0000"),
            contractor_unit_price=Decimal("44.5000"),
            created_by=self.manager,
        )
        self.client.force_login(self.operator)

        response = self.client.post(
            reverse("operator-fueling-request"),
            {
                "occurred_at": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
                "source_storage": self.storage.pk,
                "vehicle": self.vehicle.pk,
                "liters": "25.000",
                "meter_value": "",
                "contractor_reference_unit_price": "1.0000",
                "contractor_unit_price": "2.0000",
                "note": "Taşeron saha dolumu",
            },
        )

        self.assertRedirects(response, reverse("operator-fueling-request"))
        request_record = FuelingRequest.objects.get(note="Taşeron saha dolumu")
        self.assertEqual(
            request_record.contractor_reference_unit_price,
            Decimal("42.0000"),
        )
        self.assertEqual(request_record.contractor_unit_price, Decimal("44.5000"))

        page = self.client.get(reverse("operator-fueling-request"))
        self.assertNotContains(page, "42,5 TL/L")
        self.assertNotContains(page, 'id="contractor-price-definitions"')

    def test_bulk_approval_allows_negative_stock(self):
        first = submit_fueling_request(
            user=self.operator,
            data=self.request_data(liters=Decimal("150"), meter_value=Decimal("1000")),
        )
        second = submit_fueling_request(
            user=self.operator,
            data=self.request_data(liters=Decimal("100"), meter_value=Decimal("1001")),
        )
        self.client.force_login(self.manager)

        response = self.client.post(
            reverse("fueling-request-approval-list"),
            {"request_ids": [first.pk, second.pk], "confirm": "1"},
        )

        self.assertRedirects(response, reverse("fueling-request-approval-list"))
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.status, FuelingRequest.Status.APPROVED)
        self.assertEqual(second.status, FuelingRequest.Status.APPROVED)
        self.assertEqual(stock_for(self.storage), Decimal("-50"))

    def test_bulk_approval_requires_confirmation_step(self):
        pending = submit_fueling_request(
            user=self.operator,
            data=self.request_data(liters=Decimal("20"), meter_value=Decimal("900")),
        )
        self.client.force_login(self.manager)

        response = self.client.post(
            reverse("fueling-request-approval-list"),
            {"request_ids": [pending.pk]},
        )

        # Onay adımı olmadan kayıt stoğa işlenmez; özet sayfası gösterilir.
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ONAY ÖZETİ")
        pending.refresh_from_db()
        self.assertEqual(pending.status, FuelingRequest.Status.PENDING)
        self.assertEqual(stock_for(self.storage), Decimal("200"))

    def test_approval_list_filters_by_operator_and_source_region(self):
        expected = submit_fueling_request(
            user=self.operator,
            data=self.request_data(),
        )
        OperatorStorageAccess.objects.create(
            user=self.other_operator,
            storage=self.other_storage,
        )
        submit_fueling_request(
            user=self.other_operator,
            data=self.request_data(
                source_storage=self.other_storage,
                liters=Decimal("10"),
            ),
        )
        self.client.force_login(self.manager)

        response = self.client.get(
            reverse("fueling-request-approval-list"),
            {"operator": self.operator.pk, "region": self.region.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertQuerySetEqual(response.context["page_obj"], [expected])


class OperatorUserFormTests(TestCase):
    def test_superuser_can_assign_operator_role_and_storages(self):
        manager = get_user_model().objects.create_superuser(
            username="admin",
            password="test-password",
            email="admin@example.test",
        )
        operator_group = Group.objects.create(name="Saha Operatörü")
        region = Region.objects.create(name="Saha", code="SAHA")
        storage = StorageUnit.objects.create(
            name="Saha Tankeri",
            code="ST-1",
            kind=StorageUnit.Kind.MOBILE_TANKER,
            region=region,
        )
        self.client.force_login(manager)

        response = self.client.post(
            reverse("user-create"),
            {
                "username": "saha-operatoru",
                "groups": [operator_group.pk],
                "operator_storages": [storage.pk],
                "is_active": "on",
                "password1": "GuvenliTest2026!",
                "password2": "GuvenliTest2026!",
            },
        )

        self.assertRedirects(response, reverse("user-list"))
        operator = get_user_model().objects.get(username="saha-operatoru")
        self.assertTrue(
            OperatorStorageAccess.objects.filter(
                user=operator,
                storage=storage,
            ).exists()
        )
