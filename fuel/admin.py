from django.contrib import admin

from .forms import RegionForm

from .models import (
    FuelMovement,
    Region,
    StorageRegionChange,
    StorageUnit,
    Supplier,
    Vehicle,
    VehicleRegionChange,
)


@admin.register(Region)
class RegionAdmin(admin.ModelAdmin):
    form = RegionForm
    list_display = ("name", "code", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "code")


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name",)


@admin.register(StorageUnit)
class StorageUnitAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "kind", "region", "capacity_liters", "is_active")
    list_filter = ("kind", "region", "is_active")
    search_fields = ("name", "code")

    def get_readonly_fields(self, request, obj=None):
        return ("region",) if obj else ()


@admin.register(StorageRegionChange)
class StorageRegionChangeAdmin(admin.ModelAdmin):
    list_display = (
        "occurred_at",
        "storage",
        "from_region",
        "to_region",
        "created_by",
    )
    list_filter = ("from_region", "to_region", "occurred_at")
    search_fields = ("storage__name", "storage__code", "reason")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Vehicle)
class VehicleAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "tag_code",
        "name",
        "region",
        "is_contractor",
        "meter_type",
        "organization",
        "is_active",
    )
    list_filter = ("is_contractor", "meter_type", "region", "is_active")
    search_fields = ("code", "tag_code", "name", "organization")

    def get_readonly_fields(self, request, obj=None):
        return ("region",) if obj else ()


@admin.register(VehicleRegionChange)
class VehicleRegionChangeAdmin(admin.ModelAdmin):
    list_display = (
        "occurred_at",
        "vehicle",
        "from_region",
        "to_region",
        "created_by",
    )
    list_filter = ("from_region", "to_region", "occurred_at")
    search_fields = ("vehicle__name", "vehicle__code", "reason")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(FuelMovement)
class FuelMovementAdmin(admin.ModelAdmin):
    list_display = (
        "occurred_at",
        "movement_type",
        "source_storage",
        "destination_storage",
        "vehicle",
        "liters",
        "created_by",
        "is_voided",
    )
    list_filter = ("movement_type", "occurred_at", "voided_at")
    search_fields = (
        "delivery_note",
        "source_storage__name",
        "destination_storage__name",
        "vehicle__code",
        "vehicle__name",
    )
    date_hierarchy = "occurred_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
