from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from fuel.models import (
    ContractorFuelPrice,
    FuelMovement,
    Region,
    StorageRegionChange,
    StorageUnit,
    Supplier,
    Vehicle,
    VehicleRegionChange,
)
from fuel.services import (
    change_storage_region,
    change_vehicle_region,
    contractor_pricing_totals,
    create_movement,
    create_stock_adjustment_from_count,
    negative_stock_warning_for,
    period_totals,
    stock_by_storage,
    stock_for,
    storage_region_at,
    vehicle_region_at,
    void_movement,
)
from fuel.correction_service import correct_movement


class FuelMovementServiceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="operator", password="test-password"
        )
        self.region = Region.objects.create(name="1. Bölge", code="B1")
        self.supplier = Supplier.objects.create(name="Test Tedarikçi")
        self.fixed = StorageUnit.objects.create(
            name="Polatlı Sabit Tank",
            code="POL-01",
            kind=StorageUnit.Kind.FIXED_TANK,
            region=self.region,
            capacity_liters=Decimal("50000"),
        )
        self.mobile = StorageUnit.objects.create(
            name="06 PT 962",
            code="06PT962",
            kind=StorageUnit.Kind.MOBILE_TANKER,
            region=self.region,
            capacity_liters=Decimal("10000"),
        )
        self.vehicle = Vehicle.objects.create(
            name="Saha kamyonu",
            code="06ABC123",
            region=self.region,
            meter_type=Vehicle.MeterType.KM,
        )
        self.now = timezone.now()

    def create_opening(self, storage, liters):
        return create_movement(
            user=self.user,
            movement_type=FuelMovement.MovementType.OPENING_BALANCE,
            data={
                "occurred_at": self.now,
                "destination_storage": storage,
                "liters": Decimal(liters),
                "note": "Açılış",
            },
        )

    def test_supplier_receipt_increases_fixed_stock_and_received_total(self):
        movement = create_movement(
            user=self.user,
            movement_type=FuelMovement.MovementType.SUPPLIER_RECEIPT,
            data={
                "occurred_at": self.now,
                "supplier": self.supplier,
                "destination_storage": self.fixed,
                "liters": Decimal("20000"),
                "purchase_unit_price": Decimal("42.5000"),
                "delivery_note": "IRS-1",
            },
        )

        self.assertEqual(stock_for(self.fixed), Decimal("20000"))
        totals = period_totals(FuelMovement.objects.active())
        self.assertEqual(totals["received"], Decimal("20000"))
        self.assertEqual(totals["consumed"], Decimal("0"))
        self.assertEqual(movement.purchase_unit_price, Decimal("42.5000"))
        self.assertEqual(movement.purchase_total, Decimal("850000.0000000"))
        self.assertEqual(totals["purchase_amount"], Decimal("850000.0000000"))

    def test_supplier_receipt_requires_purchase_unit_price(self):
        with self.assertRaises(ValidationError) as error:
            create_movement(
                user=self.user,
                movement_type=FuelMovement.MovementType.SUPPLIER_RECEIPT,
                data={
                    "occurred_at": self.now,
                    "supplier": self.supplier,
                    "destination_storage": self.fixed,
                    "liters": Decimal("100"),
                },
            )

        self.assertIn("purchase_unit_price", error.exception.error_dict)

    def test_contractor_fueling_uses_effective_price_and_keeps_snapshot(self):
        self.vehicle.is_contractor = True
        self.vehicle.meter_type = Vehicle.MeterType.NONE
        self.vehicle.save(update_fields=["is_contractor", "meter_type"])
        ContractorFuelPrice.objects.create(
            effective_from=self.now - timedelta(days=2),
            reference_unit_price=Decimal("42.0000"),
            contractor_unit_price=Decimal("44.5000"),
            created_by=self.user,
        )

        movement = create_movement(
            user=self.user,
            movement_type=FuelMovement.MovementType.VEHICLE_FUELING,
            data={
                "occurred_at": self.now - timedelta(days=1),
                "source_storage": self.fixed,
                "vehicle": self.vehicle,
                "liters": Decimal("100"),
            },
        )
        ContractorFuelPrice.objects.create(
            effective_from=self.now,
            reference_unit_price=Decimal("43.0000"),
            contractor_unit_price=Decimal("46.0000"),
            created_by=self.user,
        )

        movement.refresh_from_db()
        self.assertEqual(
            movement.contractor_reference_unit_price,
            Decimal("42.0000"),
        )
        self.assertEqual(movement.contractor_unit_price, Decimal("44.5000"))
        self.assertEqual(movement.contractor_difference_total, Decimal("250.0000000"))
        totals = contractor_pricing_totals([movement])
        self.assertEqual(totals["reference_total"], Decimal("4200.0000000"))
        self.assertEqual(totals["charged_total"], Decimal("4450.0000000"))

    def test_transfer_moves_stock_without_changing_company_total(self):
        self.create_opening(self.fixed, "10000")

        create_movement(
            user=self.user,
            movement_type=FuelMovement.MovementType.INTERNAL_TRANSFER,
            data={
                "occurred_at": self.now,
                "source_storage": self.fixed,
                "destination_storage": self.mobile,
                "liters": Decimal("4000"),
                "note": "Saha dağıtımı",
            },
        )

        balances = stock_by_storage([self.fixed, self.mobile])
        self.assertEqual(balances[self.fixed.pk], Decimal("6000"))
        self.assertEqual(balances[self.mobile.pk], Decimal("4000"))
        self.assertEqual(sum(balances.values()), Decimal("10000"))
        totals = period_totals(FuelMovement.objects.active())
        self.assertEqual(totals["transferred"], Decimal("4000"))
        self.assertEqual(totals["consumed"], Decimal("0"))

    def test_storage_region_change_preserves_stock_and_movement_regions(self):
        old_movement_time = self.now - timedelta(hours=2)
        opening = create_movement(
            user=self.user,
            movement_type=FuelMovement.MovementType.OPENING_BALANCE,
            data={
                "occurred_at": old_movement_time,
                "destination_storage": self.mobile,
                "liters": Decimal("500"),
                "note": "Açılış",
            },
        )
        new_region = Region.objects.create(name="2. Bölge", code="B2")

        change = change_storage_region(
            user=self.user,
            storage=self.mobile,
            to_region=new_region,
            occurred_at=self.now - timedelta(hours=1),
            reason="Tanker yeni şantiyeye görevlendirildi",
        )

        self.mobile.refresh_from_db()
        opening.refresh_from_db()
        self.assertEqual(self.mobile.region, new_region)
        self.assertEqual(stock_for(self.mobile), Decimal("500"))
        self.assertEqual(change.from_region, self.region)
        self.assertEqual(change.to_region, new_region)
        self.assertEqual(change.created_by, self.user)
        self.assertEqual(opening.destination_region, self.region)
        self.assertEqual(storage_region_at(self.mobile, old_movement_time), self.region)

        fueling = create_movement(
            user=self.user,
            movement_type=FuelMovement.MovementType.VEHICLE_FUELING,
            data={
                "occurred_at": self.now,
                "source_storage": self.mobile,
                "vehicle": self.vehicle,
                "liters": Decimal("50"),
                "meter_value": Decimal("1000"),
            },
        )
        self.assertEqual(fueling.source_region, new_region)
        self.assertEqual(stock_for(self.mobile), Decimal("450"))

    def test_storage_region_changes_are_chronological_and_audited(self):
        second_region = Region.objects.create(name="2. Bölge", code="B2")
        third_region = Region.objects.create(name="3. Bölge", code="B3")
        changed_at = self.now - timedelta(hours=1)
        change_storage_region(
            user=self.user,
            storage=self.fixed,
            to_region=second_region,
            occurred_at=changed_at,
            reason="Bölge kapatıldı",
        )

        with self.assertRaises(ValidationError) as error:
            change_storage_region(
                user=self.user,
                storage=self.fixed,
                to_region=third_region,
                occurred_at=changed_at - timedelta(minutes=1),
                reason="Eski tarihli kayıt",
            )

        self.assertIn("occurred_at", error.exception.error_dict)
        self.assertEqual(StorageRegionChange.objects.count(), 1)
        self.fixed.refresh_from_db()
        self.assertEqual(self.fixed.region, second_region)

    def test_vehicle_region_change_preserves_meter_and_movement_regions(self):
        self.create_opening(self.fixed, "1000")
        old_movement_time = self.now - timedelta(hours=2)
        old_fueling = create_movement(
            user=self.user,
            movement_type=FuelMovement.MovementType.VEHICLE_FUELING,
            data={
                "occurred_at": old_movement_time,
                "source_storage": self.fixed,
                "vehicle": self.vehicle,
                "liters": Decimal("100"),
                "meter_value": Decimal("1000"),
            },
        )
        new_region = Region.objects.create(name="2. Bölge", code="B2")

        change = change_vehicle_region(
            user=self.user,
            vehicle=self.vehicle,
            to_region=new_region,
            occurred_at=self.now - timedelta(hours=1),
            reason="Araç yeni şantiyeye görevlendirildi",
        )

        self.vehicle.refresh_from_db()
        old_fueling.refresh_from_db()
        self.assertEqual(self.vehicle.region, new_region)
        self.assertEqual(change.from_region, self.region)
        self.assertEqual(change.to_region, new_region)
        self.assertEqual(old_fueling.vehicle_region, self.region)
        self.assertEqual(vehicle_region_at(self.vehicle, old_movement_time), self.region)

        new_fueling = create_movement(
            user=self.user,
            movement_type=FuelMovement.MovementType.VEHICLE_FUELING,
            data={
                "occurred_at": self.now,
                "source_storage": self.fixed,
                "vehicle": self.vehicle,
                "liters": Decimal("50"),
                "meter_value": Decimal("1100"),
            },
        )
        self.assertEqual(new_fueling.vehicle_region, new_region)
        self.assertEqual(new_fueling.meter_value, Decimal("1100"))
        self.assertEqual(VehicleRegionChange.objects.count(), 1)
        self.assertEqual(stock_for(self.fixed), Decimal("850"))

    def test_vehicle_fueling_from_fixed_tank_reduces_stock(self):
        self.create_opening(self.fixed, "5000")

        create_movement(
            user=self.user,
            movement_type=FuelMovement.MovementType.VEHICLE_FUELING,
            data={
                "occurred_at": self.now,
                "source_storage": self.fixed,
                "vehicle": self.vehicle,
                "liters": Decimal("250"),
                "meter_value": Decimal("124500"),
                "note": "Doğrudan dolum",
            },
        )

        self.assertEqual(stock_for(self.fixed), Decimal("4750"))
        totals = period_totals(FuelMovement.objects.active())
        self.assertEqual(totals["consumed"], Decimal("250"))

    def test_vehicle_fueling_from_mobile_tanker_reduces_mobile_stock(self):
        self.create_opening(self.mobile, "3000")

        create_movement(
            user=self.user,
            movement_type=FuelMovement.MovementType.VEHICLE_FUELING,
            data={
                "occurred_at": self.now,
                "source_storage": self.mobile,
                "vehicle": self.vehicle,
                "liters": Decimal("180"),
                "meter_value": Decimal("124920"),
            },
        )

        self.assertEqual(stock_for(self.mobile), Decimal("2820"))

    def test_negative_stock_is_saved_and_reported_as_warning(self):
        self.create_opening(self.fixed, "100")

        movement = create_movement(
            user=self.user,
            movement_type=FuelMovement.MovementType.VEHICLE_FUELING,
            data={
                "occurred_at": self.now,
                "source_storage": self.fixed,
                "vehicle": self.vehicle,
                "liters": Decimal("150"),
                "meter_value": Decimal("125000"),
            },
        )

        self.assertEqual(stock_for(self.fixed), Decimal("-50"))
        self.assertEqual(
            negative_stock_warning_for(movement),
            "Uyarı: Polatlı Sabit Tank stoğu eksiye düştü (-50.000 L). Kalibrasyon ve kayıtları kontrol edin.",
        )

    def test_contractor_vehicle_cannot_receive_meter_value(self):
        self.vehicle.is_contractor = True
        self.vehicle.save(update_fields=["is_contractor"])

        with self.assertRaisesMessage(
            ValidationError, "Taşeron araçlarda sayaç değeri girilmez"
        ):
            create_movement(
                user=self.user,
                movement_type=FuelMovement.MovementType.VEHICLE_FUELING,
                data={
                    "occurred_at": self.now,
                    "source_storage": self.fixed,
                    "vehicle": self.vehicle,
                    "liters": Decimal("100"),
                    "meter_value": Decimal("125000"),
                    "contractor_reference_unit_price": Decimal("42.0000"),
                    "contractor_unit_price": Decimal("44.5000"),
                },
            )

    def test_vehicle_with_meter_requires_meter_value(self):
        self.create_opening(self.fixed, "1000")

        with self.assertRaises(ValidationError) as error:
            create_movement(
                user=self.user,
                movement_type=FuelMovement.MovementType.VEHICLE_FUELING,
                data={
                    "occurred_at": self.now,
                    "source_storage": self.fixed,
                    "vehicle": self.vehicle,
                    "liters": Decimal("100"),
                },
            )

        self.assertIn("meter_value", error.exception.error_dict)

    def test_stock_adjustments_change_stock_without_changing_report_totals(self):
        self.create_opening(self.fixed, "1000")
        create_movement(
            user=self.user,
            movement_type=FuelMovement.MovementType.STOCK_ADJUSTMENT,
            data={
                "occurred_at": self.now,
                "destination_storage": self.fixed,
                "liters": Decimal("100"),
                "note": "Fiziksel sayım fazlası",
            },
        )
        create_movement(
            user=self.user,
            movement_type=FuelMovement.MovementType.STOCK_ADJUSTMENT,
            data={
                "occurred_at": self.now,
                "source_storage": self.fixed,
                "liters": Decimal("50"),
                "note": "Fiziksel sayım eksiği",
            },
        )

        self.assertEqual(stock_for(self.fixed), Decimal("1050"))
        totals = period_totals(FuelMovement.objects.active())
        self.assertEqual(totals["received"], Decimal("0"))
        self.assertEqual(totals["consumed"], Decimal("0"))
        self.assertEqual(totals["transferred"], Decimal("0"))

    def test_counted_stock_creates_only_the_calculated_adjustment(self):
        self.create_opening(self.fixed, "1000")

        decrease = create_stock_adjustment_from_count(
            user=self.user,
            occurred_at=self.now,
            storage=self.fixed,
            counted_stock=Decimal("850"),
            note="Fiziksel sayım",
        )

        self.assertEqual(decrease.source_storage, self.fixed)
        self.assertIsNone(decrease.destination_storage)
        self.assertEqual(decrease.liters, Decimal("150"))
        self.assertEqual(stock_for(self.fixed), Decimal("850"))
        self.assertIn("Sistem stoğu: 1000.000 L", decrease.note)
        self.assertIn("Sayılan stok: 850.000 L", decrease.note)

        increase = create_stock_adjustment_from_count(
            user=self.user,
            occurred_at=self.now,
            storage=self.fixed,
            counted_stock=Decimal("900"),
            note="İkinci fiziksel sayım",
        )

        self.assertIsNone(increase.source_storage)
        self.assertEqual(increase.destination_storage, self.fixed)
        self.assertEqual(increase.liters, Decimal("50"))
        self.assertEqual(stock_for(self.fixed), Decimal("900"))
        totals = period_totals(FuelMovement.objects.active())
        self.assertEqual(totals["received"], Decimal("0"))
        self.assertEqual(totals["consumed"], Decimal("0"))
        self.assertEqual(totals["transferred"], Decimal("0"))

    def test_counted_stock_rejects_a_noop_and_capacity_overflow(self):
        self.create_opening(self.fixed, "1000")

        with self.assertRaises(ValidationError) as no_change:
            create_stock_adjustment_from_count(
                user=self.user,
                occurred_at=self.now,
                storage=self.fixed,
                counted_stock=Decimal("1000"),
                note="Fiziksel sayım",
            )
        self.assertIn("counted_stock", no_change.exception.error_dict)

        with self.assertRaises(ValidationError) as over_capacity:
            create_stock_adjustment_from_count(
                user=self.user,
                occurred_at=self.now,
                storage=self.fixed,
                counted_stock=Decimal("50001"),
                note="Fiziksel sayım",
            )
        self.assertIn("counted_stock", over_capacity.exception.error_dict)

    def test_voiding_vehicle_fueling_restores_stock_and_removes_consumption(self):
        self.create_opening(self.fixed, "1000")
        fueling = create_movement(
            user=self.user,
            movement_type=FuelMovement.MovementType.VEHICLE_FUELING,
            data={
                "occurred_at": self.now,
                "source_storage": self.fixed,
                "vehicle": self.vehicle,
                "liters": Decimal("200"),
                "meter_value": Decimal("125000"),
            },
        )

        void_movement(
            movement_id=fueling.pk,
            user=self.user,
            reason="Yanlış araç seçildi",
        )

        fueling.refresh_from_db()
        self.assertTrue(fueling.is_voided)
        self.assertEqual(fueling.voided_by, self.user)
        self.assertEqual(fueling.void_reason, "Yanlış araç seçildi")
        self.assertEqual(stock_for(self.fixed), Decimal("1000"))
        totals = period_totals(FuelMovement.objects.active())
        self.assertEqual(totals["consumed"], Decimal("0"))

    def test_voiding_incoming_movement_can_make_stock_negative(self):
        receipt = create_movement(
            user=self.user,
            movement_type=FuelMovement.MovementType.SUPPLIER_RECEIPT,
            data={
                "occurred_at": self.now,
                "supplier": self.supplier,
                "destination_storage": self.fixed,
                "liters": Decimal("1000"),
                "purchase_unit_price": Decimal("42.0000"),
            },
        )
        create_movement(
            user=self.user,
            movement_type=FuelMovement.MovementType.VEHICLE_FUELING,
            data={
                "occurred_at": self.now,
                "source_storage": self.fixed,
                "vehicle": self.vehicle,
                "liters": Decimal("900"),
                "meter_value": Decimal("125100"),
            },
        )

        void_movement(
            movement_id=receipt.pk,
            user=self.user,
            reason="Yanlış giriş",
        )

        receipt.refresh_from_db()
        self.assertTrue(receipt.is_voided)
        self.assertEqual(stock_for(self.fixed), Decimal("-900"))

    def test_destination_capacity_is_enforced(self):
        self.fixed.capacity_liters = Decimal("500")
        self.fixed.save(update_fields=["capacity_liters"])

        with self.assertRaisesMessage(ValidationError, "Kapasite aşılıyor"):
            create_movement(
                user=self.user,
                movement_type=FuelMovement.MovementType.SUPPLIER_RECEIPT,
                data={
                    "occurred_at": self.now,
                    "supplier": self.supplier,
                    "destination_storage": self.fixed,
                    "liters": Decimal("600"),
                    "purchase_unit_price": Decimal("42.0000"),
                },
            )

        self.assertEqual(stock_for(self.fixed), Decimal("0"))

    def test_vehicle_meter_must_follow_chronological_order(self):
        self.create_opening(self.fixed, "1000")
        create_movement(
            user=self.user,
            movement_type=FuelMovement.MovementType.VEHICLE_FUELING,
            data={
                "occurred_at": self.now,
                "source_storage": self.fixed,
                "vehicle": self.vehicle,
                "liters": Decimal("100"),
                "meter_value": Decimal("1000"),
            },
        )

        with self.assertRaisesMessage(ValidationError, "sonraki dolumdaki"):
            create_movement(
                user=self.user,
                movement_type=FuelMovement.MovementType.VEHICLE_FUELING,
                data={
                    "occurred_at": self.now - timedelta(days=1),
                    "source_storage": self.fixed,
                    "vehicle": self.vehicle,
                    "liters": Decimal("100"),
                    "meter_value": Decimal("1100"),
                },
            )

    def test_correction_keeps_old_record_and_applies_replacement(self):
        self.create_opening(self.fixed, "1000")
        fueling = create_movement(
            user=self.user,
            movement_type=FuelMovement.MovementType.VEHICLE_FUELING,
            data={
                "occurred_at": self.now,
                "source_storage": self.fixed,
                "vehicle": self.vehicle,
                "liters": Decimal("200"),
                "meter_value": Decimal("1000"),
            },
        )

        replacement = correct_movement(
            movement=fueling,
            data={
                "occurred_at": self.now,
                "source_storage": self.fixed,
                "vehicle": self.vehicle,
                "liters": Decimal("150"),
                "meter_value": Decimal("1000"),
                "note": "Doğru miktar",
            },
            reason="Litre yanlış girildi",
            user=self.user,
        )

        fueling.refresh_from_db()
        self.assertTrue(fueling.is_voided)
        self.assertEqual(replacement.corrects, fueling)
        self.assertEqual(stock_for(self.fixed), Decimal("850"))
        self.assertEqual(
            period_totals(FuelMovement.objects.active())["consumed"],
            Decimal("150"),
        )

    def test_correction_can_leave_stock_negative(self):
        receipt = create_movement(
            user=self.user,
            movement_type=FuelMovement.MovementType.SUPPLIER_RECEIPT,
            data={
                "occurred_at": self.now,
                "supplier": self.supplier,
                "destination_storage": self.fixed,
                "liters": Decimal("1000"),
                "purchase_unit_price": Decimal("42.0000"),
            },
        )
        create_movement(
            user=self.user,
            movement_type=FuelMovement.MovementType.VEHICLE_FUELING,
            data={
                "occurred_at": self.now,
                "source_storage": self.fixed,
                "vehicle": self.vehicle,
                "liters": Decimal("900"),
                "meter_value": Decimal("1200"),
            },
        )

        replacement = correct_movement(
            movement=receipt,
            data={
                "occurred_at": self.now,
                "supplier": self.supplier,
                "destination_storage": self.fixed,
                "liters": Decimal("800"),
                "purchase_unit_price": Decimal("42.0000"),
                "delivery_note": "DÜZELTME",
            },
            reason="Miktar yanlış",
            user=self.user,
        )

        receipt.refresh_from_db()
        self.assertTrue(receipt.is_voided)
        self.assertEqual(replacement.corrects, receipt)
        self.assertEqual(stock_for(self.fixed), Decimal("-100"))

    def test_future_dated_movement_is_rejected(self):
        with self.assertRaisesMessage(ValidationError, "ileri tarihli olamaz"):
            create_movement(
                user=self.user,
                movement_type=FuelMovement.MovementType.SUPPLIER_RECEIPT,
                data={
                    "occurred_at": self.now + timedelta(minutes=1),
                    "supplier": self.supplier,
                    "destination_storage": self.fixed,
                    "liters": Decimal("100"),
                    "purchase_unit_price": Decimal("42.0000"),
                },
            )

    def test_future_dated_correction_is_rejected(self):
        receipt = create_movement(
            user=self.user,
            movement_type=FuelMovement.MovementType.SUPPLIER_RECEIPT,
            data={
                "occurred_at": self.now,
                "supplier": self.supplier,
                "destination_storage": self.fixed,
                "liters": Decimal("500"),
                "purchase_unit_price": Decimal("42.0000"),
            },
        )

        with self.assertRaisesMessage(ValidationError, "ileri tarihli olamaz"):
            correct_movement(
                movement=receipt,
                data={
                    "occurred_at": self.now + timedelta(minutes=1),
                    "supplier": self.supplier,
                    "destination_storage": self.fixed,
                    "liters": Decimal("400"),
                    "purchase_unit_price": Decimal("42.0000"),
                },
                reason="Tarih düzeltmesi",
                user=self.user,
            )
        receipt.refresh_from_db()
        self.assertFalse(receipt.is_voided)

    def test_stock_adjustment_cannot_be_corrected(self):
        self.create_opening(self.fixed, "250")
        adjustment = create_stock_adjustment_from_count(
            user=self.user,
            occurred_at=self.now,
            storage=self.fixed,
            counted_stock=Decimal("300"),
            note="Sayım",
        )

        with self.assertRaisesMessage(ValidationError, "düzeltilemez"):
            correct_movement(
                movement=adjustment,
                data={
                    "occurred_at": self.now,
                    "destination_storage": self.fixed,
                    "liters": Decimal("60"),
                },
                reason="Sayım farkı yanlış",
                user=self.user,
            )
        adjustment.refresh_from_db()
        self.assertFalse(adjustment.is_voided)
