import datetime as dt
from decimal import Decimal

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.forms import inlineformset_factory
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from ledger.models import Account, DimensionValue
from ui.forms import DateInput, lookup_field

from . import services as S
from .models import Accrual, AccrualLine


class AccrualForm(forms.ModelForm):
    class Meta:
        model = Accrual
        fields = ["date", "partner", "description", "liability_account", "notes"]
        widgets = {"date": DateInput(), "notes": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        lookup_field(self.fields["partner"], "partner", "المستشار / المؤجر / المورد")
        self.fields["liability_account"].queryset = Account.objects.filter(is_postable=True, code__startswith="2301").order_by("code")
        self.fields["liability_account"].initial = Account.objects.filter(code="2301050104").first()


class LineForm(forms.ModelForm):
    class Meta:
        model = AccrualLine
        fields = ["account", "dim1", "dim2", "amount", "description"]

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        lookup_field(self.fields["account"], "account", "حساب المصروف")
        lookup_field(self.fields["dim1"], "cc", "مركز التكلفة")
        lookup_field(self.fields["dim2"], "equipment", "المعدة")
        self.fields["amount"].widget.attrs.update({"data-amount": "1", "inputmode": "decimal"})


Lines = inlineformset_factory(Accrual, AccrualLine, form=LineForm, extra=1, can_delete=True)


def _errs(request, e):
    for m in getattr(e, "messages", [str(e)]):
        messages.error(request, m)


@login_required
def accrual_list(request):
    if request.method == "POST" and "generate" in request.POST:
        try:
            y, m = map(int, request.POST["month"].split("-"))
            made = S.generate_recurring(y, m, user=request.user)
            messages.success(request, f"أُنشئ {len(made)} استحقاق ثابت لشهر {m}/{y}")
        except (ValidationError, ValueError, KeyError) as e:
            _errs(request, e)
        return redirect("accrual_list")
    st = request.GET.get("state", "open")
    qs = Accrual.objects.select_related("partner", "liability_account")
    if st:
        qs = qs.filter(state=st)
    rows = list(qs[:300])
    total = sum((a.remaining for a in rows if a.state == "open"), Decimal(0))
    return render(request, "accruals/list.html", dict(rows=rows, st=st, total=total, states=Accrual.STATES,
                                                       month=timezone.localdate().strftime("%Y-%m")))


@login_required
def accrual_new(request):
    a = Accrual(date=timezone.localdate(), source="manual")
    form = AccrualForm(request.POST or None, instance=a)
    lines = Lines(request.POST or None, instance=a, prefix="lines")
    if request.method == "POST" and form.is_valid() and lines.is_valid():
        try:
            with transaction.atomic():
                obj = form.save(commit=False)
                obj.month = obj.date.replace(day=1)
                obj.save()
                lines.instance = obj
                lines.save()
                if "post" in request.POST:
                    S.post_accrual(obj, user=request.user)
            messages.success(request, "تم تسجيل الاستحقاق وإثبات الالتزام" if "post" in request.POST else "تم الحفظ كمسودة")
            return redirect("accrual_detail", pk=obj.pk)
        except ValidationError as e:
            _errs(request, e)
    return render(request, "accruals/form.html", dict(form=form, lines=lines))


@login_required
def accrual_detail(request, pk):
    a = get_object_or_404(Accrual.objects.select_related("partner", "liability_account", "entry"), pk=pk)
    if request.method == "POST" and "post" in request.POST and a.state == "draft":
        try:
            S.post_accrual(a, user=request.user)
            messages.success(request, "رُحّل")
        except ValidationError as e:
            _errs(request, e)
        return redirect("accrual_detail", pk=pk)
    if request.method == "POST" and "settle" in request.POST and a.state == "open":
        try:
            S.settle_by_reclass(a, Decimal(request.POST["amount"].replace(",", ".")), Account.objects.get(pk=request.POST["account"]),
                                dt.date.fromisoformat(request.POST["date"]),
                                dim1=DimensionValue.objects.filter(pk=request.POST.get("dim1") or 0).first(),
                                note=request.POST.get("note", ""), user=request.user)
            messages.success(request, "تم إنشاء قيد الربط")
        except (ValidationError, ValueError, KeyError, Account.DoesNotExist) as e:
            _errs(request, e)
        return redirect("accrual_detail", pk=pk)
    pays = a.payments.select_related("voucher", "voucher__cashbox").order_by("voucher__date")
    from ui.forms import LookupWidget

    return render(request, "accruals/detail.html", dict(
        a=a, pays=pays, lines=a.lines.select_related("account", "dim1"), today=timezone.localdate().isoformat(),
        sets=a.settlements.select_related("account", "entry"),
        w_acc=LookupWidget("account", "الحساب الذي صُرف عليه").render("account", None),
        w_cc=LookupWidget("cc", "مركز التكلفة").render("dim1", None)))
