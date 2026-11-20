"""«البيانات الأساسية» (1.21): add / edit / delete for everything the shop runs with — items, categories, PlayStation
devices, customers / suppliers / employees, cashboxes, warehouses, till devices, expense types, doctors.

Delete really deletes when nothing was ever recorded on the row. When sales / entries point at it, it is stopped instead
(it disappears from the till and the lists, history stays correct) — the books never lose a line.
"""
from decimal import Decimal

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, models, transaction
from django.db.models import ProtectedError, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from core.modules import enabled


def _owner(u):
    from pos.voids import is_owner

    return is_owner(u)


# ------------------------------------------------------------------ what to do when a row is new
def _new_category(obj):
    from inventory.models import ItemCategory

    like = ItemCategory.objects.filter(name="أخرى").first() or ItemCategory.objects.order_by("pk").first()
    obj.stock_account, obj.farm_account, obj.admin_account = like.stock_account, like.farm_account, like.admin_account


def _new_cashbox(obj):
    from ledger.models import Account

    parent = Account.objects.get(code="12010301")
    n = 50
    while Account.objects.filter(code=f"12010301{n:02d}").exists():
        n += 1
    obj.account = Account.objects.create(code=f"12010301{n:02d}", name=obj.name[:150], parent=parent, level=parent.level + 1, is_postable=True,
                                         account_type="asset", normal_balance="debit", report="balance_sheet", cash_flow_category="cash")
    obj.currency = obj.currency or "EGP"


def _new_warehouse(obj):
    from inventory.models import Warehouse
    from ledger.models import DimensionValue

    obj.project = DimensionValue.objects.get(dimension__number=1, code="1")
    if not obj.code:
        n = Warehouse.objects.count() + 1
        while Warehouse.objects.filter(code=f"W{n:02d}").exists():
            n += 1
        obj.code = f"W{n:02d}"


def _new_expense(obj):
    from ledger.models import Account

    parent = Account.objects.get(code="32020101")
    n = 30
    while Account.objects.filter(code=f"32020101{n:02d}").exists():
        n += 1
    obj.code, obj.parent, obj.level, obj.is_postable = f"32020101{n:02d}", parent, parent.level + 1, True
    obj.account_type, obj.normal_balance, obj.report = "expense", "debit", "profit_loss"


def _new_item(obj):
    from inventory.models import Item

    if not obj.code:
        last = Item.objects.filter(code__regex=r"^\d+$").order_by("-id").first()
        code = (int(last.code) + 1) if last else 1001
        while Item.objects.filter(code=str(code)).exists():
            code += 1
        obj.code = str(code)


def _partner_kind(kind):
    def new(obj):
        obj.kind = kind
    return new


# ------------------------------------------------------------------ checks before saving / deleting
def _check_item(obj):
    from inventory.reorder import needs_level

    if needs_level(obj.item_type) and obj.reorder_level is None:
        raise ValidationError("اكتب حد الطلب (0 = من غير تنبيه)")
    if obj.reorder_level is not None and obj.reorder_level < 0:
        raise ValidationError("لا يمكن أن يكون حد الطلب سالبًا")
    if obj.sale_price is not None and obj.sale_price < 0:
        raise ValidationError("سعر البيع لا يكون سالبًا")
    if obj.barcode:
        from inventory.models import Item

        other = Item.objects.filter(barcode=obj.barcode).exclude(pk=obj.pk).first()
        if other:
            raise ValidationError(f"هذا الباركود مسجّل على «{other.name}»")


def _guard_cashbox(obj):
    from pos.models import Branch

    if Branch.objects.filter(cashbox=obj).exists():
        raise ValidationError("هذه خزينة فرع — لا يمكن حذفها (أغلق الفرع أولًا)")


def _guard_warehouse(obj):
    from pos.models import Branch

    if Branch.objects.filter(warehouse=obj).exists():
        raise ValidationError("هذا مخزن فرع — لا يمكن حذفه (أغلق الفرع أولًا)")


def _guard_item(obj):
    if obj.code == "0":
        raise ValidationError("«صنف بدون كود» الخاص بالكاشير — لا يمكن حذفه")


def _guard_device(obj):
    from pos.models import Device

    if not Device.objects.filter(branch=obj.branch, active=True).exclude(pk=obj.pk).exists():
        raise ValidationError("هذا آخر جهاز كاشير في الفرع — أضف جهازًا آخر أولًا")


# key: (title, singular, icon, model path, fields, list columns, permission / owner, module needed, list filter, on new, check, guard, search)
def _kinds():
    from django.apps import apps

    Doctor, Station = apps.get_model("clinic", "Doctor"), apps.get_model("gaming", "Station")
    from pos.models import DiningTable
    from inventory.models import Item, ItemCategory, Warehouse
    from ledger.models import Account, Partner
    from pos.models import Device
    from treasury.models import CashBox

    K = {
        "item": dict(title="الأصناف", one="صنف", icon="📦", model=Item, list_url="shop_items",
                     fields=["name", "code", "category", "unit", "barcode", "sale_price", "vat_rate", "reorder_level", "item_type", "aliases", "active"],
                     cols=["code", "name", "category", "sale_price", "reorder_level"], perm="inventory.change_item",
                     new=_new_item, check=_check_item, guard=_guard_item, search=["name", "code", "barcode"]),
        "category": dict(title="تصنيفات الأصناف", one="تصنيف", icon="🗂", model=ItemCategory, fields=["name"], cols=["name"],
                         perm="inventory.change_item", new=_new_category, search=["name"], count=("item", "صنف")),
        "station": dict(title="أجهزة الصالة (بلايستيشن / بلياردو)", one="جهاز", icon="🎮", model=Station, module="gaming",
                        fields=["name", "kind", "rate", "rate_multi", "order", "active"], cols=["name", "kind", "rate", "rate_multi", "active"],
                        owner=True, search=["name"]),
        "table": dict(title="الترابيزات (كافيه / مطعم)", one="ترابيزة", icon="🪑", model=DiningTable, module="recipes",
                      fields=["name", "area", "seats", "order", "active"], cols=["name", "area", "seats", "active"], owner=True, search=["name", "area"]),
        "customer": dict(title="العملاء", one="عميل", icon="👥", model=Partner, filter=Q(kind="customer", merged_into__isnull=True),
                         fields=["name", "phone", "credit_limit", "tax_id", "aliases", "active"], cols=["name", "phone", "credit_limit", "active"],
                         perm="ledger.change_partner", new=_partner_kind("customer"), search=["name", "phone"]),
        "supplier": dict(title="الموردين", one="مورد", icon="🚚", model=Partner, filter=Q(kind="supplier", merged_into__isnull=True),
                         fields=["name", "phone", "tax_id", "aliases", "active"], cols=["name", "phone", "tax_id", "active"],
                         perm="ledger.change_partner", new=_partner_kind("supplier"), search=["name", "phone"]),
        "employee": dict(title="الموظفين (للسلف والأجور)", one="موظف", icon="🧑‍💼", model=Partner, filter=Q(kind="employee", merged_into__isnull=True),
                         fields=["name", "phone", "aliases", "active"], cols=["name", "phone", "active"],
                         perm="ledger.change_partner", new=_partner_kind("employee"), search=["name", "phone"]),
        "cashbox": dict(title="الخزن والمحافظ", one="خزنة", icon="🏦", model=CashBox, fields=["name", "kind", "allow_negative", "order", "active"],
                        cols=["name", "kind", "active"], owner=True, new=_new_cashbox, guard=_guard_cashbox, search=["name"]),
        "warehouse": dict(title="المخازن", one="مخزن", icon="🏬", model=Warehouse, module="stores", fields=["name", "code", "keeper", "active"],
                          cols=["code", "name", "keeper", "active"], owner=True, new=_new_warehouse, guard=_guard_warehouse, search=["name", "code"]),
        "device": dict(title="أجهزة الكاشير", one="جهاز كاشير", icon="🧾", model=Device, fields=["name", "active"], cols=["branch", "code", "name", "active"],
                       owner=True, no_add="shop_settings", guard=_guard_device, search=["name", "code"]),
        "expense": dict(title="أنواع المصروفات", one="نوع مصروف", icon="💸", model=Account, module="money",
                        filter=Q(is_postable=True, report="profit_loss", account_type="expense", code__startswith="3202"),
                        fields=["name", "active"], cols=["code", "name", "active"], owner=True, new=_new_expense, search=["name"]),
        "doctor": dict(title="الأطباء", one="طبيب", icon="👨‍⚕️", model=Doctor, module="clinic",
                       fields=["name", "specialty", "phone", "share_pct", "user", "order", "active"], cols=["name", "specialty", "share_pct", "user", "active"],
                       owner=True, no_add="clinic_doctors", search=["name"]),
    }
    return K


def _kind(request, kind):
    K = _kinds()
    if kind not in K:
        raise Http404
    k = dict(K[kind], key=kind)
    if k.get("module") and k["module"] not in enabled():
        raise Http404
    u = request.user
    if not (u.is_superuser or (_owner(u) if k.get("owner") else u.has_perm(k["perm"]) or _owner(u))):
        raise PermissionDenied
    return k


def kinds_for(request):
    """The kinds this user can open (for the «البيانات الأساسية» hub)."""
    out = []
    for key in _kinds():
        try:
            out.append(_kind(request, key))
        except (Http404, PermissionDenied):
            continue
    return out


def _form(k, data=None, instance=None):
    model = k["model"]
    F = forms.modelform_factory(model, fields=k["fields"])
    f = F(data, instance=instance)
    for name, fld in f.fields.items():
        if name == "reorder_level":
            fld.required = False  # checked in _check_item: required for stock items only
            fld.help_text = "أقل كمية قبل إعادة الشراء — 0 = بدون تنبيه (غير مطلوب للخدمات والوصفات)"
        if name == "category":
            fld.queryset = fld.queryset.order_by("name")
        if name == "user":  # a doctor's login: only users made as «دكتور»
            fld.queryset = fld.queryset.filter(groups__name="دكتور", is_active=True).distinct()
            fld.label_from_instance = lambda u: f"{u.first_name or u.username} ({u.username})"
        if isinstance(fld.widget, (forms.TextInput, forms.NumberInput)) and name in ("code", "barcode", "phone", "tax_id"):
            fld.widget.attrs["dir"] = "ltr"
    return f


def _qs(k):
    qs = k["model"].objects.all()
    if k.get("filter") is not None:
        qs = qs.filter(k["filter"])
    return qs


def _label(model, name):
    try:
        return model._meta.get_field(name).verbose_name
    except Exception:  # noqa: BLE001
        return name


def _value(obj, name):
    v = getattr(obj, name)
    f = obj._meta.get_field(name)
    if f.choices:
        return getattr(obj, f"get_{name}_display")()
    if isinstance(v, bool):
        return "✓" if v else "—"
    if v is None:
        return ""
    if isinstance(v, Decimal):
        return f"{v.normalize():f}"
    return v


def remove(obj, guard=None):
    """→ (deleted?, message). Never loses history: what is used is stopped, not deleted."""
    if guard:
        guard(obj)
    try:
        with transaction.atomic():
            name = str(obj)
            obj.delete()
        return True, f"حُذف «{name}»"
    except (ProtectedError, IntegrityError, models.RestrictedError):
        if hasattr(obj, "active"):
            type(obj).objects.filter(pk=obj.pk).update(active=False)
            return False, f"«{obj}» عليه حركات مسجّلة — أُوقف بدلًا من حذفه (لن يظهر مرة أخرى، وتبقى الحسابات السابقة كما هي). تعيده من «تعديل»."
        raise ValidationError(f"«{obj}» عليه حركات أو مستخدم في موضع آخر — لا يمكن حذفه")


@login_required
def hub(request):
    return render(request, "ui/manage_hub.html", dict(kinds=kinds_for(request)))


@login_required
def listing(request, kind):
    k = _kind(request, kind)
    if k.get("list_url"):
        return redirect(k["list_url"])
    model = k["model"]
    if request.method == "POST":
        if k.get("no_add"):
            raise PermissionDenied
        f = _form(k, request.POST)
        if f.is_valid():
            obj = f.save(commit=False)
            try:
                with transaction.atomic():
                    if k.get("new"):
                        k["new"](obj)
                    if k.get("check"):
                        k["check"](obj)
                    obj.save()
                    f.save_m2m()
                messages.success(request, f"تمت الإضافة {k['one']} «{obj}» ✔")
                return redirect(request.get_full_path())
            except ValidationError as e:
                f.add_error(None, e)
    else:
        f = _form(k)
    q = request.GET.get("q", "").strip()
    qs = _qs(k)
    if q:
        cond = Q()
        for name in k.get("search", []):
            cond |= Q(**{f"{name}__icontains": q})
        qs = qs.filter(cond)
    if "order" in [x.name for x in model._meta.fields]:
        qs = qs.order_by("order", "pk")
    rows = [dict(obj=o, vals=[_value(o, c) for c in k["cols"]]) for o in qs[:500]]
    if k.get("count"):
        rel, word = k["count"]
        for r in rows:
            r["count"] = getattr(r["obj"], f"{rel}_set").count()
    return render(request, "ui/manage_list.html", dict(k=k, form=f, rows=rows, q=q, heads=[_label(model, c) for c in k["cols"]],
                                                         add_url=reverse(k["no_add"]) if k.get("no_add") else None))


@login_required
def edit(request, kind, pk):
    k = _kind(request, kind)
    obj = get_object_or_404(_qs(k), pk=pk)
    back = request.GET.get("next") or (reverse(k["list_url"]) if k.get("list_url") else reverse("data_list", args=[kind]))
    if not back.startswith("/") or back.startswith("//"):
        back = "/"
    if request.method == "POST" and request.POST.get("op") == "delete":
        try:
            _, msg = remove(obj, k.get("guard"))
            messages.success(request, msg)
        except ValidationError as e:
            messages.error(request, e.messages[0])
            return redirect(request.get_full_path())
        return redirect(back)
    if request.method == "POST":
        f = _form(k, request.POST, instance=obj)
        if f.is_valid():
            o = f.save(commit=False)
            try:
                with transaction.atomic():
                    if k.get("check"):
                        k["check"](o)
                    o.save()
                    f.save_m2m()
                messages.success(request, f"تم الحفظ «{o}» ✔")
                return redirect(back)
            except ValidationError as e:
                f.add_error(None, e)
    else:
        f = _form(k, instance=obj)
    return render(request, "ui/manage_form.html", dict(k=k, form=f, obj=obj, back=back))
