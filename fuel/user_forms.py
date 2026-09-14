from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import SetPasswordForm, UserCreationForm
from django.contrib.auth.models import Group
from django.db import transaction

from .models import OperatorStorageAccess, StorageUnit


User = get_user_model()


def _style_user_fields(fields):
    for field in fields.values():
        if isinstance(field.widget, forms.CheckboxInput):
            field.widget.attrs.setdefault("class", "form-check-input")
        elif isinstance(field.widget, forms.CheckboxSelectMultiple):
            field.widget.attrs.setdefault("class", "role-option-list")
        else:
            field.widget.attrs.setdefault("class", "form-control")


def _storage_access_label(storage):
    return f"{storage.code} — {storage.name} · {storage.region.name}"


def _operator_storage_field():
    return forms.ModelMultipleChoiceField(
        label="Erişebileceği tank ve tankerler",
        queryset=StorageUnit.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text="Saha Operatörü yalnızca burada seçilen stok noktalarından kayıt gönderebilir.",
    )


class OperatorStorageAccessMixin:
    def _setup_operator_storages(self):
        self.fields["operator_storages"].queryset = StorageUnit.active.select_related(
            "region"
        )
        self.fields["operator_storages"].widget.attrs["class"] = (
            "operator-storage-option-list"
        )
        self.fields["operator_storages"].label_from_instance = _storage_access_label
        if self.instance.pk:
            self.fields["operator_storages"].initial = StorageUnit.objects.filter(
                operator_accesses__user=self.instance
            )

    def clean(self):
        cleaned_data = super().clean()
        groups = cleaned_data.get("groups")
        storages = cleaned_data.get("operator_storages")
        if (
            groups
            and groups.filter(name="Saha Operatörü").exists()
            and not storages
        ):
            self.add_error(
                "operator_storages",
                "Saha Operatörü için en az bir tank veya tanker seçin.",
            )
        return cleaned_data

    @transaction.atomic
    def _save_operator_storages(self, user):
        OperatorStorageAccess.objects.filter(user=user).delete()
        OperatorStorageAccess.objects.bulk_create(
            [
                OperatorStorageAccess(user=user, storage=storage)
                for storage in self.cleaned_data.get("operator_storages", [])
            ]
        )


class UserCreateForm(OperatorStorageAccessMixin, UserCreationForm):
    operator_storages = _operator_storage_field()
    groups = forms.ModelMultipleChoiceField(
        label="Roller",
        queryset=Group.objects.order_by("name"),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = (
            "username",
            "first_name",
            "last_name",
            "email",
            "groups",
            "operator_storages",
            "is_active",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["is_active"].label = "Hesap aktif"
        self.fields["groups"].help_text = "Kullanıcıya bir veya birden fazla rol verebilirsiniz."
        self._setup_operator_storages()
        _style_user_fields(self.fields)

    def save(self, commit=True):
        user = super().save(commit=commit)
        if commit:
            self._save_operator_storages(user)
        return user


class UserUpdateForm(OperatorStorageAccessMixin, forms.ModelForm):
    operator_storages = _operator_storage_field()
    groups = forms.ModelMultipleChoiceField(
        label="Roller",
        queryset=Group.objects.order_by("name"),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = User
        fields = (
            "username",
            "first_name",
            "last_name",
            "email",
            "groups",
            "operator_storages",
            "is_active",
        )

    def __init__(self, *args, current_user=None, **kwargs):
        self.current_user = current_user
        super().__init__(*args, **kwargs)
        self.fields["is_active"].label = "Hesap aktif"
        self.fields["groups"].help_text = "Kullanıcıya bir veya birden fazla rol verebilirsiniz."
        self._setup_operator_storages()
        _style_user_fields(self.fields)

    def save(self, commit=True):
        user = super().save(commit=commit)
        if commit:
            self._save_operator_storages(user)
        return user

    def clean_is_active(self):
        is_active = self.cleaned_data["is_active"]
        if self.instance == self.current_user and not is_active:
            raise forms.ValidationError("Kendi kullanıcı hesabınızı pasife alamazsınız.")
        return is_active


class UserSetPasswordForm(SetPasswordForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style_user_fields(self.fields)
