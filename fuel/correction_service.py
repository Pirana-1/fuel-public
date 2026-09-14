from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import FuelMovement, StorageUnit, Vehicle
from .services import (
    prepare_movement_pricing_data,
    stock_for,
    storage_region_at,
    validate_vehicle_meter,
    vehicle_region_at,
)


def _apply_movement_delta(deltas, movement, multiplier=Decimal("1")):
    """Apply a movement's stock effect to a storage-id keyed delta map."""
    liters = Decimal(movement.liters) * multiplier
    if movement.source_storage_id:
        deltas[movement.source_storage_id] -= liters
    if movement.destination_storage_id:
        deltas[movement.destination_storage_id] += liters


def _validate_final_stocks(storages, deltas):
    errors = []
    for storage in storages:
        final_stock = stock_for(storage) + deltas[storage.pk]
        if storage.capacity_liters is not None and final_stock > storage.capacity_liters:
            errors.append(
                f"{storage.name} için düzeltme sonrası stok kapasiteyi aşıyor "
                f"({final_stock} / {storage.capacity_liters} L)."
            )
    if errors:
        raise ValidationError(errors)


@transaction.atomic
def correct_movement(*, movement, data, reason, user):
    """Void an incorrect movement and create its auditable replacement atomically."""
    original = FuelMovement.objects.select_for_update().get(pk=movement.pk)
    if original.is_voided:
        raise ValidationError("İptal edilmiş bir hareket düzeltilemez.")
    if hasattr(original, "correction"):
        raise ValidationError("Bu hareket daha önce düzeltilmiş.")
    if original.movement_type == FuelMovement.MovementType.STOCK_ADJUSTMENT:
        raise ValidationError(
            "Stok düzeltmeleri fiziksel sayım kaydıdır ve düzeltilemez; "
            "hatalı kaydı iptal edip yeni sayım girin."
        )
    if not reason or not reason.strip():
        raise ValidationError({"correction_reason": "Düzeltme nedeni zorunludur."})
    if data.get("occurred_at") and data["occurred_at"] > timezone.now():
        raise ValidationError({"occurred_at": "Hareket tarihi ileri tarihli olamaz."})

    data = prepare_movement_pricing_data(
        movement_type=original.movement_type,
        data=data,
    )
    candidate = FuelMovement(
        movement_type=original.movement_type,
        created_by=user,
        corrects=original,
        **data,
    )

    # create_movement ile aynı kilit sırası: önce depolar (pk sırasıyla), sonra araç.
    storage_ids = {
        storage_id
        for storage_id in (
            original.source_storage_id,
            original.destination_storage_id,
            candidate.source_storage_id,
            candidate.destination_storage_id,
        )
        if storage_id
    }
    storages = list(
        StorageUnit.objects.select_for_update()
        .filter(pk__in=storage_ids)
        .order_by("pk")
    )
    if candidate.vehicle_id:
        candidate.vehicle = Vehicle.objects.select_for_update().get(
            pk=candidate.vehicle_id
        )

    if candidate.source_storage_id:
        candidate.source_region = storage_region_at(
            candidate.source_storage,
            candidate.occurred_at,
        )
    if candidate.destination_storage_id:
        candidate.destination_region = storage_region_at(
            candidate.destination_storage,
            candidate.occurred_at,
        )
    if candidate.vehicle_id:
        candidate.vehicle_region = vehicle_region_at(
            candidate.vehicle,
            candidate.occurred_at,
        )
    candidate.full_clean()
    validate_vehicle_meter(candidate, exclude_movement_id=original.pk)

    deltas = defaultdict(lambda: Decimal("0"))
    _apply_movement_delta(deltas, original, Decimal("-1"))
    _apply_movement_delta(deltas, candidate)
    _validate_final_stocks(storages, deltas)

    original.void_reason = f"Düzeltildi: {reason.strip()}"
    original.voided_by = user
    original.voided_at = timezone.now()
    original.save(update_fields=["void_reason", "voided_by", "voided_at"])
    candidate.save()
    return candidate
