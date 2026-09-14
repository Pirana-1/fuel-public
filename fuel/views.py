from datetime import datetime, time, timedelta
from decimal import Decimal
from io import BytesIO

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, permission_required
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import connection
from django.db.models import Count, OuterRef, Q, Subquery, Sum
from django.forms.models import model_to_dict
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_GET, require_POST
from .forms import (
    ContractorFuelPriceForm,
    InternalTransferForm,
    MovementVoidForm,
    OpeningBalanceForm,
    OperatorFuelingRequestForm,
    RejectFuelingRequestForm,
    RegionForm,
    StorageRegionChangeForm,
    StorageUnitForm,
    SupplierForm,
    SupplierReceiptForm,
    StockAdjustmentForm,
    VehicleForm,
    VehicleFuelingForm,
    VehicleImportForm,
    VehicleRegionChangeForm,
)
from .correction_forms import (
    MovementCorrectionReasonForm,
    form_class_for_movement,
    include_original_choices,
    initial_for_movement,
)
from .correction_service import correct_movement
from .models import (
    ContractorFuelPrice,
    FuelMovement,
    FuelingRequest,
    Region,
    StorageUnit,
    Supplier,
    Vehicle,
)
from .report_excel import build_report_workbook, report_filename
from .services import (
    ZERO,
    attach_balances,
    approve_fueling_request,
    contractor_price_at,
    contractor_pricing_totals,
    daily_activity_summary,
    negative_stock_warning_for,
    period_totals,
    operator_storages_for,
    reject_fueling_request,
    region_activity_summary,
    storage_activity_summary,
    storage_region_at,
    stock_by_storage,
    stock_for,
    supplier_receipt_summary,
    vehicle_consumption_summary,
    void_movement,
)
from .vehicle_import import (
    assess_vehicle_rows,
    import_vehicle_rows,
    parse_vehicle_workbook,
)


def _aware_boundary(value, *, end=False):
    parsed = parse_date(value) if value else None
    if parsed is None:
        return None
    boundary = datetime.combine(parsed, time.min)
    if end:
        boundary += timedelta(days=1)
    return timezone.make_aware(boundary)


def _default_period():
    today = timezone.localdate()
    return today.replace(day=1), today


QUICK_PERIOD_DAYS = {
    "today": 1,
    "7d": 7,
    "30d": 30,
}


def _period_values(params, *, use_default_period=False):
    today = timezone.localdate()
    quick_period = params.get("period", "")
    days = QUICK_PERIOD_DAYS.get(quick_period)
    if days:
        start = today - timedelta(days=days - 1)
        return start.isoformat(), today.isoformat(), quick_period

    default_start, default_end = _default_period()
    start_value = params.get("start") or (
        default_start.isoformat() if use_default_period else ""
    )
    end_value = params.get("end") or (
        default_end.isoformat() if use_default_period else ""
    )
    return start_value, end_value, ""


def _movement_filters(params, *, use_default_period=False, include_voided=False):
    queryset = FuelMovement.objects.all()
    if not include_voided:
        queryset = queryset.active()
    queryset = queryset.select_related(
        "source_storage__region",
        "destination_storage__region",
        "source_region",
        "destination_region",
        "supplier",
        "vehicle__region",
        "vehicle_region",
        "created_by",
        "voided_by",
        "corrects",
        "correction",
    )

    start_value, end_value, quick_period = _period_values(
        params, use_default_period=use_default_period
    )
    start_at = _aware_boundary(start_value)
    end_at = _aware_boundary(end_value, end=True)

    if start_at:
        queryset = queryset.filter(occurred_at__gte=start_at)
    if end_at:
        queryset = queryset.filter(occurred_at__lt=end_at)

    movement_type = params.get("movement_type", "")
    region_id = params.get("region", "")
    storage_id = params.get("storage", "")
    vehicle_id = params.get("vehicle", "")
    meter_type = params.get("meter_type", "")
    vehicle_class = params.get("vehicle_class", "")
    status = params.get("status", "") if include_voided else ""

    if movement_type in FuelMovement.MovementType.values:
        queryset = queryset.filter(movement_type=movement_type)
    if region_id.isdigit():
        storage_region_filter = (
            Q(
                movement_type=FuelMovement.MovementType.OPENING_BALANCE,
                destination_region_id=region_id,
            )
            | Q(
                movement_type=FuelMovement.MovementType.SUPPLIER_RECEIPT,
                destination_region_id=region_id,
            )
            | Q(
                movement_type=FuelMovement.MovementType.INTERNAL_TRANSFER,
                source_region_id=region_id,
            )
            | Q(
                movement_type=FuelMovement.MovementType.INTERNAL_TRANSFER,
                destination_region_id=region_id,
            )
            | Q(
                movement_type=FuelMovement.MovementType.STOCK_ADJUSTMENT,
                source_region_id=region_id,
            )
            | Q(
                movement_type=FuelMovement.MovementType.STOCK_ADJUSTMENT,
                destination_region_id=region_id,
            )
        )
        vehicle_region_filter = Q(
            movement_type=FuelMovement.MovementType.VEHICLE_FUELING,
            vehicle_region_id=region_id,
        )
        queryset = queryset.filter(storage_region_filter | vehicle_region_filter)
    if storage_id.isdigit():
        queryset = queryset.filter(
            Q(source_storage_id=storage_id) | Q(destination_storage_id=storage_id)
        )
    if vehicle_id.isdigit():
        queryset = queryset.filter(vehicle_id=vehicle_id)
    if meter_type in Vehicle.MeterType.values:
        queryset = queryset.filter(vehicle__meter_type=meter_type)
    if vehicle_class == "contractor":
        queryset = queryset.filter(vehicle__is_contractor=True)
    elif vehicle_class == "in_house":
        queryset = queryset.filter(vehicle__is_contractor=False)
    else:
        vehicle_class = ""
    if status == "active":
        queryset = queryset.filter(voided_at__isnull=True)
    elif status == "voided":
        queryset = queryset.filter(voided_at__isnull=False)
    else:
        status = ""

    filter_keys = (
        "period",
        "start",
        "end",
        "movement_type",
        "region",
        "storage",
        "vehicle",
        "meter_type",
        "vehicle_class",
        "status",
    )
    return queryset, {
        "start": start_value,
        "end": end_value,
        "period": quick_period,
        "movement_type": movement_type,
        "region": region_id,
        "storage": storage_id,
        "vehicle": vehicle_id,
        "meter_type": meter_type,
        "vehicle_class": vehicle_class,
        "status": status,
        "has_active_filters": any(params.get(key, "").strip() for key in filter_keys),
    }


def _filter_options():
    return {
        "movement_types": FuelMovement.MovementType.choices,
        "regions": Region.objects.all(),
        "storages": StorageUnit.objects.select_related("region"),
        "vehicles": Vehicle.objects.select_related("region"),
        "meter_types": Vehicle.MeterType.choices,
        "vehicle_classes": (
            ("in_house", "Kurum aracı"),
            ("contractor", "Taşeron"),
        ),
    }


def _add_validation_errors(form, error):
    if hasattr(error, "error_dict"):
        for field_name, field_errors in error.error_dict.items():
            target = field_name if field_name in form.fields else None
            for field_error in field_errors:
                form.add_error(target, field_error)
    else:
        form.add_error(None, error)


@require_GET
def health(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        return JsonResponse({"status": "unhealthy"}, status=503)
    return JsonResponse({"status": "ok"})


def _dashboard_metrics(request):
    storages = list(StorageUnit.active.select_related("region"))
    balances = stock_by_storage(storages)
    storage_cards = attach_balances(storages, balances)
    fixed_stock = sum(
        (item.current_stock for item in storage_cards if item.kind == StorageUnit.Kind.FIXED_TANK),
        ZERO,
    )
    mobile_stock = sum(
        (item.current_stock for item in storage_cards if item.kind == StorageUnit.Kind.MOBILE_TANKER),
        ZERO,
    )
    period_queryset, period = _movement_filters(request.GET, use_default_period=True)
    totals = period_totals(period_queryset)
    return {
        "period": period,
        "period_queryset": period_queryset,
        "totals": totals,
        "fixed_stock": fixed_stock,
        "mobile_stock": mobile_stock,
        "total_stock": fixed_stock + mobile_stock,
        "storage_cards": storage_cards,
        "low_stock_count": sum(item.is_low_stock for item in storage_cards),
        "negative_stock_count": sum(
            item.is_negative_stock for item in storage_cards
        ),
    }


def _dashboard_live_context(request):
    metrics = _dashboard_metrics(request)
    period_queryset = metrics.pop("period_queryset")
    return {
        **metrics,
        "top_vehicles": vehicle_consumption_summary(period_queryset)[:6],
        "recent_movements": period_queryset.order_by("-occurred_at", "-id")[:8],
    }


@login_required
def dashboard(request):
    if not request.user.has_perm("fuel.view_fuelmovement"):
        if request.user.has_perm("fuel.add_fuelingrequest"):
            return redirect("operator-fueling-request")
        raise PermissionDenied
    return render(request, "fuel/dashboard.html", _dashboard_live_context(request))


@login_required
@permission_required("fuel.view_fuelmovement", raise_exception=True)
def dashboard_metrics(request):
    return render(
        request,
        "fuel/includes/dashboard_live.html",
        _dashboard_live_context(request),
    )


@login_required
@permission_required("fuel.view_fuelmovement", raise_exception=True)
def movement_list(request):
    queryset, filters = _movement_filters(request.GET, include_voided=True)
    page_size_options = (15, 30, 50, 100)
    try:
        page_size = int(request.GET.get("page_size", "30"))
    except (TypeError, ValueError):
        page_size = 30
    if page_size not in page_size_options:
        page_size = 30

    filters["page_size"] = str(page_size)
    if page_size != 30:
        filters["has_active_filters"] = True

    paginator = Paginator(queryset, page_size)
    page = paginator.get_page(request.GET.get("page"))
    context = {
        "page_obj": page,
        "filters": filters,
        "page_size_options": page_size_options,
        **_filter_options(),
    }
    return render(request, "fuel/movement_list.html", context)


def _movement_create(
    request,
    *,
    form_class,
    title,
    subtitle,
    accent,
    note_text=None,
    form_layout="",
    extra_context=None,
):
    if request.method == "POST":
        form = form_class(request.POST)
        if form.is_valid():
            try:
                movement = form.save_for_user(request.user)
            except ValidationError as error:
                _add_validation_errors(form, error)
            else:
                messages.success(
                    request,
                    f"{movement.get_movement_type_display()} başarıyla kaydedildi.",
                )
                warning = negative_stock_warning_for(movement)
                if warning:
                    messages.warning(request, warning)
                return redirect("movement-list")
    else:
        form = form_class()
    context = {
        "form": form,
        "title": title,
        "subtitle": subtitle,
        "accent": accent,
        "note_text": note_text,
        "form_layout": form_layout,
    }
    if extra_context:
        context.update(extra_context)
    return render(request, "fuel/movement_form.html", context)


def _storage_status_data(storages):
    storages = list(storages)
    balances = stock_by_storage(storages)
    status = {}
    for storage in storages:
        current_stock = balances[storage.pk]
        capacity = storage.capacity_liters
        status[str(storage.pk)] = {
            "current_stock": str(current_stock),
            "is_negative_stock": current_stock < ZERO,
            "capacity": str(capacity) if capacity is not None else None,
            "remaining": (
                str(max(ZERO, capacity - current_stock))
                if capacity is not None
                else None
            ),
        }
    return status


def _contractor_price_definitions_data():
    return [
        {
            "effective_from": item.effective_from.isoformat(),
            "reference_unit_price": str(item.reference_unit_price),
            "contractor_unit_price": str(item.contractor_unit_price),
        }
        for item in ContractorFuelPrice.objects.order_by("effective_from", "pk")
    ]


def _vehicle_meter_status_data():
    latest_meter = (
        FuelMovement.objects.active()
        .filter(vehicle_id=OuterRef("pk"), meter_value__isnull=False)
        .order_by("-occurred_at", "-pk")
        .values("meter_value")[:1]
    )
    vehicles = Vehicle.active.annotate(latest_meter_value=Subquery(latest_meter))
    units = {
        Vehicle.MeterType.KM: "km",
        Vehicle.MeterType.HOUR: "saat",
    }
    return {
        str(vehicle.pk): {
            "meter_type": vehicle.meter_type,
            "is_contractor": vehicle.is_contractor,
            "label": vehicle.get_meter_type_display(),
            "unit": units.get(vehicle.meter_type, ""),
            "last_value": (
                str(vehicle.latest_meter_value)
                if vehicle.latest_meter_value is not None
                else None
            ),
        }
        for vehicle in vehicles
    }


@login_required
@permission_required("fuel.add_fuelmovement", raise_exception=True)
def opening_balance_create(request):
    return _movement_create(
        request,
        form_class=OpeningBalanceForm,
        title="Başlangıç stoğu",
        subtitle="Sistem açılışında tank veya tankerde bulunan devir miktarını kaydedin.",
        accent="purple",
    )


@login_required
@permission_required("fuel.add_fuelmovement", raise_exception=True)
def supplier_receipt_create(request):
    return _movement_create(
        request,
        form_class=SupplierReceiptForm,
        title="Tedarikçiden yakıt girişi",
        subtitle="Tedarikçiden gelen motorini hedef sabit tanka ekleyin.",
        accent="green",
        form_layout="supplier-receipt-fields",
    )


@login_required
@permission_required("fuel.add_fuelmovement", raise_exception=True)
def internal_transfer_create(request):
    storages = StorageUnit.active.filter(
        kind__in=(StorageUnit.Kind.FIXED_TANK, StorageUnit.Kind.MOBILE_TANKER)
    )
    return _movement_create(
        request,
        form_class=InternalTransferForm,
        title="Tankere iç transfer",
        subtitle="Sabit tanktan mobil tankere aktarılan yakıt tüketim sayılmaz.",
        accent="blue",
        extra_context={"storage_status": _storage_status_data(storages)},
    )


@login_required
@permission_required("fuel.add_fuelmovement", raise_exception=True)
def vehicle_fueling_create(request):
    storages = StorageUnit.active.all()
    return _movement_create(
        request,
        form_class=VehicleFuelingForm,
        title="Araca yakıt verme",
        subtitle="Sabit tank veya mobil tanker üzerinden yapılan araç dolumunu kaydedin.",
        accent="orange",
        extra_context={
            "storage_status": _storage_status_data(storages),
            "vehicle_meter_status": _vehicle_meter_status_data(),
        },
    )


@login_required
@permission_required("fuel.add_fuelingrequest", raise_exception=True)
def operator_fueling_request(request):
    form = OperatorFuelingRequestForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        try:
            request_record = form.save_for_user()
        except ValidationError as error:
            _add_validation_errors(form, error)
        else:
            messages.success(
                request,
                f"{request_record.vehicle.code} için yakıt kaydı onaya gönderildi.",
            )
            return redirect("operator-fueling-request")

    storages = operator_storages_for(request.user)
    recent_requests = (
        FuelingRequest.objects.filter(submitted_by=request.user)
        .select_related("source_storage", "vehicle", "decided_by")[:12]
    )
    return render(
        request,
        "fuel/operator_fueling_request.html",
        {
            "form": form,
            "recent_requests": recent_requests,
            "storage_status": _storage_status_data(storages),
            "vehicle_meter_status": _vehicle_meter_status_data(),
        },
    )


def _approval_filters(params):
    queryset = FuelingRequest.objects.select_related(
        "source_storage",
        "source_region",
        "vehicle",
        "vehicle_region",
        "submitted_by",
        "decided_by",
        "movement",
    )
    operator_id = params.get("operator", "")
    region_id = params.get("region", "")
    status = params.get("status", FuelingRequest.Status.PENDING)
    search_query = params.get("q", "").strip()

    if operator_id.isdigit():
        queryset = queryset.filter(submitted_by_id=operator_id)
    if region_id.isdigit():
        queryset = queryset.filter(source_region_id=region_id)
    if status in FuelingRequest.Status.values:
        queryset = queryset.filter(status=status)
    elif status != "all":
        status = FuelingRequest.Status.PENDING
        queryset = queryset.filter(status=status)
    if search_query:
        queryset = queryset.filter(
            Q(vehicle__code__icontains=search_query)
            | Q(vehicle__tag_code__icontains=search_query)
            | Q(vehicle__name__icontains=search_query)
            | Q(source_storage__code__icontains=search_query)
            | Q(source_storage__name__icontains=search_query)
        )
    return queryset, {
        "operator": operator_id,
        "region": region_id,
        "status": status,
        "q": search_query,
    }


def _validation_error_text(error):
    if hasattr(error, "messages"):
        return " ".join(error.messages)
    return str(error)


@login_required
@permission_required("fuel.approve_fuelingrequest", raise_exception=True)
def fueling_request_approval_list(request):
    queryset, filters = _approval_filters(request.GET)
    page_size_options = (50, 100, 500)
    try:
        page_size = int(request.GET.get("page_size", "50"))
    except (TypeError, ValueError):
        page_size = 50
    if page_size not in page_size_options:
        page_size = 50

    if request.method == "POST":
        request_ids = list(
            dict.fromkeys(
                value for value in request.POST.getlist("request_ids") if value.isdigit()
            )
        )
        if not request_ids:
            messages.warning(request, "Onaylamak için en az bir kayıt seçin.")
        elif request.POST.get("confirm") != "1":
            # JS'siz tarayıcılarda da onay adımı zorunlu: seçim önce
            # özetle gösterilir, işlem yalnızca confirm=1 ile uygulanır.
            return _approval_list_response(
                request,
                queryset=queryset,
                filters=filters,
                bulk_confirm_requests=list(
                    FuelingRequest.objects.filter(pk__in=request_ids)
                    .select_related("vehicle", "source_storage")
                    .order_by("pk")
                ),
            )
        else:
            approved_count = 0
            failed = []
            negative_warnings = []
            labels = {
                str(item.pk): item.vehicle.code
                for item in FuelingRequest.objects.filter(pk__in=request_ids)
                .select_related("vehicle")
            }
            for request_id in request_ids:
                try:
                    approved_request = approve_fueling_request(
                        request_id=int(request_id), user=request.user
                    )
                except (FuelingRequest.DoesNotExist, ValidationError) as error:
                    failed.append(
                        f"{labels.get(request_id, '#' + request_id)}: "
                        f"{_validation_error_text(error)}"
                    )
                else:
                    approved_count += 1
                    warning = negative_stock_warning_for(approved_request.movement)
                    if warning:
                        negative_warnings.append(warning)
            if approved_count:
                messages.success(request, f"{approved_count} saha kaydı onaylandı.")
            for warning in negative_warnings[:4]:
                messages.warning(request, warning)
            if failed:
                preview = " · ".join(failed[:4])
                if len(failed) > 4:
                    preview += f" · {len(failed) - 4} kayıt daha"
                messages.error(request, f"Onaylanamayan kayıtlar: {preview}")

        query = request.GET.urlencode()
        target = reverse("fueling-request-approval-list")
        return redirect(f"{target}?{query}" if query else target)

    return _approval_list_response(request, queryset=queryset, filters=filters)


def _approval_list_response(
    request,
    *,
    queryset,
    filters,
    bulk_confirm_requests=None,
):
    page_size_options = (50, 100, 500)
    try:
        page_size = int(request.GET.get("page_size", "50"))
    except (TypeError, ValueError):
        page_size = 50
    if page_size not in page_size_options:
        page_size = 50
    paginator = Paginator(queryset, page_size)
    page = paginator.get_page(request.GET.get("page"))
    operators = (
        get_user_model()
        .objects.filter(submitted_fueling_requests__isnull=False)
        .distinct()
        .order_by("username")
    )
    return render(
        request,
        "fuel/fueling_request_approval_list.html",
        {
            "page_obj": page,
            "filters": filters,
            "operators": operators,
            "regions": Region.objects.all(),
            "statuses": FuelingRequest.Status.choices,
            "page_size": page_size,
            "page_size_options": page_size_options,
            "pending_count": FuelingRequest.objects.filter(
                status=FuelingRequest.Status.PENDING
            ).count(),
            "bulk_confirm_requests": bulk_confirm_requests or [],
        },
    )


@login_required
@permission_required("fuel.approve_fuelingrequest", raise_exception=True)
@require_POST
def fueling_request_reject(request, pk):
    form = RejectFuelingRequestForm(request.POST)
    if form.is_valid():
        try:
            request_record = reject_fueling_request(
                request_id=pk,
                user=request.user,
                reason=form.cleaned_data["reason"],
            )
        except (FuelingRequest.DoesNotExist, ValidationError) as error:
            messages.error(request, _validation_error_text(error))
        else:
            messages.success(
                request,
                f"{request_record.vehicle.code} saha kaydı reddedildi.",
            )
    else:
        messages.error(request, "Ret için en az 3 karakterlik gerekçe yazın.")
    return redirect("fueling-request-approval-list")


@login_required
@permission_required("fuel.change_fuelmovement", raise_exception=True)
def stock_adjustment_create(request):
    storages = StorageUnit.active.all()
    return _movement_create(
        request,
        form_class=StockAdjustmentForm,
        title="Stok düzeltmesi",
        subtitle="Fiziksel sayım sonucunu girin; stok farkını sistem hesaplasın.",
        accent="purple",
        note_text=(
            "Stok düzeltmesi toplam gelen veya araç tüketimi sayılmaz. "
            "Yalnızca seçilen tankın ya da tankerin mevcut stoğunu değiştirir."
        ),
        extra_context={"storage_status": _storage_status_data(storages)},
    )


@login_required
@permission_required("fuel.change_fuelmovement", raise_exception=True)
def movement_void(request, pk):
    movement = get_object_or_404(
        FuelMovement.objects.select_related(
            "source_storage",
            "destination_storage",
            "supplier",
            "vehicle",
            "created_by",
            "voided_by",
        ),
        pk=pk,
    )
    if movement.is_voided:
        messages.warning(request, "Bu hareket daha önce iptal edilmiş.")
        return redirect("movement-list")

    form = MovementVoidForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            voided = void_movement(
                movement_id=movement.pk,
                user=request.user,
                reason=form.cleaned_data["reason"],
            )
        except ValidationError as error:
            _add_validation_errors(form, error)
        else:
            messages.success(
                request,
                f"{voided.get_movement_type_display()} hareketi iptal edildi.",
            )
            return redirect("movement-list")

    return render(
        request,
        "fuel/movement_void.html",
        {"form": form, "movement": movement},
    )


@login_required
@permission_required("fuel.change_fuelmovement", raise_exception=True)
def movement_correct(request, pk):
    movement = get_object_or_404(
        FuelMovement.objects.select_related(
            "source_storage",
            "destination_storage",
            "supplier",
            "vehicle",
            "created_by",
            "correction",
        ),
        pk=pk,
    )
    if movement.is_voided:
        messages.warning(request, "İptal edilmiş bir hareket düzeltilemez.")
        return redirect("movement-list")
    if hasattr(movement, "correction"):
        messages.warning(request, "Bu hareket daha önce düzeltilmiş.")
        return redirect("movement-list")
    if movement.movement_type == FuelMovement.MovementType.STOCK_ADJUSTMENT:
        messages.info(
            request,
            "Stok düzeltmeleri fiziksel sayım kaydıdır. Hatalı kaydı iptal edip yeni sayım girin.",
        )
        return redirect("movement-list")

    form_class = form_class_for_movement(movement)
    if request.method == "POST":
        form = form_class(request.POST)
        reason_form = MovementCorrectionReasonForm(request.POST)
    else:
        form = form_class(initial=initial_for_movement(movement, form_class))
        reason_form = MovementCorrectionReasonForm()
    include_original_choices(form, movement)

    form_is_valid = form.is_valid() if request.method == "POST" else False
    reason_is_valid = reason_form.is_valid() if request.method == "POST" else False
    if form_is_valid and reason_is_valid:
        try:
            replacement = correct_movement(
                movement=movement,
                data=form.movement_data(),
                reason=reason_form.cleaned_data["correction_reason"],
                user=request.user,
            )
        except ValidationError as error:
            if hasattr(error, "error_dict"):
                for field_name, field_errors in error.error_dict.items():
                    target_form = (
                        reason_form
                        if field_name == "correction_reason"
                        else form
                    )
                    target_name = field_name if field_name in target_form.fields else None
                    for field_error in field_errors:
                        target_form.add_error(target_name, field_error)
            else:
                form.add_error(None, error)
        else:
            messages.success(
                request,
                f"{replacement.get_movement_type_display()} hareketi düzeltildi.",
            )
            warning = negative_stock_warning_for(replacement)
            if warning:
                messages.warning(request, warning)
            return redirect("movement-list")

    correction_context = {
        "form": form,
        "reason_form": reason_form,
        "movement": movement,
    }
    if movement.movement_type == FuelMovement.MovementType.VEHICLE_FUELING:
        correction_context.update(
            {
                "vehicle_meter_status": _vehicle_meter_status_data(),
                "contractor_price_definitions": (
                    _contractor_price_definitions_data()
                ),
            }
        )
    return render(
        request,
        "fuel/movement_correct.html",
        correction_context,
    )


def _master_page(
    request,
    *,
    form_class,
    queryset,
    add_permission,
    template_name,
    title,
    search_fields,
    prepare_items=None,
    search_placeholder=None,
    regions=None,
):
    can_add = request.user.has_perm(add_permission)
    if request.method == "POST":
        if not can_add:
            raise PermissionDenied
        form = form_class(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Kayıt başarıyla oluşturuldu.")
            return redirect(request.path)
    else:
        form = form_class()

    search_query = request.GET.get("q", "").strip()
    region_filter = request.GET.get("region", "")
    status_filter = request.GET.get("status", "")
    if search_query:
        search_condition = Q()
        for field_name in search_fields:
            search_condition |= Q(**{f"{field_name}__icontains": search_query})
        queryset = queryset.filter(search_condition)
    if regions is None:
        region_filter = ""
    else:
        try:
            region_id = int(region_filter)
        except (TypeError, ValueError):
            region_filter = ""
        else:
            if region_id > 0:
                queryset = queryset.filter(region_id=region_id)
            else:
                region_filter = ""
    if status_filter == "active":
        queryset = queryset.filter(is_active=True)
    elif status_filter == "passive":
        queryset = queryset.filter(is_active=False)
    else:
        status_filter = ""

    paginator = Paginator(queryset, 30)
    page = paginator.get_page(request.GET.get("page"))
    items = list(page.object_list)
    if prepare_items:
        items = prepare_items(items)
    page.object_list = items
    return render(
        request,
        template_name,
        {
            "form": form,
            "items": items,
            "page_obj": page,
            "can_add": can_add,
            "title": title,
            "search_query": search_query,
            "region_filter": region_filter,
            "regions": regions,
            "status_filter": status_filter,
            "search_placeholder": search_placeholder,
        },
    )


def _master_edit(
    request,
    *,
    model,
    form_class,
    pk,
    list_url_name,
    title,
):
    item = get_object_or_404(model, pk=pk)
    form = form_class(request.POST or None, instance=item)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Tanım başarıyla güncellendi.")
        return redirect(list_url_name)
    return render(
        request,
        "fuel/master_edit.html",
        {
            "form": form,
            "item": item,
            "title": title,
            "list_url_name": list_url_name,
            "heading_description_expr": f"{item} kaydının kullanılabilir bilgilerini güncelleyin.",
        },
    )


def _master_toggle(request, *, model, form_class, pk, list_url_name):
    item = get_object_or_404(model, pk=pk)
    field_names = list(form_class.base_fields)
    data = model_to_dict(item, fields=field_names)
    data["is_active"] = not item.is_active
    form = form_class(data, instance=item)
    if form.is_valid():
        form.save()
        state = "aktifleştirildi" if item.is_active else "pasife alındı"
        messages.success(request, f"{item} {state}.")
    else:
        error_text = " ".join(
            str(error)
            for field_errors in form.errors.values()
            for error in field_errors
        )
        messages.error(request, error_text)
    return redirect(list_url_name)


@login_required
@permission_required("fuel.view_region", raise_exception=True)
@require_GET
def region_list(request):
    return _master_page(
        request,
        form_class=RegionForm,
        queryset=(
            Region.objects.annotate(
                active_storage_count=Count(
                    "storage_units",
                    filter=Q(storage_units__is_active=True),
                    distinct=True,
                ),
                active_vehicle_count=Count(
                    "vehicles",
                    filter=Q(vehicles__is_active=True),
                    distinct=True,
                ),
            ).order_by("name")
        ),
        add_permission="fuel.add_region",
        template_name="fuel/region_list.html",
        title="Bölgeler",
        search_fields=("name", "code"),
    )


@login_required
@permission_required("fuel.add_region", raise_exception=True)
def region_create(request):
    form = RegionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        region = form.save()
        messages.success(request, f"{region} eklendi.")
        return redirect("region-list")
    return render(
        request,
        "fuel/region_create.html",
        {"form": form},
    )


@login_required
@permission_required("fuel.change_region", raise_exception=True)
def region_edit(request, pk):
    return _master_edit(
        request,
        model=Region,
        form_class=RegionForm,
        pk=pk,
        list_url_name="region-list",
        title="Bölgeyi düzenle",
    )


@require_POST
@login_required
@permission_required("fuel.change_region", raise_exception=True)
def region_toggle(request, pk):
    return _master_toggle(
        request,
        model=Region,
        form_class=RegionForm,
        pk=pk,
        list_url_name="region-list",
    )


@login_required
@permission_required("fuel.view_supplier", raise_exception=True)
@require_GET
def supplier_list(request):
    return _master_page(
        request,
        form_class=SupplierForm,
        queryset=(
            Supplier.objects.annotate(
                total_received=Sum(
                    "fuel_movements__liters",
                    filter=Q(
                        fuel_movements__movement_type=FuelMovement.MovementType.SUPPLIER_RECEIPT,
                        fuel_movements__voided_at__isnull=True,
                    ),
                    default=ZERO,
                )
            ).order_by("name")
        ),
        add_permission="fuel.add_supplier",
        template_name="fuel/supplier_list.html",
        title="Tedarikçiler",
        search_fields=("name",),
    )


@login_required
@permission_required("fuel.add_supplier", raise_exception=True)
def supplier_create(request):
    form = SupplierForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        supplier = form.save()
        messages.success(request, f"{supplier} eklendi.")
        return redirect("supplier-list")
    return render(
        request,
        "fuel/supplier_create.html",
        {"form": form},
    )


@login_required
@permission_required("fuel.change_supplier", raise_exception=True)
def supplier_edit(request, pk):
    return _master_edit(
        request,
        model=Supplier,
        form_class=SupplierForm,
        pk=pk,
        list_url_name="supplier-list",
        title="Tedarikçiyi düzenle",
    )


@require_POST
@login_required
@permission_required("fuel.change_supplier", raise_exception=True)
def supplier_toggle(request, pk):
    return _master_toggle(
        request,
        model=Supplier,
        form_class=SupplierForm,
        pk=pk,
        list_url_name="supplier-list",
    )


@login_required
@permission_required("fuel.view_storageunit", raise_exception=True)
@require_GET
def storage_list(request):
    return _master_page(
        request,
        form_class=StorageUnitForm,
        queryset=StorageUnit.objects.select_related("region"),
        add_permission="fuel.add_storageunit",
        template_name="fuel/storage_list.html",
        title="Tanklar ve tankerler",
        search_fields=("name", "code", "region__name"),
        regions=Region.objects.order_by("name"),
        prepare_items=lambda items: attach_balances(items, stock_by_storage(items)),
    )


@login_required
@permission_required("fuel.add_storageunit", raise_exception=True)
def storage_create(request):
    form = StorageUnitForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        storage = form.save()
        messages.success(request, f"{storage} eklendi.")
        return redirect("storage-list")
    return render(
        request,
        "fuel/storage_create.html",
        {"form": form},
    )


@login_required
@permission_required("fuel.change_storageunit", raise_exception=True)
def storage_edit(request, pk):
    return _master_edit(
        request,
        model=StorageUnit,
        form_class=StorageUnitForm,
        pk=pk,
        list_url_name="storage-list",
        title="Tank veya tankeri düzenle",
    )


@login_required
@permission_required("fuel.change_storageunit", raise_exception=True)
def storage_region_change(request, pk):
    storage = get_object_or_404(
        StorageUnit.objects.select_related("region"),
        pk=pk,
    )
    form = StorageRegionChangeForm(request.POST or None, storage=storage)
    if request.method == "POST" and form.is_valid():
        try:
            change = form.save_for_user(request.user)
        except ValidationError as error:
            _add_validation_errors(form, error)
        else:
            messages.success(
                request,
                f"{storage.code} tank/tankeri {change.to_region.name} bölgesine geçirildi.",
            )
            return redirect("storage-list")

    history = storage.region_changes.select_related(
        "from_region",
        "to_region",
        "created_by",
    )
    return render(
        request,
        "fuel/storage_region_change.html",
        {
            "form": form,
            "storage": storage,
            "current_stock": stock_for(storage),
            "history": history,
            "heading_description_expr": (
                f"{storage.code} · {storage.name} kaydını geçmiş hareketleri "
                "değiştirmeden başka bölgeye aktarın."
            ),
        },
    )


@require_POST
@login_required
@permission_required("fuel.change_storageunit", raise_exception=True)
def storage_toggle(request, pk):
    return _master_toggle(
        request,
        model=StorageUnit,
        form_class=StorageUnitForm,
        pk=pk,
        list_url_name="storage-list",
    )


@login_required
@permission_required("fuel.view_vehicle", raise_exception=True)
@require_GET
def vehicle_list(request):
    queryset = Vehicle.objects.select_related("region")
    search_query = request.GET.get("q", "").strip()
    region_filter = request.GET.get("region", "")
    meter_type_filter = request.GET.get("meter_type", "")
    vehicle_class_filter = request.GET.get("vehicle_class", "")
    status_filter = request.GET.get("status", "")
    page_size_options = (50, 100, 500)

    if search_query:
        queryset = queryset.filter(
            Q(code__icontains=search_query)
            | Q(tag_code__icontains=search_query)
            | Q(name__icontains=search_query)
            | Q(organization__icontains=search_query)
            | Q(region__name__icontains=search_query)
        )

    try:
        region_id = int(region_filter)
    except (TypeError, ValueError):
        region_filter = ""
    else:
        if region_id > 0:
            queryset = queryset.filter(region_id=region_id)
        else:
            region_filter = ""

    if meter_type_filter in Vehicle.MeterType.values:
        queryset = queryset.filter(meter_type=meter_type_filter)
    else:
        meter_type_filter = ""

    if vehicle_class_filter == "contractor":
        queryset = queryset.filter(is_contractor=True)
    elif vehicle_class_filter == "in_house":
        queryset = queryset.filter(is_contractor=False)
    else:
        vehicle_class_filter = ""

    if status_filter == "active":
        queryset = queryset.filter(is_active=True)
    elif status_filter == "passive":
        queryset = queryset.filter(is_active=False)
    else:
        status_filter = ""

    try:
        page_size = int(request.GET.get("page_size", "50"))
    except (TypeError, ValueError):
        page_size = 50
    if page_size not in page_size_options:
        page_size = 50

    page = Paginator(queryset, page_size).get_page(request.GET.get("page"))
    return render(
        request,
        "fuel/vehicle_list.html",
        {
            "items": list(page.object_list),
            "page_obj": page,
            "can_add": request.user.has_perm("fuel.add_vehicle"),
            "search_query": search_query,
            "region_filter": region_filter,
            "meter_type_filter": meter_type_filter,
            "vehicle_class_filter": vehicle_class_filter,
            "status_filter": status_filter,
            "page_size": page_size,
            "page_size_options": page_size_options,
            "regions": Region.objects.order_by("name"),
            "meter_types": Vehicle.MeterType.choices,
            "vehicle_classes": (
                ("in_house", "Kurum aracı"),
                ("contractor", "Taşeron"),
            ),
            "search_placeholder": "Plaka, RFID veya açıklama ara",
        },
    )


@login_required
@permission_required("fuel.add_vehicle", raise_exception=True)
def vehicle_create(request):
    form = VehicleForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        vehicle = form.save()
        messages.success(request, f"{vehicle} eklendi.")
        return redirect("vehicle-list")
    return render(
        request,
        "fuel/vehicle_create.html",
        {"form": form},
    )


@login_required
@permission_required("fuel.add_vehicle", raise_exception=True)
def vehicle_import(request):
    action = request.POST.get("action") if request.method == "POST" else ""
    form = (
        VehicleImportForm()
        if action == "import"
        else VehicleImportForm(request.POST or None, request.FILES or None)
    )
    preview = None
    preview_token = ""

    if request.method == "POST" and action == "import":
        try:
            payload = signing.loads(
                request.POST.get("preview_token", ""),
                salt="fuel.vehicle-import",
                max_age=30 * 60,
            )
            if payload.get("version") != 2:
                raise signing.BadSignature
            result = import_vehicle_rows(
                payload["rows"],
            )
        except signing.SignatureExpired:
            form.add_error(None, "İçe aktarma önizlemesinin süresi doldu. Dosyayı yeniden kontrol edin.")
        except (signing.BadSignature, KeyError, TypeError):
            form.add_error(None, "İçe aktarma önizlemesi geçersiz. Dosyayı yeniden kontrol edin.")
        except ValidationError as error:
            _add_validation_errors(form, error)
        else:
            message = f"{result['created_count']} yeni araç/makine içe aktarıldı."
            if result["updated_count"]:
                message += f" {result['updated_count']} mevcut aracın bilgileri güncellendi."
            if result["skipped_count"]:
                message += f" {result['skipped_count']} değişiklik gerektirmeyen kayıt atlandı."
            if result["protected_count"]:
                messages.warning(
                    request,
                    f"{result['protected_count']} mevcut kayıt geçmiş sayaç hareketleri "
                    "nedeniyle güncellenmeden bırakıldı.",
                )
            if result["unassigned_region_count"]:
                message += (
                    f" {result['unassigned_region_count']} kayıt "
                    "Bölgesi belirlenmedi altında oluşturuldu."
                )
            messages.success(request, message)
            return redirect("vehicle-list")

    elif request.method == "POST" and form.is_valid():
        try:
            parsed = parse_vehicle_workbook(form.cleaned_data["workbook"])
        except ValidationError as error:
            _add_validation_errors(form, error)
        else:
            assessment = assess_vehicle_rows(parsed["rows"])
            preview = {
                **parsed,
                **assessment,
                "sample_rows": parsed["rows"][:10],
            }
            if not assessment["conflicts"]:
                preview_token = signing.dumps(
                    {
                        "version": 2,
                        "rows": parsed["rows"],
                    },
                    salt="fuel.vehicle-import",
                    compress=True,
                )

    return render(
        request,
        "fuel/vehicle_import.html",
        {
            "form": form,
            "preview": preview,
            "preview_token": preview_token,
        },
    )


@login_required
@permission_required("fuel.change_vehicle", raise_exception=True)
def vehicle_edit(request, pk):
    return _master_edit(
        request,
        model=Vehicle,
        form_class=VehicleForm,
        pk=pk,
        list_url_name="vehicle-list",
        title="Araç veya makineyi düzenle",
    )


@login_required
@permission_required("fuel.change_vehicle", raise_exception=True)
def vehicle_region_change(request, pk):
    vehicle = get_object_or_404(
        Vehicle.objects.select_related("region"),
        pk=pk,
    )
    form = VehicleRegionChangeForm(request.POST or None, vehicle=vehicle)
    if request.method == "POST" and form.is_valid():
        try:
            change = form.save_for_user(request.user)
        except ValidationError as error:
            _add_validation_errors(form, error)
        else:
            messages.success(
                request,
                f"{vehicle.code} aracı {change.to_region.name} bölgesine geçirildi.",
            )
            return redirect("vehicle-list")

    history = vehicle.region_changes.select_related(
        "from_region",
        "to_region",
        "created_by",
    )
    latest_meter = (
        FuelMovement.objects.active()
        .filter(vehicle=vehicle, meter_value__isnull=False)
        .order_by("-occurred_at", "-pk")
        .values_list("meter_value", flat=True)
        .first()
    )
    return render(
        request,
        "fuel/vehicle_region_change.html",
        {
            "form": form,
            "vehicle": vehicle,
            "latest_meter": latest_meter,
            "history": history,
            "heading_description_expr": (
                f"{vehicle.code} · {vehicle.name} kaydını geçmiş dolumları "
                "değiştirmeden başka bölgeye aktarın."
            ),
        },
    )


@require_POST
@login_required
@permission_required("fuel.change_vehicle", raise_exception=True)
def vehicle_toggle(request, pk):
    return _master_toggle(
        request,
        model=Vehicle,
        form_class=VehicleForm,
        pk=pk,
        list_url_name="vehicle-list",
    )


def _report_context(params):
    queryset, filters = _movement_filters(params, use_default_period=True)
    totals = period_totals(queryset)
    vehicle_summary = vehicle_consumption_summary(queryset)
    # Satır bazlı özetler ve sayfalama aynı materialize edilmiş listeyi paylaşır;
    # büyük dönemlerde queryset'in tekrar tekrar taranması önlenir.
    movements = list(queryset)
    report_storages = StorageUnit.objects.select_related("region")
    if filters["region"].isdigit():
        region_id = int(filters["region"])
        historical_storage_ids = {
            storage_id
            for movement in movements
            if region_id
            in (movement.source_region_id, movement.destination_region_id)
            for storage_id in (
                movement.source_storage_id,
                movement.destination_storage_id,
            )
            if storage_id is not None
        }
        report_storages = report_storages.filter(
            Q(region_id=region_id) | Q(pk__in=historical_storage_ids)
        )
    if filters["storage"].isdigit():
        report_storages = report_storages.filter(pk=filters["storage"])
    report_storages = list(report_storages)
    storage_summary = storage_activity_summary(movements, report_storages)
    start_at = _aware_boundary(filters["start"])
    end_at = _aware_boundary(filters["end"], end=True)
    opening_balances = (
        stock_by_storage(
            report_storages,
            as_of=start_at - timedelta(microseconds=1),
        )
        if start_at
        else {storage.pk: ZERO for storage in report_storages}
    )
    closing_balances = stock_by_storage(
        report_storages,
        as_of=end_at - timedelta(microseconds=1) if end_at else None,
    )
    for row in storage_summary:
        storage_id = row["storage"].pk
        row["opening_stock"] = opening_balances[storage_id]
        row["closing_stock"] = closing_balances[storage_id]
        row["period_region"] = storage_region_at(
            row["storage"],
            end_at - timedelta(microseconds=1) if end_at else timezone.now(),
        )

    contractor_prices = ContractorFuelPrice.objects.select_related("created_by")
    if end_at:
        contractor_prices = contractor_prices.filter(effective_from__lt=end_at)
    contractor_prices = list(contractor_prices.order_by("effective_from", "pk"))
    if start_at:
        earlier_price = (
            ContractorFuelPrice.objects.select_related("created_by")
            .filter(effective_from__lt=start_at)
            .order_by("-effective_from", "-pk")
            .first()
        )
        contractor_prices = [
            item for item in contractor_prices if item.effective_from >= start_at
        ]
        if earlier_price is not None:
            contractor_prices.insert(0, earlier_price)

    return {
        "queryset": movements,
        "filters": filters,
        "totals": totals,
        "vehicle_summary": vehicle_summary,
        "supplier_summary": supplier_receipt_summary(queryset),
        "daily_summary": daily_activity_summary(queryset),
        "region_summary": region_activity_summary(movements),
        "storage_summary": storage_summary,
        "contractor_prices": contractor_prices,
    }


def _contractor_report_params(params):
    scoped_params = params.copy()
    scoped_params["movement_type"] = FuelMovement.MovementType.VEHICLE_FUELING
    scoped_params["meter_type"] = ""
    scoped_params["vehicle_class"] = "contractor"
    return scoped_params


def _report_page_context(request, *, contractor_report=False):
    params = (
        _contractor_report_params(request.GET)
        if contractor_report
        else request.GET
    )
    context = _report_context(params)
    paginator = Paginator(context["queryset"], 50)
    context["page_obj"] = paginator.get_page(request.GET.get("page"))
    context.update(_filter_options())
    if contractor_report:
        context["vehicles"] = Vehicle.objects.select_related("region").filter(
            is_contractor=True
        )
        movements = context["queryset"]
        contractor_financials = contractor_pricing_totals(movements)
        vehicle_financials = {}
        for movement in movements:
            if (
                movement.contractor_reference_total is None
                or movement.contractor_charged_total is None
            ):
                continue
            key = (movement.vehicle_id, movement.vehicle_region_id)
            totals = vehicle_financials.setdefault(
                key,
                {"reference_total": ZERO, "charged_total": ZERO},
            )
            totals["reference_total"] += movement.contractor_reference_total
            totals["charged_total"] += movement.contractor_charged_total
        for row in context["vehicle_summary"]:
            amounts = vehicle_financials.get(
                (row["vehicle_id"], row["vehicle_region_id"]),
                {"reference_total": ZERO, "charged_total": ZERO},
            )
            row.update(amounts)
            row["difference_total"] = (
                amounts["charged_total"] - amounts["reference_total"]
            )
        context["contractor_stats"] = {
            "fueling_count": len(movements),
            "vehicle_count": len(
                {item.vehicle_id for item in movements if item.vehicle_id}
            ),
            "region_count": len(
                {item.vehicle_region_id for item in movements if item.vehicle_region_id}
            ),
            **contractor_financials,
        }
    context["contractor_report"] = contractor_report
    context["report_export_url_name"] = (
        "contractor-report-export-xlsx"
        if contractor_report
        else "report-export-xlsx"
    )
    return context


@login_required
@permission_required("fuel.view_fuelmovement", raise_exception=True)
def reports(request):
    context = _report_page_context(request)
    return render(request, "fuel/reports.html", context)


@login_required
@permission_required("fuel.view_fuelmovement", raise_exception=True)
def contractor_reports(request):
    context = _report_page_context(request, contractor_report=True)
    return render(request, "fuel/reports.html", context)


@login_required
@permission_required("fuel.view_fuelmovement", raise_exception=True)
def contractor_price_list(request):
    can_add = request.user.has_perm("fuel.change_fuelmovement")
    if request.method == "POST" and not can_add:
        raise PermissionDenied

    form = ContractorFuelPriceForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        price = form.save(commit=False)
        price.created_by = request.user
        price.full_clean()
        price.save(force_insert=True)
        messages.success(request, "Yeni taşeron yakıt fiyatı tanımlandı.")
        return redirect("contractor-price-list")

    return render(
        request,
        "fuel/contractor_price_list.html",
        {
            "form": form,
            "prices": ContractorFuelPrice.objects.select_related("created_by")[:100],
            "current_price": contractor_price_at(timezone.now()),
            "can_add": can_add,
        },
    )


def _report_filter_labels(filters):
    region = (
        Region.objects.filter(pk=filters["region"]).first()
        if filters["region"].isdigit()
        else None
    )
    storage = (
        StorageUnit.objects.select_related("region")
        .filter(pk=filters["storage"])
        .first()
        if filters["storage"].isdigit()
        else None
    )
    vehicle = (
        Vehicle.objects.filter(pk=filters["vehicle"]).first()
        if filters["vehicle"].isdigit()
        else None
    )
    return {
        "region": region.name if region else "Tümü",
        "storage": str(storage) if storage else "Tümü",
        "vehicle": str(vehicle) if vehicle else "Tümü",
        "meter_type": dict(Vehicle.MeterType.choices).get(
            filters.get("meter_type"), "Tümü"
        ),
        "vehicle_class": {
            "in_house": "Kurum aracı",
            "contractor": "Taşeron",
        }.get(filters.get("vehicle_class"), "Tümü"),
    }


@login_required
@permission_required("fuel.view_fuelmovement", raise_exception=True)
def report_export_xlsx(request):
    context = _report_context(request.GET)
    context["filter_labels"] = _report_filter_labels(context["filters"])
    workbook = build_report_workbook(
        context=context,
        generated_by=request.user,
        generated_at=timezone.now(),
    )

    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    response = HttpResponse(
        output.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = (
        f'attachment; filename="{report_filename(context["filters"])}"'
    )
    return response


@login_required
@permission_required("fuel.view_fuelmovement", raise_exception=True)
def contractor_report_export_xlsx(request):
    context = _report_context(_contractor_report_params(request.GET))
    context["contractor_report"] = True
    context["filter_labels"] = _report_filter_labels(context["filters"])
    workbook = build_report_workbook(
        context=context,
        generated_by=request.user,
        generated_at=timezone.now(),
    )

    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    response = HttpResponse(
        output.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = (
        f'attachment; filename="{report_filename(context["filters"])}"'
    )
    return response
