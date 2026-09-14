from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from .models import (
    ContractorFuelPrice,
    FuelMovement,
    FuelingRequest,
    OperatorStorageAccess,
    Region,
    StorageRegionChange,
    StorageUnit,
    Vehicle,
    VehicleRegionChange,
)


ZERO = Decimal("0")


def operator_storages_for(user):
    queryset = StorageUnit.active.select_related("region")
    if user.is_superuser:
        return queryset
    return queryset.filter(operator_accesses__user=user)


def contractor_price_at(occurred_at):
    if occurred_at is None:
        return None
    return (
        ContractorFuelPrice.objects.filter(effective_from__lte=occurred_at)
        .order_by("-effective_from", "-pk")
        .first()
    )


def prepare_movement_pricing_data(*, movement_type, data):
    prepared = data.copy()
    if movement_type == FuelMovement.MovementType.SUPPLIER_RECEIPT:
        if prepared.get("purchase_unit_price") is None:
            raise ValidationError(
                {"purchase_unit_price": "Alış birim fiyatı girilmelidir."}
            )
        return prepared

    if movement_type != FuelMovement.MovementType.VEHICLE_FUELING:
        return prepared

    vehicle = prepared.get("vehicle")
    if vehicle is None or not vehicle.is_contractor:
        return prepared

    reference_price = prepared.get("contractor_reference_unit_price")
    contractor_price = prepared.get("contractor_unit_price")
    if reference_price is None and contractor_price is None:
        price_definition = contractor_price_at(prepared.get("occurred_at"))
        if price_definition is None:
            raise ValidationError(
                {
                    "contractor_reference_unit_price": (
                        "Bu tarih için taşeron yakıt fiyatı tanımlanmamış. "
                        "Önce taşeron fiyatı tanımlayın."
                    )
                }
            )
        prepared["contractor_reference_unit_price"] = (
            price_definition.reference_unit_price
        )
        prepared["contractor_unit_price"] = price_definition.contractor_unit_price
    elif reference_price is None or contractor_price is None:
        raise ValidationError(
            {
                "contractor_reference_unit_price": (
                    "Taşeron dolumunda referans ve yansıtma fiyatı birlikte girilmelidir."
                )
            }
        )
    return prepared


@transaction.atomic
def submit_fueling_request(*, user, data):
    data = prepare_movement_pricing_data(
        movement_type=FuelMovement.MovementType.VEHICLE_FUELING,
        data=data,
    )
    source_storage = data["source_storage"]
    if not operator_storages_for(user).filter(pk=source_storage.pk).exists():
        raise ValidationError(
            {"source_storage": "Bu tank veya tanker için işlem yetkiniz bulunmuyor."}
        )

    occurred_at = data["occurred_at"]
    if occurred_at > timezone.now():
        raise ValidationError({"occurred_at": "Hareket tarihi ileri tarihli olamaz."})
    request_record = FuelingRequest(
        submitted_by=user,
        source_region=storage_region_at(source_storage, occurred_at),
        vehicle_region=vehicle_region_at(data["vehicle"], occurred_at),
        **data,
    )
    request_record.full_clean()
    request_record.save(force_insert=True)
    return request_record


@transaction.atomic
def approve_fueling_request(*, request_id, user):
    request_record = (
        FuelingRequest.objects.select_for_update()
        .select_related("source_storage", "vehicle", "submitted_by")
        .get(pk=request_id)
    )
    if request_record.status != FuelingRequest.Status.PENDING:
        raise ValidationError("Bu saha kaydı daha önce sonuçlandırılmış.")
    if not request_record.source_storage.is_active:
        raise ValidationError("Kaynak tank veya tanker artık aktif değil.")
    if not request_record.vehicle.is_active:
        raise ValidationError("Araç veya makine artık aktif değil.")

    movement = create_movement(
        user=request_record.submitted_by,
        movement_type=FuelMovement.MovementType.VEHICLE_FUELING,
        data={
            "occurred_at": request_record.occurred_at,
            "source_storage": request_record.source_storage,
            "vehicle": request_record.vehicle,
            "liters": request_record.liters,
            "meter_value": request_record.meter_value,
            "contractor_reference_unit_price": (
                request_record.contractor_reference_unit_price
            ),
            "contractor_unit_price": request_record.contractor_unit_price,
            "note": request_record.note,
        },
    )
    request_record.status = FuelingRequest.Status.APPROVED
    request_record.decided_by = user
    request_record.decided_at = timezone.now()
    request_record.decision_reason = ""
    request_record.movement = movement
    request_record.contractor_reference_unit_price = (
        movement.contractor_reference_unit_price
    )
    request_record.contractor_unit_price = movement.contractor_unit_price
    request_record.full_clean()
    request_record.save(
        update_fields=[
            "status",
            "decided_by",
            "decided_at",
            "decision_reason",
            "movement",
            "contractor_reference_unit_price",
            "contractor_unit_price",
        ]
    )
    return request_record


@transaction.atomic
def reject_fueling_request(*, request_id, user, reason):
    reason = reason.strip()
    if not reason:
        raise ValidationError({"reason": "Ret gerekçesi zorunludur."})

    request_record = FuelingRequest.objects.select_for_update().get(pk=request_id)
    if request_record.status != FuelingRequest.Status.PENDING:
        raise ValidationError("Bu saha kaydı daha önce sonuçlandırılmış.")

    request_record.status = FuelingRequest.Status.REJECTED
    request_record.decided_by = user
    request_record.decided_at = timezone.now()
    request_record.decision_reason = reason
    request_record.full_clean()
    request_record.save(
        update_fields=[
            "status",
            "decided_by",
            "decided_at",
            "decision_reason",
        ]
    )
    return request_record


def active_movements(*, as_of=None):
    queryset = FuelMovement.objects.active()
    if as_of is not None:
        queryset = queryset.filter(occurred_at__lte=as_of)
    return queryset


def stock_by_storage(storages=None, *, as_of=None):
    if storages is None:
        storages = StorageUnit.objects.all()
    storages = list(storages)
    storage_ids = [storage.pk for storage in storages]
    balances = {storage_id: ZERO for storage_id in storage_ids}

    if not storage_ids:
        return balances

    movements = active_movements(as_of=as_of)
    incoming = (
        movements.filter(destination_storage_id__in=storage_ids)
        .values("destination_storage_id")
        .annotate(total=Sum("liters"))
    )
    outgoing = (
        movements.filter(source_storage_id__in=storage_ids)
        .values("source_storage_id")
        .annotate(total=Sum("liters"))
    )

    for row in incoming:
        balances[row["destination_storage_id"]] += row["total"] or ZERO
    for row in outgoing:
        balances[row["source_storage_id"]] -= row["total"] or ZERO
    return balances


def stock_for(storage, *, as_of=None):
    return stock_by_storage([storage], as_of=as_of).get(storage.pk, ZERO)


def negative_stock_warning_for(movement):
    """Return a user-facing warning when a movement leaves its source negative."""
    if not movement.source_storage_id:
        return None
    remaining = stock_for(movement.source_storage)
    if remaining >= ZERO:
        return None
    return (
        f"Uyarı: {movement.source_storage.name} stoğu eksiye düştü "
        f"({remaining:,.3f} L). Kalibrasyon ve kayıtları kontrol edin."
    )


def storage_region_at(storage, occurred_at):
    latest_change = (
        StorageRegionChange.objects.filter(
            storage=storage,
            occurred_at__lte=occurred_at,
        )
        .select_related("to_region")
        .order_by("-occurred_at", "-pk")
        .first()
    )
    if latest_change:
        return latest_change.to_region

    first_change = (
        StorageRegionChange.objects.filter(storage=storage)
        .select_related("from_region")
        .order_by("occurred_at", "pk")
        .first()
    )
    if first_change:
        return first_change.from_region
    return storage.region


@transaction.atomic
def change_storage_region(*, user, storage, to_region, occurred_at, reason):
    locked_storage = StorageUnit.objects.select_for_update().get(pk=storage.pk)
    destination_region = Region.objects.get(pk=to_region.pk)
    reason = reason.strip()

    if not destination_region.is_active:
        raise ValidationError({"to_region": "Yalnızca aktif bir bölge seçilebilir."})
    if locked_storage.region_id == destination_region.pk:
        raise ValidationError({"to_region": "Yeni bölge mevcut bölgeden farklı olmalıdır."})
    if occurred_at > timezone.now():
        raise ValidationError({"occurred_at": "Bölge değişikliği ileri tarihli olamaz."})
    if not reason:
        raise ValidationError({"reason": "Bölge değişikliği gerekçesi zorunludur."})

    latest_change = (
        locked_storage.region_changes.select_for_update()
        .order_by("-occurred_at", "-pk")
        .first()
    )
    if latest_change and occurred_at <= latest_change.occurred_at:
        raise ValidationError(
            {
                "occurred_at": (
                    "Değişiklik tarihi son bölge değişikliğinden sonra olmalıdır "
                    f"({timezone.localtime(latest_change.occurred_at):%d.%m.%Y %H:%M})."
                )
            }
        )

    change = StorageRegionChange(
        storage=locked_storage,
        from_region=locked_storage.region,
        to_region=destination_region,
        occurred_at=occurred_at,
        reason=reason,
        created_by=user,
    )
    change.full_clean()
    change.save(force_insert=True)
    locked_storage.region = destination_region
    locked_storage.save(update_fields=["region"])
    return change


def vehicle_region_at(vehicle, occurred_at):
    latest_change = (
        VehicleRegionChange.objects.filter(
            vehicle=vehicle,
            occurred_at__lte=occurred_at,
        )
        .select_related("to_region")
        .order_by("-occurred_at", "-pk")
        .first()
    )
    if latest_change:
        return latest_change.to_region

    first_change = (
        VehicleRegionChange.objects.filter(vehicle=vehicle)
        .select_related("from_region")
        .order_by("occurred_at", "pk")
        .first()
    )
    if first_change:
        return first_change.from_region
    return vehicle.region


@transaction.atomic
def change_vehicle_region(*, user, vehicle, to_region, occurred_at, reason):
    locked_vehicle = Vehicle.objects.select_for_update().get(pk=vehicle.pk)
    destination_region = Region.objects.get(pk=to_region.pk)
    reason = reason.strip()

    if not destination_region.is_active:
        raise ValidationError({"to_region": "Yalnızca aktif bir bölge seçilebilir."})
    if locked_vehicle.region_id == destination_region.pk:
        raise ValidationError({"to_region": "Yeni bölge mevcut bölgeden farklı olmalıdır."})
    if occurred_at > timezone.now():
        raise ValidationError({"occurred_at": "Bölge değişikliği ileri tarihli olamaz."})
    if not reason:
        raise ValidationError({"reason": "Bölge değişikliği gerekçesi zorunludur."})

    latest_change = (
        locked_vehicle.region_changes.select_for_update()
        .order_by("-occurred_at", "-pk")
        .first()
    )
    if latest_change and occurred_at <= latest_change.occurred_at:
        raise ValidationError(
            {
                "occurred_at": (
                    "Değişiklik tarihi son bölge değişikliğinden sonra olmalıdır "
                    f"({timezone.localtime(latest_change.occurred_at):%d.%m.%Y %H:%M})."
                )
            }
        )

    change = VehicleRegionChange(
        vehicle=locked_vehicle,
        from_region=locked_vehicle.region,
        to_region=destination_region,
        occurred_at=occurred_at,
        reason=reason,
        created_by=user,
    )
    change.full_clean()
    change.save(force_insert=True)
    locked_vehicle.region = destination_region
    locked_vehicle.save(update_fields=["region"])
    return change


def validate_vehicle_meter(movement, *, exclude_movement_id=None):
    if not movement.vehicle_id or movement.meter_value is None:
        return

    movements = FuelMovement.objects.active().filter(
        vehicle_id=movement.vehicle_id,
        meter_value__isnull=False,
    )
    if exclude_movement_id is not None:
        movements = movements.exclude(pk=exclude_movement_id)

    previous = (
        movements.filter(occurred_at__lte=movement.occurred_at)
        .order_by("-occurred_at", "-pk")
        .first()
    )
    following = (
        movements.filter(occurred_at__gt=movement.occurred_at)
        .order_by("occurred_at", "pk")
        .first()
    )
    if previous and movement.meter_value < previous.meter_value:
        raise ValidationError(
            {
                "meter_value": (
                    "Sayaç değeri önceki dolumdaki değerden küçük olamaz "
                    f"({previous.meter_value})."
                )
            }
        )
    if following and movement.meter_value > following.meter_value:
        raise ValidationError(
            {
                "meter_value": (
                    "Sayaç değeri sonraki dolumdaki değerden büyük olamaz "
                    f"({following.meter_value})."
                )
            }
        )


@transaction.atomic
def create_movement(*, user, movement_type, data):
    data = data.copy()
    source = data.get("source_storage")
    destination = data.get("destination_storage")
    lock_ids = sorted(
        {storage.pk for storage in (source, destination) if storage is not None}
    )
    locked = {
        storage.pk: storage
        for storage in StorageUnit.objects.select_for_update()
        .filter(pk__in=lock_ids)
        .order_by("pk")
    }

    if source is not None:
        data["source_storage"] = locked[source.pk]
    if destination is not None:
        data["destination_storage"] = locked[destination.pk]
    if data.get("vehicle") is not None:
        data["vehicle"] = Vehicle.objects.select_for_update().get(
            pk=data["vehicle"].pk
        )

    data = prepare_movement_pricing_data(
        movement_type=movement_type,
        data=data,
    )

    occurred_at = data["occurred_at"]
    if occurred_at > timezone.now():
        raise ValidationError({"occurred_at": "Hareket tarihi ileri tarihli olamaz."})
    data["source_region"] = (
        storage_region_at(data["source_storage"], occurred_at)
        if data.get("source_storage")
        else None
    )
    data["destination_region"] = (
        storage_region_at(data["destination_storage"], occurred_at)
        if data.get("destination_storage")
        else None
    )
    data["vehicle_region"] = (
        vehicle_region_at(data["vehicle"], occurred_at)
        if data.get("vehicle")
        else None
    )

    movement = FuelMovement(
        movement_type=movement_type,
        created_by=user,
        **data,
    )
    movement.full_clean()
    validate_vehicle_meter(movement)

    if movement.destination_storage_id and movement.destination_storage.capacity_liters:
        current_stock = stock_for(movement.destination_storage)
        final_stock = current_stock + movement.liters
        if final_stock > movement.destination_storage.capacity_liters:
            raise ValidationError(
                {
                    "liters": (
                        f"Kapasite aşılıyor. {movement.destination_storage.name} için "
                        f"işlem sonrası stok {final_stock:,.3f} L, kapasite ise "
                        f"{movement.destination_storage.capacity_liters:,.3f} L."
                    )
                }
            )

    movement.save(force_insert=True)
    return movement


@transaction.atomic
def create_stock_adjustment_from_count(
    *, user, occurred_at, storage, counted_stock, note
):
    locked_storage = StorageUnit.objects.select_for_update().get(pk=storage.pk)
    current_stock = stock_for(locked_storage)
    difference = counted_stock - current_stock

    if counted_stock < ZERO:
        raise ValidationError({"counted_stock": "Sayılan stok negatif olamaz."})
    if (
        locked_storage.capacity_liters is not None
        and counted_stock > locked_storage.capacity_liters
    ):
        raise ValidationError(
            {
                "counted_stock": (
                    "Sayılan stok tank kapasitesini aşıyor "
                    f"({counted_stock:,.3f} / {locked_storage.capacity_liters:,.3f} L)."
                )
            }
        )
    if difference == ZERO:
        raise ValidationError(
            {"counted_stock": "Sayılan stok mevcut stokla aynı; düzeltme gerekmiyor."}
        )

    audit_note = (
        f"{note.strip()} · Sistem stoğu: {current_stock:.3f} L · "
        f"Sayılan stok: {counted_stock:.3f} L"
    )
    is_increase = difference > ZERO
    return create_movement(
        user=user,
        movement_type=FuelMovement.MovementType.STOCK_ADJUSTMENT,
        data={
            "occurred_at": occurred_at,
            "source_storage": None if is_increase else locked_storage,
            "destination_storage": locked_storage if is_increase else None,
            "liters": abs(difference),
            "note": audit_note,
        },
    )


@transaction.atomic
def void_movement(*, movement_id, user, reason):
    reason = reason.strip()
    if not reason:
        raise ValidationError({"reason": "İptal gerekçesi zorunludur."})

    movement = FuelMovement.objects.select_for_update().get(pk=movement_id)
    if movement.is_voided:
        raise ValidationError({"reason": "Bu hareket daha önce iptal edilmiş."})

    lock_ids = sorted(
        storage_id
        for storage_id in (
            movement.source_storage_id,
            movement.destination_storage_id,
        )
        if storage_id is not None
    )
    locked = {
        storage.pk: storage
        for storage in StorageUnit.objects.select_for_update()
        .filter(pk__in=lock_ids)
        .order_by("pk")
    }

    if movement.source_storage_id:
        source = locked[movement.source_storage_id]
        if source.capacity_liters:
            final_stock = stock_for(source) + movement.liters
            if final_stock > source.capacity_liters:
                raise ValidationError(
                    {
                        "reason": (
                            f"Bu hareket iptal edilirse {source.name} stoğu kapasiteyi "
                            f"aşar ({final_stock:,.3f} / {source.capacity_liters:,.3f} L)."
                        )
                    }
                )

    movement.voided_at = timezone.now()
    movement.voided_by = user
    movement.void_reason = reason
    movement.save(update_fields=["voided_at", "voided_by", "void_reason"])
    return movement


def period_totals(queryset):
    purchase_amount = ExpressionWrapper(
        F("liters") * F("purchase_unit_price"),
        output_field=DecimalField(max_digits=26, decimal_places=7),
    )
    totals = queryset.aggregate(
        received=Sum(
            "liters",
            filter=Q(movement_type=FuelMovement.MovementType.SUPPLIER_RECEIPT),
        ),
        consumed=Sum(
            "liters",
            filter=Q(movement_type=FuelMovement.MovementType.VEHICLE_FUELING),
        ),
        transferred=Sum(
            "liters",
            filter=Q(movement_type=FuelMovement.MovementType.INTERNAL_TRANSFER),
        ),
        purchase_amount=Sum(
            purchase_amount,
            filter=Q(
                movement_type=FuelMovement.MovementType.SUPPLIER_RECEIPT,
                purchase_unit_price__isnull=False,
            ),
        ),
    )
    return {key: value or ZERO for key, value in totals.items()}


def vehicle_consumption_summary(queryset):
    return list(
        queryset.filter(
            movement_type=FuelMovement.MovementType.VEHICLE_FUELING,
            vehicle__isnull=False,
        )
        .values(
            "vehicle_id",
            "vehicle__code",
            "vehicle__name",
            "vehicle__tag_code",
            "vehicle__meter_type",
            "vehicle__is_contractor",
            "vehicle_region_id",
            "vehicle_region__name",
            "vehicle__organization",
        )
        .annotate(total_liters=Sum("liters"), fueling_count=Count("id"))
        .order_by("-total_liters", "vehicle__code")
    )


def supplier_receipt_summary(queryset):
    purchase_amount = ExpressionWrapper(
        F("liters") * F("purchase_unit_price"),
        output_field=DecimalField(max_digits=26, decimal_places=7),
    )
    rows = list(
        queryset.filter(
            movement_type=FuelMovement.MovementType.SUPPLIER_RECEIPT,
            supplier__isnull=False,
        )
        .values("supplier_id", "supplier__name")
        .annotate(
            delivery_count=Count("id"),
            total_liters=Sum("liters"),
            total_external_scale_kg=Sum("external_scale_kg"),
            total_company_scale_kg=Sum("company_scale_kg"),
            scale_pair_count=Count(
                "id",
                filter=Q(
                    external_scale_kg__isnull=False,
                    company_scale_kg__isnull=False,
                ),
            ),
            scale_difference_kg=Sum(
                F("external_scale_kg") - F("company_scale_kg"),
                filter=Q(
                    external_scale_kg__isnull=False,
                    company_scale_kg__isnull=False,
                ),
            ),
            priced_liters=Sum(
                "liters",
                filter=Q(purchase_unit_price__isnull=False),
            ),
            total_purchase_amount=Sum(
                purchase_amount,
                filter=Q(purchase_unit_price__isnull=False),
            ),
        )
        .order_by("supplier__name")
    )
    for row in rows:
        priced_liters = row["priced_liters"] or ZERO
        purchase_amount_total = row["total_purchase_amount"] or ZERO
        row["average_unit_price"] = (
            purchase_amount_total / priced_liters if priced_liters else None
        )
    return rows


def contractor_pricing_totals(movements):
    priced_liters = ZERO
    reference_total = ZERO
    charged_total = ZERO
    priced_fueling_count = 0
    unpriced_fueling_count = 0
    for movement in movements:
        if movement.movement_type != FuelMovement.MovementType.VEHICLE_FUELING:
            continue
        if not movement.vehicle_id or not movement.vehicle.is_contractor:
            continue
        if (
            movement.contractor_reference_unit_price is None
            or movement.contractor_unit_price is None
        ):
            unpriced_fueling_count += 1
            continue
        priced_fueling_count += 1
        priced_liters += movement.liters
        reference_total += movement.contractor_reference_total
        charged_total += movement.contractor_charged_total

    return {
        "priced_liters": priced_liters,
        "reference_total": reference_total,
        "charged_total": charged_total,
        "difference_total": charged_total - reference_total,
        "weighted_average_unit_price": (
            charged_total / priced_liters if priced_liters else None
        ),
        "priced_fueling_count": priced_fueling_count,
        "unpriced_fueling_count": unpriced_fueling_count,
    }


def daily_activity_summary(queryset):
    return list(
        queryset.annotate(
            day=TruncDate("occurred_at", tzinfo=timezone.get_current_timezone())
        )
        .values("day")
        .annotate(
            received=Sum(
                "liters",
                filter=Q(
                    movement_type=FuelMovement.MovementType.SUPPLIER_RECEIPT
                ),
            ),
            transferred=Sum(
                "liters",
                filter=Q(
                    movement_type=FuelMovement.MovementType.INTERNAL_TRANSFER
                ),
            ),
            consumed=Sum(
                "liters",
                filter=Q(
                    movement_type=FuelMovement.MovementType.VEHICLE_FUELING
                ),
            ),
        )
        .order_by("day")
    )


def region_activity_summary(queryset):
    rows = defaultdict(
        lambda: {
            "region_id": None,
            "region_name": "",
            "received": ZERO,
            "transfer_in": ZERO,
            "transfer_out": ZERO,
            "consumed": ZERO,
        }
    )

    def row_for(region):
        row = rows[region.pk]
        row["region_id"] = region.pk
        row["region_name"] = region.name
        return row

    for movement in queryset:
        if (
            movement.movement_type == FuelMovement.MovementType.SUPPLIER_RECEIPT
            and movement.destination_region_id
        ):
            row_for(movement.destination_region)["received"] += movement.liters
        elif movement.movement_type == FuelMovement.MovementType.INTERNAL_TRANSFER:
            if movement.source_region_id:
                row_for(movement.source_region)["transfer_out"] += movement.liters
            if movement.destination_region_id:
                row_for(movement.destination_region)["transfer_in"] += movement.liters
        elif (
            movement.movement_type == FuelMovement.MovementType.VEHICLE_FUELING
            and movement.vehicle_region_id
        ):
            row_for(movement.vehicle_region)["consumed"] += movement.liters

    return sorted(rows.values(), key=lambda row: row["region_name"])


def storage_activity_summary(queryset, storages):
    storages = list(storages)
    balances = stock_by_storage(storages)
    rows = {
        storage.pk: {
            "storage": storage,
            "opening_added": ZERO,
            "received": ZERO,
            "transfer_in": ZERO,
            "transfer_out": ZERO,
            "vehicle_out": ZERO,
            "adjustment_net": ZERO,
            "current_stock": balances[storage.pk],
            "is_negative_stock": balances[storage.pk] < ZERO,
        }
        for storage in storages
    }

    for movement in queryset:
        if (
            movement.movement_type == FuelMovement.MovementType.OPENING_BALANCE
            and movement.destination_storage_id in rows
        ):
            rows[movement.destination_storage_id]["opening_added"] += movement.liters
        elif (
            movement.movement_type == FuelMovement.MovementType.SUPPLIER_RECEIPT
            and movement.destination_storage_id in rows
        ):
            rows[movement.destination_storage_id]["received"] += movement.liters
        elif movement.movement_type == FuelMovement.MovementType.INTERNAL_TRANSFER:
            if movement.source_storage_id in rows:
                rows[movement.source_storage_id]["transfer_out"] += movement.liters
            if movement.destination_storage_id in rows:
                rows[movement.destination_storage_id]["transfer_in"] += movement.liters
        elif (
            movement.movement_type == FuelMovement.MovementType.VEHICLE_FUELING
            and movement.source_storage_id in rows
        ):
            rows[movement.source_storage_id]["vehicle_out"] += movement.liters
        elif movement.movement_type == FuelMovement.MovementType.STOCK_ADJUSTMENT:
            if movement.source_storage_id in rows:
                rows[movement.source_storage_id]["adjustment_net"] -= movement.liters
            if movement.destination_storage_id in rows:
                rows[movement.destination_storage_id]["adjustment_net"] += movement.liters

    return list(rows.values())


def attach_balances(storages, balances):
    result = []
    for storage in storages:
        storage.current_stock = balances.get(storage.pk, ZERO)
        storage.is_negative_stock = storage.current_stock < ZERO
        storage.is_low_stock = (
            storage.low_stock_threshold > ZERO
            and storage.current_stock <= storage.low_stock_threshold
        )
        if storage.capacity_liters:
            storage.fill_ratio = max(
                ZERO, min(Decimal("1"), storage.current_stock / storage.capacity_liters)
            )
            storage.fill_percent = float(storage.fill_ratio * Decimal("100"))
        else:
            storage.fill_ratio = None
            storage.fill_percent = None
        result.append(storage)
    return result
