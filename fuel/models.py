from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


class ActiveManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(is_active=True)


class Region(models.Model):
    name = models.CharField("Bölge adı", max_length=120, unique=True)
    code = models.CharField("Kısa kod", max_length=30, unique=True)
    is_active = models.BooleanField("Aktif", default=True)

    objects = models.Manager()
    active = ActiveManager()

    class Meta:
        ordering = ["name"]
        verbose_name = "Bölge"
        verbose_name_plural = "Bölgeler"

    def __str__(self):
        return self.name


class Supplier(models.Model):
    name = models.CharField("Tedarikçi adı", max_length=160, unique=True)
    is_active = models.BooleanField("Aktif", default=True)

    objects = models.Manager()
    active = ActiveManager()

    class Meta:
        ordering = ["name"]
        verbose_name = "Tedarikçi"
        verbose_name_plural = "Tedarikçiler"

    def __str__(self):
        return self.name


class StorageUnit(models.Model):
    class Kind(models.TextChoices):
        FIXED_TANK = "FIXED_TANK", "Sabit tank"
        MOBILE_TANKER = "MOBILE_TANKER", "Mobil tanker"

    name = models.CharField("Ad", max_length=160)
    code = models.CharField("Kod", max_length=50, unique=True)
    kind = models.CharField("Tür", max_length=20, choices=Kind.choices)
    region = models.ForeignKey(
        Region,
        verbose_name="Bölge",
        on_delete=models.PROTECT,
        related_name="storage_units",
    )
    capacity_liters = models.DecimalField(
        "Kapasite (L)", max_digits=14, decimal_places=3, null=True, blank=True
    )
    low_stock_threshold = models.DecimalField(
        "Düşük stok eşiği (L)",
        max_digits=14,
        decimal_places=3,
        default=Decimal("0"),
    )
    is_active = models.BooleanField("Aktif", default=True)

    objects = models.Manager()
    active = ActiveManager()

    class Meta:
        ordering = ["region_id", "kind", "name"]
        verbose_name = "Depolama birimi"
        verbose_name_plural = "Depolama birimleri"
        constraints = [
            models.CheckConstraint(
                condition=Q(capacity_liters__isnull=True) | Q(capacity_liters__gt=0),
                name="storage_capacity_positive",
            ),
            models.CheckConstraint(
                condition=Q(low_stock_threshold__gte=0),
                name="storage_low_threshold_nonnegative",
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.get_kind_display()})"


class StorageRegionChange(models.Model):
    storage = models.ForeignKey(
        StorageUnit,
        verbose_name="Tank/tanker",
        on_delete=models.PROTECT,
        related_name="region_changes",
    )
    from_region = models.ForeignKey(
        Region,
        verbose_name="Eski bölge",
        on_delete=models.PROTECT,
        related_name="storage_region_departures",
    )
    to_region = models.ForeignKey(
        Region,
        verbose_name="Yeni bölge",
        on_delete=models.PROTECT,
        related_name="storage_region_arrivals",
    )
    occurred_at = models.DateTimeField("Değişiklik tarihi")
    reason = models.TextField("Değişiklik gerekçesi")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Kaydı oluşturan",
        on_delete=models.PROTECT,
        related_name="created_storage_region_changes",
    )
    created_at = models.DateTimeField("Kayıt zamanı", auto_now_add=True)

    class Meta:
        ordering = ["-occurred_at", "-id"]
        verbose_name = "Tank/tanker bölge değişikliği"
        verbose_name_plural = "Tank/tanker bölge değişiklikleri"
        constraints = [
            models.CheckConstraint(
                condition=~Q(from_region=models.F("to_region")),
                name="storage_region_change_regions_differ",
            )
        ]
        indexes = [models.Index(fields=["storage", "occurred_at"])]

    def clean(self):
        super().clean()
        if self.from_region_id == self.to_region_id:
            raise ValidationError({"to_region": "Yeni bölge mevcut bölgeden farklı olmalıdır."})

    def __str__(self):
        return f"{self.storage.code}: {self.from_region} → {self.to_region}"


class Vehicle(models.Model):
    class MeterType(models.TextChoices):
        KM = "KM", "Kilometre"
        HOUR = "HOUR", "Çalışma saati"
        NONE = "NONE", "Sayaç yok"

    name = models.CharField("Araç/makine adı", max_length=160)
    code = models.CharField("Araç kodu/plaka", max_length=60, unique=True)
    tag_code = models.CharField(
        "Araç anahtarı / RFID kodu",
        max_length=20,
        unique=True,
        blank=True,
        null=True,
    )
    region = models.ForeignKey(
        Region,
        verbose_name="Bölge",
        on_delete=models.PROTECT,
        related_name="vehicles",
    )
    meter_type = models.CharField(
        "Sayaç türü",
        max_length=10,
        choices=MeterType.choices,
        default=MeterType.NONE,
    )
    is_contractor = models.BooleanField("Taşeron araç/makine", default=False)
    organization = models.CharField(
        "Bağlı firma/taşeron", max_length=160, blank=True
    )
    is_active = models.BooleanField("Aktif", default=True)

    objects = models.Manager()
    active = ActiveManager()

    class Meta:
        ordering = ["code"]
        verbose_name = "Araç/makine"
        verbose_name_plural = "Araçlar/makineler"

    def __str__(self):
        return f"{self.code} — {self.name}"


class VehicleRegionChange(models.Model):
    vehicle = models.ForeignKey(
        Vehicle,
        verbose_name="Araç/makine",
        on_delete=models.PROTECT,
        related_name="region_changes",
    )
    from_region = models.ForeignKey(
        Region,
        verbose_name="Eski bölge",
        on_delete=models.PROTECT,
        related_name="vehicle_region_departures",
    )
    to_region = models.ForeignKey(
        Region,
        verbose_name="Yeni bölge",
        on_delete=models.PROTECT,
        related_name="vehicle_region_arrivals",
    )
    occurred_at = models.DateTimeField("Değişiklik tarihi")
    reason = models.TextField("Değişiklik gerekçesi")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Kaydı oluşturan",
        on_delete=models.PROTECT,
        related_name="created_vehicle_region_changes",
    )
    created_at = models.DateTimeField("Kayıt zamanı", auto_now_add=True)

    class Meta:
        ordering = ["-occurred_at", "-id"]
        verbose_name = "Araç/makine bölge değişikliği"
        verbose_name_plural = "Araç/makine bölge değişiklikleri"
        constraints = [
            models.CheckConstraint(
                condition=~Q(from_region=models.F("to_region")),
                name="vehicle_region_change_regions_differ",
            )
        ]
        indexes = [models.Index(fields=["vehicle", "occurred_at"])]

    def clean(self):
        super().clean()
        if self.from_region_id == self.to_region_id:
            raise ValidationError({"to_region": "Yeni bölge mevcut bölgeden farklı olmalıdır."})

    def __str__(self):
        return f"{self.vehicle.code}: {self.from_region} → {self.to_region}"


class ContractorFuelPrice(models.Model):
    effective_from = models.DateTimeField("Geçerlilik başlangıcı", db_index=True)
    reference_unit_price = models.DecimalField(
        "Referans ortalama fiyat (TL/L)", max_digits=12, decimal_places=4
    )
    contractor_unit_price = models.DecimalField(
        "Taşerona yansıtılacak fiyat (TL/L)", max_digits=12, decimal_places=4
    )
    note = models.TextField("Açıklama", blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Kaydı oluşturan",
        on_delete=models.PROTECT,
        related_name="created_contractor_fuel_prices",
    )
    created_at = models.DateTimeField("Kayıt zamanı", auto_now_add=True)

    class Meta:
        ordering = ["-effective_from", "-id"]
        verbose_name = "Taşeron yakıt fiyatı"
        verbose_name_plural = "Taşeron yakıt fiyatları"
        constraints = [
            models.CheckConstraint(
                condition=Q(reference_unit_price__gt=0),
                name="contractor_price_reference_positive",
            ),
            models.CheckConstraint(
                condition=Q(contractor_unit_price__gt=0),
                name="contractor_price_charge_positive",
            ),
        ]

    @property
    def unit_difference(self):
        return self.contractor_unit_price - self.reference_unit_price

    def clean(self):
        super().clean()
        errors = {}
        if self.reference_unit_price is not None and self.reference_unit_price <= 0:
            errors["reference_unit_price"] = "Referans ortalama fiyat sıfırdan büyük olmalıdır."
        if self.contractor_unit_price is not None and self.contractor_unit_price <= 0:
            errors["contractor_unit_price"] = "Taşerona yansıtılacak fiyat sıfırdan büyük olmalıdır."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return (
            f"{self.effective_from:%d.%m.%Y %H:%M} — "
            f"{self.contractor_unit_price} TL/L"
        )


class FuelMovementQuerySet(models.QuerySet):
    def active(self):
        return self.filter(voided_at__isnull=True)


class FuelMovement(models.Model):
    class MovementType(models.TextChoices):
        OPENING_BALANCE = "OPENING_BALANCE", "Başlangıç stoğu"
        SUPPLIER_RECEIPT = "SUPPLIER_RECEIPT", "Tedarikçiden giriş"
        INTERNAL_TRANSFER = "INTERNAL_TRANSFER", "İç transfer"
        VEHICLE_FUELING = "VEHICLE_FUELING", "Araca yakıt verme"
        STOCK_ADJUSTMENT = "STOCK_ADJUSTMENT", "Stok düzeltmesi"

    movement_type = models.CharField(
        "Hareket türü", max_length=24, choices=MovementType.choices
    )
    occurred_at = models.DateTimeField("İşlem tarihi")
    source_storage = models.ForeignKey(
        StorageUnit,
        verbose_name="Kaynak tank/tanker",
        on_delete=models.PROTECT,
        related_name="outgoing_movements",
        null=True,
        blank=True,
    )
    destination_storage = models.ForeignKey(
        StorageUnit,
        verbose_name="Hedef tank/tanker",
        on_delete=models.PROTECT,
        related_name="incoming_movements",
        null=True,
        blank=True,
    )
    source_region = models.ForeignKey(
        Region,
        verbose_name="Kaynak işlem bölgesi",
        on_delete=models.PROTECT,
        related_name="source_fuel_movements",
        null=True,
        blank=True,
    )
    destination_region = models.ForeignKey(
        Region,
        verbose_name="Hedef işlem bölgesi",
        on_delete=models.PROTECT,
        related_name="destination_fuel_movements",
        null=True,
        blank=True,
    )
    supplier = models.ForeignKey(
        Supplier,
        verbose_name="Tedarikçi",
        on_delete=models.PROTECT,
        related_name="fuel_movements",
        null=True,
        blank=True,
    )
    vehicle = models.ForeignKey(
        Vehicle,
        verbose_name="Araç/makine",
        on_delete=models.PROTECT,
        related_name="fuel_movements",
        null=True,
        blank=True,
    )
    vehicle_region = models.ForeignKey(
        Region,
        verbose_name="Araç işlem bölgesi",
        on_delete=models.PROTECT,
        related_name="vehicle_fuel_movements",
        null=True,
        blank=True,
    )
    liters = models.DecimalField("Miktar (L)", max_digits=14, decimal_places=3)
    delivery_note = models.CharField("İrsaliye no", max_length=100, blank=True)
    external_scale_kg = models.DecimalField(
        "Tedarikçi kantarı (kg)",
        max_digits=14,
        decimal_places=3,
        null=True,
        blank=True,
    )
    company_scale_kg = models.DecimalField(
        "Kurum kantarı (kg)",
        max_digits=14,
        decimal_places=3,
        null=True,
        blank=True,
    )
    meter_value = models.DecimalField(
        "Kilometre/çalışma saati",
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
    )
    purchase_unit_price = models.DecimalField(
        "Alış birim fiyatı (TL/L)",
        max_digits=12,
        decimal_places=4,
        null=True,
        blank=True,
    )
    contractor_reference_unit_price = models.DecimalField(
        "Taşeron referans ortalama fiyatı (TL/L)",
        max_digits=12,
        decimal_places=4,
        null=True,
        blank=True,
    )
    contractor_unit_price = models.DecimalField(
        "Taşerona yansıtılan fiyat (TL/L)",
        max_digits=12,
        decimal_places=4,
        null=True,
        blank=True,
    )
    note = models.TextField("Açıklama", blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Kaydı oluşturan",
        on_delete=models.PROTECT,
        related_name="created_fuel_movements",
    )
    corrects = models.OneToOneField(
        "self",
        verbose_name="Düzeltilen hareket",
        on_delete=models.PROTECT,
        related_name="correction",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField("Kayıt zamanı", auto_now_add=True)
    voided_at = models.DateTimeField("İptal zamanı", null=True, blank=True)
    voided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="İptal eden",
        on_delete=models.PROTECT,
        related_name="voided_fuel_movements",
        null=True,
        blank=True,
    )
    void_reason = models.TextField("İptal gerekçesi", blank=True)

    objects = FuelMovementQuerySet.as_manager()

    class Meta:
        ordering = ["-occurred_at", "-id"]
        verbose_name = "Yakıt hareketi"
        verbose_name_plural = "Yakıt hareketleri"
        constraints = [
            models.CheckConstraint(
                condition=Q(liters__gt=0), name="movement_liters_positive"
            ),
            models.CheckConstraint(
                condition=~Q(source_storage=models.F("destination_storage")),
                name="movement_source_differs_from_destination",
            ),
            models.CheckConstraint(
                condition=Q(external_scale_kg__isnull=True)
                | Q(external_scale_kg__gte=0),
                name="movement_external_scale_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(company_scale_kg__isnull=True)
                | Q(company_scale_kg__gte=0),
                name="movement_company_scale_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(meter_value__isnull=True) | Q(meter_value__gte=0),
                name="movement_meter_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(purchase_unit_price__isnull=True)
                | Q(purchase_unit_price__gt=0),
                name="movement_purchase_price_positive",
            ),
            models.CheckConstraint(
                condition=Q(contractor_reference_unit_price__isnull=True)
                | Q(contractor_reference_unit_price__gt=0),
                name="movement_contractor_reference_price_positive",
            ),
            models.CheckConstraint(
                condition=Q(contractor_unit_price__isnull=True)
                | Q(contractor_unit_price__gt=0),
                name="movement_contractor_price_positive",
            ),
            models.CheckConstraint(
                condition=(
                    Q(source_storage__isnull=True, source_region__isnull=True)
                    | Q(source_storage__isnull=False, source_region__isnull=False)
                ),
                name="movement_source_region_matches_storage",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        destination_storage__isnull=True,
                        destination_region__isnull=True,
                    )
                    | Q(
                        destination_storage__isnull=False,
                        destination_region__isnull=False,
                    )
                ),
                name="movement_destination_region_matches_storage",
            ),
            models.CheckConstraint(
                condition=(
                    Q(vehicle__isnull=True, vehicle_region__isnull=True)
                    | Q(vehicle__isnull=False, vehicle_region__isnull=False)
                ),
                name="movement_vehicle_region_matches_vehicle",
            ),
        ]
        indexes = [
            models.Index(fields=["occurred_at", "movement_type"]),
            models.Index(fields=["vehicle", "occurred_at"]),
            models.Index(fields=["source_storage", "occurred_at"]),
            models.Index(fields=["destination_storage", "occurred_at"]),
        ]

    @property
    def is_voided(self):
        return self.voided_at is not None

    @property
    def purchase_total(self):
        if self.purchase_unit_price is None:
            return None
        return self.liters * self.purchase_unit_price

    @property
    def contractor_unit_difference(self):
        if (
            self.contractor_reference_unit_price is None
            or self.contractor_unit_price is None
        ):
            return None
        return self.contractor_unit_price - self.contractor_reference_unit_price

    @property
    def contractor_reference_total(self):
        if self.contractor_reference_unit_price is None:
            return None
        return self.liters * self.contractor_reference_unit_price

    @property
    def contractor_charged_total(self):
        if self.contractor_unit_price is None:
            return None
        return self.liters * self.contractor_unit_price

    @property
    def contractor_difference_total(self):
        unit_difference = self.contractor_unit_difference
        if unit_difference is None:
            return None
        return self.liters * unit_difference

    def clean(self):
        super().clean()
        errors = {}

        if self.source_storage_id and self.source_storage_id == self.destination_storage_id:
            errors["destination_storage"] = "Kaynak ve hedef aynı olamaz."

        if self.corrects_id and self.corrects.movement_type != self.movement_type:
            errors["corrects"] = "Düzeltme kaydı, eski hareketle aynı türde olmalıdır."

        if self.movement_type == self.MovementType.OPENING_BALANCE:
            if not self.destination_storage_id:
                errors["destination_storage"] = "Başlangıç stoğu için hedef gereklidir."
            if self.source_storage_id or self.supplier_id or self.vehicle_id:
                errors["movement_type"] = "Başlangıç stoğunda kaynak, tedarikçi veya araç kullanılamaz."

        elif self.movement_type == self.MovementType.SUPPLIER_RECEIPT:
            if not self.destination_storage_id:
                errors["destination_storage"] = "Yakıtın girdiği sabit tank gereklidir."
            elif self.destination_storage.kind != StorageUnit.Kind.FIXED_TANK:
                errors["destination_storage"] = "Tedarikçi girişi yalnızca sabit tanka yapılabilir."
            if not self.supplier_id:
                errors["supplier"] = "Tedarikçi seçilmelidir."
            if self.source_storage_id or self.vehicle_id:
                errors["movement_type"] = "Tedarikçi girişinde kaynak tank veya araç kullanılamaz."
            if self._state.adding and self.purchase_unit_price is None:
                errors["purchase_unit_price"] = "Alış birim fiyatı girilmelidir."
            if self.contractor_reference_unit_price is not None or self.contractor_unit_price is not None:
                errors["movement_type"] = "Tedarikçi girişinde taşeron fiyatı kullanılamaz."

        elif self.movement_type == self.MovementType.INTERNAL_TRANSFER:
            if not self.source_storage_id:
                errors["source_storage"] = "Kaynak sabit tank gereklidir."
            elif self.source_storage.kind != StorageUnit.Kind.FIXED_TANK:
                errors["source_storage"] = "Transfer kaynağı sabit tank olmalıdır."
            if not self.destination_storage_id:
                errors["destination_storage"] = "Hedef mobil tanker gereklidir."
            elif self.destination_storage.kind != StorageUnit.Kind.MOBILE_TANKER:
                errors["destination_storage"] = "Transfer hedefi mobil tanker olmalıdır."
            if self.supplier_id or self.vehicle_id:
                errors["movement_type"] = "İç transferde tedarikçi veya araç kullanılamaz."

        elif self.movement_type == self.MovementType.VEHICLE_FUELING:
            if not self.source_storage_id:
                errors["source_storage"] = "Yakıtı veren tank veya tanker gereklidir."
            if not self.vehicle_id:
                errors["vehicle"] = "Araç/makine seçilmelidir."
            elif self.vehicle.is_contractor:
                if self.meter_value is not None:
                    errors["meter_value"] = "Taşeron araçlarda sayaç değeri girilmez."
                if self._state.adding and self.contractor_reference_unit_price is None:
                    errors["contractor_reference_unit_price"] = (
                        "Taşeron referans ortalama fiyatı girilmelidir."
                    )
                if self._state.adding and self.contractor_unit_price is None:
                    errors["contractor_unit_price"] = (
                        "Taşerona yansıtılacak fiyat girilmelidir."
                    )
            elif self.vehicle.meter_type in (
                Vehicle.MeterType.KM,
                Vehicle.MeterType.HOUR,
            ):
                if self.meter_value is None:
                    errors["meter_value"] = (
                        f"Bu araç için {self.vehicle.get_meter_type_display().lower()} "
                        "girilmelidir."
                    )
            elif self.meter_value is not None:
                errors["meter_value"] = "Bu araçta sayaç kullanılmıyor."
            if self.destination_storage_id or self.supplier_id:
                errors["movement_type"] = "Araç dolumunda hedef tank veya tedarikçi kullanılamaz."
            if self.purchase_unit_price is not None:
                errors["purchase_unit_price"] = "Araç dolumunda alış fiyatı kullanılamaz."
            if self.vehicle_id and not self.vehicle.is_contractor and (
                self.contractor_reference_unit_price is not None
                or self.contractor_unit_price is not None
            ):
                errors["movement_type"] = "Kurum araçlarında taşeron fiyatı kullanılamaz."

        elif self.movement_type == self.MovementType.STOCK_ADJUSTMENT:
            if bool(self.source_storage_id) == bool(self.destination_storage_id):
                errors["movement_type"] = "Stok düzeltmesinde artış hedefi veya azalış kaynağından yalnızca biri seçilmelidir."
            if not self.note.strip():
                errors["note"] = "Stok düzeltmesi için gerekçe zorunludur."
            if self.supplier_id or self.vehicle_id:
                errors["movement_type"] = "Stok düzeltmesinde tedarikçi veya araç kullanılamaz."

        if self.purchase_unit_price is not None and self.purchase_unit_price <= 0:
            errors["purchase_unit_price"] = "Alış birim fiyatı sıfırdan büyük olmalıdır."
        if (
            self.contractor_reference_unit_price is not None
            and self.contractor_reference_unit_price <= 0
        ):
            errors["contractor_reference_unit_price"] = (
                "Taşeron referans ortalama fiyatı sıfırdan büyük olmalıdır."
            )
        if self.contractor_unit_price is not None and self.contractor_unit_price <= 0:
            errors["contractor_unit_price"] = (
                "Taşerona yansıtılacak fiyat sıfırdan büyük olmalıdır."
            )
        if bool(self.contractor_reference_unit_price is None) != bool(
            self.contractor_unit_price is None
        ):
            errors["contractor_reference_unit_price"] = (
                "Taşeron referans ve yansıtma fiyatı birlikte girilmelidir."
            )
        if self.movement_type not in (
            self.MovementType.SUPPLIER_RECEIPT,
            self.MovementType.VEHICLE_FUELING,
        ) and (
            self.purchase_unit_price is not None
            or self.contractor_reference_unit_price is not None
            or self.contractor_unit_price is not None
        ):
            errors["movement_type"] = "Bu hareket türünde fiyat bilgisi kullanılamaz."

        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.get_movement_type_display()} — {self.liters} L"


class OperatorStorageAccess(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Saha operatörü",
        on_delete=models.CASCADE,
        related_name="operator_storage_accesses",
    )
    storage = models.ForeignKey(
        StorageUnit,
        verbose_name="Yetkili tank/tanker",
        on_delete=models.CASCADE,
        related_name="operator_accesses",
    )

    class Meta:
        ordering = ["user__username", "storage__region__name", "storage__name"]
        verbose_name = "Operatör tank/tanker yetkisi"
        verbose_name_plural = "Operatör tank/tanker yetkileri"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "storage"],
                name="operator_storage_access_unique",
            )
        ]

    def __str__(self):
        return f"{self.user.get_username()} — {self.storage}"


class FuelingRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Onay bekliyor"
        APPROVED = "APPROVED", "Onaylandı"
        REJECTED = "REJECTED", "Reddedildi"

    occurred_at = models.DateTimeField("Yakıt verme tarihi")
    source_storage = models.ForeignKey(
        StorageUnit,
        verbose_name="Yakıtı veren tank/tanker",
        on_delete=models.PROTECT,
        related_name="fueling_requests",
    )
    source_region = models.ForeignKey(
        Region,
        verbose_name="Kaynak işlem bölgesi",
        on_delete=models.PROTECT,
        related_name="source_fueling_requests",
    )
    vehicle = models.ForeignKey(
        Vehicle,
        verbose_name="Araç/makine",
        on_delete=models.PROTECT,
        related_name="fueling_requests",
    )
    vehicle_region = models.ForeignKey(
        Region,
        verbose_name="Araç işlem bölgesi",
        on_delete=models.PROTECT,
        related_name="vehicle_fueling_requests",
    )
    liters = models.DecimalField("Miktar (L)", max_digits=14, decimal_places=3)
    meter_value = models.DecimalField(
        "Kilometre/çalışma saati",
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
    )
    contractor_reference_unit_price = models.DecimalField(
        "Taşeron referans ortalama fiyatı (TL/L)",
        max_digits=12,
        decimal_places=4,
        null=True,
        blank=True,
    )
    contractor_unit_price = models.DecimalField(
        "Taşerona yansıtılacak fiyat (TL/L)",
        max_digits=12,
        decimal_places=4,
        null=True,
        blank=True,
    )
    note = models.TextField("Açıklama", blank=True)
    status = models.CharField(
        "Durum",
        max_length=12,
        choices=Status.choices,
        default=Status.PENDING,
    )
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Gönderen operatör",
        on_delete=models.PROTECT,
        related_name="submitted_fueling_requests",
    )
    submitted_at = models.DateTimeField("Gönderim zamanı", auto_now_add=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Karar veren",
        on_delete=models.PROTECT,
        related_name="decided_fueling_requests",
        null=True,
        blank=True,
    )
    decided_at = models.DateTimeField("Karar zamanı", null=True, blank=True)
    decision_reason = models.TextField("Karar açıklaması", blank=True)
    movement = models.OneToOneField(
        FuelMovement,
        verbose_name="Oluşan yakıt hareketi",
        on_delete=models.PROTECT,
        related_name="source_request",
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["-submitted_at", "-id"]
        verbose_name = "Saha yakıt kaydı"
        verbose_name_plural = "Saha yakıt kayıtları"
        permissions = [
            ("approve_fuelingrequest", "Saha yakıt kayıtlarını onaylayabilir"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(liters__gt=0),
                name="fueling_request_liters_positive",
            ),
            models.CheckConstraint(
                condition=Q(meter_value__isnull=True) | Q(meter_value__gte=0),
                name="fueling_request_meter_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(contractor_reference_unit_price__isnull=True)
                | Q(contractor_reference_unit_price__gt=0),
                name="fueling_request_reference_price_positive",
            ),
            models.CheckConstraint(
                condition=Q(contractor_unit_price__isnull=True)
                | Q(contractor_unit_price__gt=0),
                name="fueling_request_contractor_price_positive",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "submitted_at"]),
            models.Index(fields=["submitted_by", "status"]),
            models.Index(fields=["source_region", "status"]),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.vehicle_id and self.vehicle.is_contractor:
            if self.meter_value is not None:
                errors["meter_value"] = "Taşeron araçlarda sayaç değeri girilmez."
            if self._state.adding and self.contractor_reference_unit_price is None:
                errors["contractor_reference_unit_price"] = (
                    "Taşeron referans ortalama fiyatı girilmelidir."
                )
            if self._state.adding and self.contractor_unit_price is None:
                errors["contractor_unit_price"] = (
                    "Taşerona yansıtılacak fiyat girilmelidir."
                )
        elif self.vehicle_id and self.vehicle.meter_type in (
            Vehicle.MeterType.KM,
            Vehicle.MeterType.HOUR,
        ):
            if self.meter_value is None:
                errors["meter_value"] = (
                    f"Bu araç için {self.vehicle.get_meter_type_display().lower()} "
                    "girilmelidir."
                )
        elif self.vehicle_id and self.meter_value is not None:
            errors["meter_value"] = "Bu araçta sayaç kullanılmıyor."
        if self.vehicle_id and not self.vehicle.is_contractor and (
            self.contractor_reference_unit_price is not None
            or self.contractor_unit_price is not None
        ):
            errors["vehicle"] = "Kurum araçlarında taşeron fiyatı kullanılamaz."
        if (
            self.contractor_reference_unit_price is not None
            and self.contractor_reference_unit_price <= 0
        ):
            errors["contractor_reference_unit_price"] = (
                "Taşeron referans ortalama fiyatı sıfırdan büyük olmalıdır."
            )
        if self.contractor_unit_price is not None and self.contractor_unit_price <= 0:
            errors["contractor_unit_price"] = (
                "Taşerona yansıtılacak fiyat sıfırdan büyük olmalıdır."
            )
        if bool(self.contractor_reference_unit_price is None) != bool(
            self.contractor_unit_price is None
        ):
            errors["contractor_reference_unit_price"] = (
                "Taşeron referans ve yansıtma fiyatı birlikte girilmelidir."
            )
        if self.status == self.Status.PENDING:
            if self.decided_by_id or self.decided_at or self.movement_id:
                errors["status"] = "Bekleyen kayıt karar veya hareket bilgisi içeremez."
        elif self.status == self.Status.APPROVED:
            if not self.decided_by_id or not self.decided_at or not self.movement_id:
                errors["status"] = "Onaylanan kayıt için karar ve hareket bilgisi zorunludur."
        elif self.status == self.Status.REJECTED:
            if not self.decided_by_id or not self.decided_at:
                errors["status"] = "Reddedilen kayıt için karar bilgisi zorunludur."
            if not self.decision_reason.strip():
                errors["decision_reason"] = "Ret gerekçesi zorunludur."
            if self.movement_id:
                errors["movement"] = "Reddedilen kayıt yakıt hareketi oluşturamaz."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.vehicle.code} — {self.liters} L — {self.get_status_display()}"
