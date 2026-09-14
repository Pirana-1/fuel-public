from django import forms
from django.utils import timezone

from .forms import (
    InternalTransferForm,
    OpeningBalanceForm,
    SupplierReceiptForm,
    VehicleFuelingCorrectionForm,
)
from .models import FuelMovement


class MovementCorrectionReasonForm(forms.Form):
    correction_reason = forms.CharField(
        label="Düzeltme nedeni",
        max_length=500,
        widget=forms.Textarea(
            attrs={
                "rows": 3,
                "placeholder": "Yanlış girilen bilgiyi ve düzeltme nedenini kısaca yazın.",
            }
        ),
    )


def form_class_for_movement(movement):
    return {
        FuelMovement.MovementType.OPENING_BALANCE: OpeningBalanceForm,
        FuelMovement.MovementType.SUPPLIER_RECEIPT: SupplierReceiptForm,
        FuelMovement.MovementType.INTERNAL_TRANSFER: InternalTransferForm,
        FuelMovement.MovementType.VEHICLE_FUELING: VehicleFuelingCorrectionForm,
    }[movement.movement_type]


def initial_for_movement(movement, form_class):
    initial = {}
    for field_name in form_class.base_fields:
        if hasattr(movement, field_name):
            initial[field_name] = getattr(movement, field_name)
    if "occurred_at" in initial:
        initial["occurred_at"] = timezone.localtime(initial["occurred_at"]).strftime(
            "%Y-%m-%dT%H:%M"
        )

    return initial


def include_original_choices(form, movement):
    field_values = {
        "supplier": movement.supplier,
        "source_storage": movement.source_storage,
        "destination_storage": movement.destination_storage,
        "vehicle": movement.vehicle,
    }
    for field_name, value in field_values.items():
        if value is None or field_name not in form.fields:
            continue
        field = form.fields[field_name]
        field.queryset = (field.queryset | field.queryset.model.objects.filter(pk=value.pk)).distinct()
