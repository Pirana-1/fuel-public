from django.contrib import messages
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET

from .user_forms import UserCreateForm, UserSetPasswordForm, UserUpdateForm


User = get_user_model()


def _require_superuser(request):
    if not request.user.is_superuser:
        raise PermissionDenied


@login_required
@require_GET
def user_list(request):
    _require_superuser(request)
    queryset = User.objects.prefetch_related("groups").order_by("username")
    search_query = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "")
    if search_query:
        queryset = queryset.filter(
            Q(username__icontains=search_query)
            | Q(first_name__icontains=search_query)
            | Q(last_name__icontains=search_query)
            | Q(email__icontains=search_query)
        )
    if status_filter == "active":
        queryset = queryset.filter(is_active=True)
    elif status_filter == "passive":
        queryset = queryset.filter(is_active=False)
    else:
        status_filter = ""

    page = Paginator(queryset, 30).get_page(request.GET.get("page"))
    return render(
        request,
        "fuel/user_list.html",
        {
            "page_obj": page,
            "search_query": search_query,
            "status_filter": status_filter,
        },
    )


@login_required
def user_create(request):
    _require_superuser(request)
    form = UserCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        messages.success(request, f"{user.get_username()} kullanıcısı oluşturuldu.")
        return redirect("user-list")
    return render(request, "fuel/user_create.html", {"form": form})


@login_required
def user_edit(request, pk):
    _require_superuser(request)
    user = get_object_or_404(User, pk=pk)
    form = UserUpdateForm(
        request.POST or None,
        instance=user,
        current_user=request.user,
    )
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"{user.get_username()} kullanıcısı güncellendi.")
        return redirect("user-list")
    return render(request, "fuel/user_edit.html", {"form": form, "edited_user": user})


@login_required
def user_set_password(request, pk):
    _require_superuser(request)
    user = get_object_or_404(User, pk=pk)
    form = UserSetPasswordForm(user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        changed_user = form.save()
        if changed_user == request.user:
            update_session_auth_hash(request, changed_user)
        messages.success(request, f"{user.get_username()} kullanıcısının parolası yenilendi.")
        return redirect("user-list")
    return render(
        request,
        "fuel/user_set_password.html",
        {
            "form": form,
            "edited_user": user,
            "heading_title_expr": f"{user.username} için parola yenile",
        },
    )
