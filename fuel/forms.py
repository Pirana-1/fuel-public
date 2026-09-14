from decimal import Decimal

from django import forms
from django.db.models import Q
from django.utils import timezone

from .models import (
    ContractorFuelPrice,
    FuelMovement,
    FuelingRequest,
    Region,
    StorageUnit,
    Supplier,
    Vehicle,
)
from .services import (
    change_storage_region,
    change_vehicle_region,
    contractor_price_at,
    create_movement,
    create_stock_adjustment_from_count,
    operator_storages_for,
    submit_fueling_request,
)


class StyledFormMixin:
    def apply_styles(self):
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                widget.attrs.setdefault("class", "form-check-input")
            else:
                widget.attrs.setdefault("class", "form-control")


def _make_searchable(field, placeholder):
    field.widget.attrs["data-searchable"] = "true"
    field.widget.attrs["data-search-placeholder"] = placeholder


def _storage_label(storage):
    return f"{storage.code} — {storage.name} · {storage.region.name}"


def _vehicle_label(vehicle):
    parts = [vehicle.code, vehicle.name, vehicle.region.name]
    if vehicle.is_contractor:
        parts.append("Taşeron")
    parts.append(vehicle.get_meter_type_display())
    return " · ".join(parts)


def _vehicle_search_text(vehicle):
    parts = [vehicle.code]
    if vehicle.tag_code:
        parts.append(f"RFID {vehicle.tag_code}")
    parts.append(vehicle.name)
    if vehicle.organization:
        parts.append(vehicle.organization)
    if vehicle.is_contractor:
        parts.append("Taşeron")
    parts.append(vehicle.region.name)
    return " · ".join(parts)


class VehicleSelectWidget(forms.Select):
    def create_option(
        self, name, value, label, selected, index, subindex=None, attrs=None
    ):
        option = super().create_option(
            name, value, label, selected, index, subindex=subindex, attrs=attrs
        )
        instance = getattr(value, "instance", None)
        if instance is not None:
            option["attrs"]["data-search"] = _vehicle_search_text(instance)
        return option


class DateTimeLocalInput(forms.DateTimeInput):
    input_type = "datetime-local"


class ContractorPricingFormMixin:
    contractor_price_field_names = (
        "contractor_reference_unit_price",
        "contractor_unit_price",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name in self.contractor_price_field_names:
            field = self.fields[field_name]
            field.min_value = Decimal("0.0001")
            field.widget.attrs.update(
                {
                    "min": "0.0001",
                    "step": "0.0001",
                    "data-contractor-price": field_name,
                }
            )
        self.fields["contractor_reference_unit_price"].label = (
            "Referans ortalama fiyat (TL/L)"
        )
        self.fields["contractor_unit_price"].label = (
            "Taşerona yansıtılacak fiyat (TL/L)"
        )

        if not self.is_bound:
            price_definition = contractor_price_at(timezone.now())
            if price_definition is not None:
                self.fields["contractor_reference_unit_price"].initial = (
                    price_definition.reference_unit_price
                )
                self.fields["contractor_unit_price"].initial = (
                    price_definition.contractor_unit_price
                )

    def clean(self):
        cleaned_data = super().clean()
        vehicle = cleaned_data.get("vehicle")
        if vehicle is None:
            return cleaned_data

        if not vehicle.is_contractor:
            for field_name in self.contractor_price_field_names:
                cleaned_data[field_name] = None
            return cleaned_data

        reference_price = cleaned_data.get("contractor_reference_unit_price")
        contractor_price = cleaned_data.get("contractor_unit_price")
        if reference_price is None and contractor_price is None:
            price_definition = contractor_price_at(cleaned_data.get("occurred_at"))
            if price_definition is not None:
                cleaned_data["contractor_reference_unit_price"] = (
                    price_definition.reference_unit_price
                )
                cleaned_data["contractor_unit_price"] = (
                    price_definition.contractor_unit_price
                )
            else:
                self.add_error(
                    "contractor_reference_unit_price",
                    "Bu tarih için taşeron yakıt fiyatı tanımlanmamış.",
                )
        elif reference_price is None or contractor_price is None:
            self.add_error(
                "contractor_reference_unit_price",
                "Referans ve yansıtma fiyatı birlikte girilmelidir.",
            )
        return cleaned_data


class AutomaticContractorPricingFormMixin:
    """Attach the effective contractor price before ModelForm model validation."""

    server_managed_price_fields = (
        "contractor_reference_unit_price",
        "contractor_unit_price",
    )

    def _update_errors(self, errors):
        if hasattr(errors, "error_dict"):
            for field_name in self.server_managed_price_fields:
                errors.error_dict.pop(field_name, None)
        return super()._update_errors(errors)

    def clean(self):
        cleaned_data = super().clean()
        vehicle = cleaned_data.get("vehicle")
        if vehicle is None:
            return cleaned_data

        if not vehicle.is_contractor:
            self.instance.contractor_reference_unit_price = None
            self.instance.contractor_unit_price = None
            return cleaned_data

        price_definition = contractor_price_at(cleaned_data.get("occurred_at"))
        if price_definition is None:
            self.add_error(
                None,
                "Bu tarih için taşeron yakıt fiyatı tanımlanmamış. "
                "Yöneticinize bilgi verin.",
            )
            return cleaned_data

        self.instance.contractor_reference_unit_price = (
            price_definition.reference_unit_price
        )
        self.instance.contractor_unit_price = price_definition.contractor_unit_price
        return cleaned_data


class BaseMovementForm(StyledFormMixin, forms.ModelForm):
    movement_type = None

    class Meta:
        model = FuelMovement
        fields = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.movement_type = self.movement_type
        if "occurred_at" in self.fields and not self.is_bound:
            self.fields["occurred_at"].initial = timezone.localtime().replace(
                second=0, microsecond=0
            )
        self.apply_styles()

    def save_for_user(self, user):
        data = self.movement_data()
        return create_movement(
            user=user,
            movement_type=self.movement_type,
            data=data,
        )

    def movement_data(self):
        return {name: self.cleaned_data.get(name) for name in self.Meta.fields}


class OpeningBalanceForm(BaseMovementForm):
    movement_type = FuelMovement.MovementType.OPENING_BALANCE

    class Meta:
        model = FuelMovement
        fields = ["occurred_at", "destination_storage", "liters", "note"]
        widgets = {"occurred_at": DateTimeLocalInput(format="%Y-%m-%dT%H:%M")}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["destination_storage"].queryset = StorageUnit.active.select_related("region")
        self.fields["destination_storage"].label = "Tank veya tanker"
        self.fields["destination_storage"].label_from_instance = _storage_label
        _make_searchable(self.fields["destination_storage"], "Tank veya tanker ara")


class SupplierReceiptForm(BaseMovementForm):
    movement_type = FuelMovement.MovementType.SUPPLIER_RECEIPT

    class Meta:
        model = FuelMovement
        fields = [
            "occurred_at",
            "supplier",
            "destination_storage",
            "liters",
            "purchase_unit_price",
            "delivery_note",
            "external_scale_kg",
            "company_scale_kg",
            "note",
        ]
        widgets = {
            "occurred_at": DateTimeLocalInput(format="%Y-%m-%dT%H:%M"),
            "note": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["supplier"].queryset = Supplier.active.all()
        self.fields["destination_storage"].queryset = StorageUnit.active.filter(
            kind=StorageUnit.Kind.FIXED_TANK
        ).select_related("region")
        self.fields["supplier"].required = True
        self.fields["supplier"].empty_label = "Tedarikçi seçin"
        self.fields["destination_storage"].required = True
        self.fields["destination_storage"].empty_label = "Sabit tank seçin"
        self.fields["destination_storage"].label = "Yakıtın girdiği sabit tank"
        self.fields["destination_storage"].label_from_instance = _storage_label
        self.fields["purchase_unit_price"].required = True
        self.fields["purchase_unit_price"].min_value = Decimal("0.0001")
        self.fields["purchase_unit_price"].widget.attrs.update(
            {"min": "0.0001", "step": "0.0001"}
        )
        self.fields["purchase_unit_price"].help_text = (
            "İrsaliyedeki veya faturadaki gerçek alış fiyatını girin."
        )
        _make_searchable(self.fields["supplier"], "Tedarikçi ara")
        _make_searchable(self.fields["destination_storage"], "Sabit tank ara")


class InternalTransferForm(BaseMovementForm):
    movement_type = FuelMovement.MovementType.INTERNAL_TRANSFER

    class Meta:
        model = FuelMovement
        fields = [
            "occurred_at",
            "source_storage",
            "destination_storage",
            "liters",
            "note",
        ]
        widgets = {
            "occurred_at": DateTimeLocalInput(format="%Y-%m-%dT%H:%M"),
            "note": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["source_storage"].queryset = StorageUnit.active.filter(
            kind=StorageUnit.Kind.FIXED_TANK
        ).select_related("region")
        self.fields["destination_storage"].queryset = StorageUnit.active.filter(
            kind=StorageUnit.Kind.MOBILE_TANKER
        ).select_related("region")
        self.fields["source_storage"].required = True
        self.fields["source_storage"].empty_label = "Kaynak sabit tank seçin"
        self.fields["destination_storage"].required = True
        self.fields["destination_storage"].empty_label = "Hedef mobil tanker seçin"
        self.fields["source_storage"].label = "Kaynak sabit tank"
        self.fields["destination_storage"].label = "Hedef mobil tanker"
        self.fields["source_storage"].label_from_instance = _storage_label
        self.fields["destination_storage"].label_from_instance = _storage_label
        self.fields["source_storage"].widget.attrs["data-storage-status"] = "source"
        self.fields["destination_storage"].widget.attrs["data-storage-status"] = "destination"
        _make_searchable(self.fields["source_storage"], "Kaynak tank ara")
        _make_searchable(self.fields["destination_storage"], "Mobil tanker ara")


class _VehicleFuelingFormBase(BaseMovementForm):
    movement_type = FuelMovement.MovementType.VEHICLE_FUELING

    class Meta:
        model = FuelMovement
        fields = [
            "occurred_at",
            "source_storage",
            "vehicle",
            "liters",
            "meter_value",
            "note",
        ]
        widgets = {
            "occurred_at": DateTimeLocalInput(format="%Y-%m-%dT%H:%M"),
            "note": forms.Textarea(attrs={"rows": 3}),
            "vehicle": VehicleSelectWidget,
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["source_storage"].queryset = StorageUnit.active.select_related("region")
        self.fields["vehicle"].queryset = Vehicle.active.select_related("region")
        self.fields["source_storage"].required = True
        self.fields["source_storage"].empty_label = "Yakıt veren tank veya tanker seçin"
        self.fields["vehicle"].required = True
        self.fields["vehicle"].empty_label = "Araç veya makine seçin"
        self.fields["source_storage"].label = "Yakıtı veren tank veya tanker"
        self.fields["source_storage"].label_from_instance = _storage_label
        self.fields["vehicle"].label_from_instance = _vehicle_label
        self.fields["meter_value"].min_value = Decimal("0")
        self.fields["meter_value"].widget.attrs["min"] = "0"
        self.fields["source_storage"].widget.attrs["data-storage-status"] = "source"
        self.fields["vehicle"].widget.attrs["data-vehicle-meter"] = "true"
        _make_searchable(self.fields["source_storage"], "Tank veya tanker ara")
        _make_searchable(
            self.fields["vehicle"],
            "Plaka, RFID, araç veya açıklama ara",
        )


class VehicleFuelingForm(
    AutomaticContractorPricingFormMixin, _VehicleFuelingFormBase
):
    pass


class VehicleFuelingCorrectionForm(
    ContractorPricingFormMixin, _VehicleFuelingFormBase
):
    class Meta(_VehicleFuelingFormBase.Meta):
        fields = [
            "occurred_at",
            "source_storage",
            "vehicle",
            "liters",
            "meter_value",
            "contractor_reference_unit_price",
            "contractor_unit_price",
            "note",
        ]


class OperatorFuelingRequestForm(
    AutomaticContractorPricingFormMixin, StyledFormMixin, forms.ModelForm
):
    class Meta:
        model = FuelingRequest
        fields = [
            "occurred_at",
            "source_storage",
            "vehicle",
            "liters",
            "meter_value",
            "note",
        ]
        widgets = {
            "occurred_at": DateTimeLocalInput(format="%Y-%m-%dT%H:%M"),
            "note": forms.Textarea(attrs={"rows": 2}),
            "vehicle": VehicleSelectWidget,
        }

    def __init__(self, *args, user, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields["source_storage"].queryset = operator_storages_for(user)
        self.fields["vehicle"].queryset = Vehicle.active.select_related("region")
        self.fields["source_storage"].empty_label = "Tank veya tanker seçin"
        self.fields["vehicle"].empty_label = "Plaka, RFID veya araç seçin"
        self.fields["source_storage"].label_from_instance = _storage_label
        self.fields["vehicle"].label_from_instance = _vehicle_label
        self.fields["meter_value"].min_value = Decimal("0")
        self.fields["meter_value"].widget.attrs["min"] = "0"
        self.fields["source_storage"].widget.attrs["data-storage-status"] = "source"
        self.fields["vehicle"].widget.attrs["data-vehicle-meter"] = "true"
        _make_searchable(self.fields["source_storage"], "Yetkili tank veya tanker ara")
        _make_searchable(
            self.fields["vehicle"],
            "Plaka, RFID, araç veya açıklama ara",
        )
        if not self.is_bound:
            self.fields["occurred_at"].initial = timezone.localtime().replace(
                second=0, microsecond=0
            )
            if self.fields["source_storage"].queryset.count() == 1:
                self.fields["source_storage"].initial = (
                    self.fields["source_storage"].queryset.first()
                )
        self.apply_styles()

    def save_for_user(self):
        return submit_fueling_request(
            user=self.user,
            data={name: self.cleaned_data.get(name) for name in self.Meta.fields},
        )


class ContractorFuelPriceForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = ContractorFuelPrice
        fields = [
            "effective_from",
            "reference_unit_price",
            "contractor_unit_price",
            "note",
        ]
        widgets = {
            "effective_from": DateTimeLocalInput(format="%Y-%m-%dT%H:%M"),
            "note": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            self.fields["effective_from"].initial = timezone.localtime().replace(
                second=0, microsecond=0
            )
        for field_name in ("reference_unit_price", "contractor_unit_price"):
            self.fields[field_name].min_value = Decimal("0.0001")
            self.fields[field_name].widget.attrs.update(
                {"min": "0.0001", "step": "0.0001"}
            )
        self.fields["reference_unit_price"].help_text = (
            "Operasyonun baz alacağı elle belirlenen ortalama maliyet."
        )
        self.fields["contractor_unit_price"].help_text = (
            "Taşeron dolumlarına varsayılan olarak yansıtılacak fiyat."
        )
        self.apply_styles()


class RejectFuelingRequestForm(StyledFormMixin, forms.Form):
    reason = forms.CharField(
        label="Ret gerekçesi",
        min_length=3,
        widget=forms.Textarea(attrs={"rows": 2}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_styles()


class StockAdjustmentForm(StyledFormMixin, forms.Form):
    occurred_at = forms.DateTimeField(
        label="Sayım tarihi",
        widget=DateTimeLocalInput(format="%Y-%m-%dT%H:%M"),
    )
    storage = forms.ModelChoiceField(
        label="Tank veya tanker",
        queryset=StorageUnit.objects.none(),
        empty_label="Tank veya tanker seçin",
    )
    counted_stock = forms.DecimalField(
        label="Sayılan stok (L)",
        min_value=Decimal("0"),
        max_digits=14,
        decimal_places=3,
    )
    note = forms.CharField(
        label="Düzeltme gerekçesi",
        min_length=3,
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["storage"].queryset = StorageUnit.active.select_related("region")
        self.fields["storage"].label_from_instance = _storage_label
        self.fields["storage"].widget.attrs["data-storage-status"] = "adjustment"
        _make_searchable(self.fields["storage"], "Tank veya tanker ara")
        if not self.is_bound:
            self.fields["occurred_at"].initial = timezone.localtime().replace(
                second=0, microsecond=0
            )
        self.apply_styles()

    def save_for_user(self, user):
        return create_stock_adjustment_from_count(
            user=user,
            occurred_at=self.cleaned_data["occurred_at"],
            storage=self.cleaned_data["storage"],
            counted_stock=self.cleaned_data["counted_stock"],
            note=self.cleaned_data["note"],
        )


class MovementVoidForm(StyledFormMixin, forms.Form):
    reason = forms.CharField(
        label="İptal gerekçesi",
        min_length=3,
        widget=forms.Textarea(attrs={"rows": 4}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_styles()


class RegionForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Region
        fields = ["name", "code", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_styles()

    def clean_is_active(self):
        is_active = self.cleaned_data["is_active"]
        if self.instance.pk and not is_active:
            has_active_children = (
                self.instance.storage_units.filter(is_active=True).exists()
                or self.instance.vehicles.filter(is_active=True).exists()
            )
            if has_active_children:
                raise forms.ValidationError(
                    "Aktif tank, tanker veya araç bağlıyken bölge pasife alınamaz."
                )
        return is_active


class SupplierForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Supplier
        fields = ["name", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_styles()


class StorageUnitForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = StorageUnit
        fields = [
            "name",
            "code",
            "kind",
            "region",
            "capacity_liters",
            "low_stock_threshold",
            "is_active",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["kind"].choices = (
            ("", "Tür seçin"),
            *StorageUnit.Kind.choices,
        )
        region_filter = Q(is_active=True)
        if self.instance.pk:
            region_filter |= Q(pk=self.instance.region_id)
        self.fields["region"].queryset = Region.objects.filter(region_filter)
        self.fields["region"].empty_label = "Bölge seçin"
        self.fields["capacity_liters"].min_value = Decimal("0.001")
        self.fields["low_stock_threshold"].min_value = Decimal("0")
        if self.instance.pk:
            self.fields["region"].disabled = True
            self.fields["region"].help_text = (
                "Bölge değişiklikleri listeden ayrı ve izlenebilir bir işlemle yapılır."
            )
        if self.instance.pk and (
            self.instance.outgoing_movements.exists()
            or self.instance.incoming_movements.exists()
        ):
            self.fields["kind"].disabled = True
            history_note = "Hareket geçmişi bulunduğu için bu alan değiştirilemez."
            self.fields["kind"].help_text = history_note
        self.apply_styles()

    def clean(self):
        cleaned_data = super().clean()
        if not self.instance.pk:
            return cleaned_data

        from .services import stock_for

        current_stock = stock_for(self.instance)
        capacity = cleaned_data.get("capacity_liters")
        if capacity is not None and capacity < current_stock:
            self.add_error(
                "capacity_liters",
                f"Kapasite güncel stoktan ({current_stock:,.3f} L) düşük olamaz.",
            )
        if cleaned_data.get("is_active") is False and current_stock != Decimal("0"):
            self.add_error(
                "is_active",
                f"Stoğu sıfır olmayan birim pasife alınamaz. Güncel stok {current_stock:,.3f} L.",
            )
        return cleaned_data


class StorageRegionChangeForm(StyledFormMixin, forms.Form):
    occurred_at = forms.DateTimeField(
        label="Değişiklik tarihi",
        widget=DateTimeLocalInput(format="%Y-%m-%dT%H:%M"),
    )
    to_region = forms.ModelChoiceField(
        label="Yeni bölge",
        queryset=Region.objects.none(),
        empty_label="Yeni bölgeyi seçin",
    )
    reason = forms.CharField(
        label="Değişiklik gerekçesi",
        min_length=3,
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    def __init__(self, *args, storage, **kwargs):
        super().__init__(*args, **kwargs)
        self.storage = storage
        self.fields["to_region"].queryset = Region.active.exclude(pk=storage.region_id)
        if not self.is_bound:
            self.fields["occurred_at"].initial = timezone.localtime().replace(
                second=0, microsecond=0
            )
        self.apply_styles()

    def save_for_user(self, user):
        return change_storage_region(
            user=user,
            storage=self.storage,
            to_region=self.cleaned_data["to_region"],
            occurred_at=self.cleaned_data["occurred_at"],
            reason=self.cleaned_data["reason"],
        )


class VehicleForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Vehicle
        fields = [
            "name",
            "code",
            "tag_code",
            "region",
            "is_contractor",
            "meter_type",
            "organization",
            "is_active",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        region_filter = Q(is_active=True)
        if self.instance.pk:
            region_filter |= Q(pk=self.instance.region_id)
        self.fields["region"].queryset = Region.objects.filter(region_filter)
        self.fields["region"].empty_label = "Bölge seçin"
        self.fields["organization"].label = "Açıklama"
        self.fields["organization"].help_text = (
            "Araca ilişkin kurum, birim, görev veya unvan bilgisini yazabilirsiniz."
        )
        self.fields["is_contractor"].help_text = (
            "Taşeron araçlarda dolum sırasında kilometre veya çalışma saati girilmez."
        )
        if self.instance.pk:
            self.fields["region"].disabled = True
            self.fields["region"].help_text = (
                "Bölge değişiklikleri listeden ayrı ve izlenebilir bir işlemle yapılır."
            )
        if self.instance.pk and self.instance.fuel_movements.exists():
            self.fields["meter_type"].disabled = True
            history_note = "Yakıt hareketi bulunduğu için bu alan değiştirilemez."
            self.fields["meter_type"].help_text = history_note
        self.apply_styles()

    def clean_tag_code(self):
        return (self.cleaned_data.get("tag_code") or "").strip() or None


class VehicleImportForm(StyledFormMixin, forms.Form):
    workbook = forms.FileField(
        label="Excel dosyası",
        help_text=(
            "Sütunlar Plaka, Araç Anahtarı, Açıklama, Makina Kodu ve "
            "saat / km / taseron olmalıdır. Son sütundaki taseron değeri araç "
            "sınıfını belirler."
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_styles()

    def clean_workbook(self):
        workbook = self.cleaned_data["workbook"]
        if not workbook.name.lower().endswith(".xlsx"):
            raise forms.ValidationError("Yalnızca .xlsx uzantılı Excel dosyaları yüklenebilir.")
        if workbook.size > 10 * 1024 * 1024:
            raise forms.ValidationError("Excel dosyası 10 MB'tan büyük olamaz.")
        return workbook


class VehicleRegionChangeForm(StyledFormMixin, forms.Form):
    occurred_at = forms.DateTimeField(
        label="Değişiklik tarihi",
        widget=DateTimeLocalInput(format="%Y-%m-%dT%H:%M"),
    )
    to_region = forms.ModelChoiceField(
        label="Yeni bölge",
        queryset=Region.objects.none(),
        empty_label="Yeni bölgeyi seçin",
    )
    reason = forms.CharField(
        label="Değişiklik gerekçesi",
        min_length=3,
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    def __init__(self, *args, vehicle, **kwargs):
        super().__init__(*args, **kwargs)
        self.vehicle = vehicle
        self.fields["to_region"].queryset = Region.active.exclude(pk=vehicle.region_id)
        if not self.is_bound:
            self.fields["occurred_at"].initial = timezone.localtime().replace(
                second=0, microsecond=0
            )
        self.apply_styles()

    def save_for_user(self, user):
        return change_vehicle_region(
            user=user,
            vehicle=self.vehicle,
            to_region=self.cleaned_data["to_region"],
            occurred_at=self.cleaned_data["occurred_at"],
            reason=self.cleaned_data["reason"],
        )
