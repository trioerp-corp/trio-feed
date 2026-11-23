"""Employees, payroll and the tax drafts (1.21) — the owner's pages."""
import datetime as dt
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from core.models import CompanySettings
from treasury.models import CashBox

from . import rules as R
from . import services as S
from .models import Employee, PayRun, Payslip


def _owner(view):
    @login_required
    def wrap(request, *a, **k):
        from pos.voids import is_head

        if not is_head(request.user):  # payroll of every branch: the owner's
            raise PermissionDenied("هذه الصفحة لصاحب المحل")
        return view(request, *a, **k)

    wrap.__name__ = view.__name__
    return wrap


def _dec(v, label="", default=Decimal(0)):
    t = str(v or "").strip().replace(",", "").translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    if not t:
        return default
    try:
        return Decimal(t)
    except InvalidOperation:
        raise ValidationError(f"{label}: «{v}» ليس رقمًا")


def _date(v):
    try:
        return dt.date.fromisoformat(v) if v else None
    except ValueError:
        raise ValidationError(f"تاريخ غير صحيح: {v}")


def _fill(e, P):
    e.name = P.get("name", "").strip()[:120]
    if not e.name:
        raise ValidationError("اكتب اسم الموظف")
    nid = P.get("national_id", "").strip()
    if nid and (not nid.isdigit() or len(nid) != 14):
        raise ValidationError("الرقم القومي 14 رقمًا")
    e.national_id, e.job, e.phone = nid, P.get("job", "").strip()[:80], P.get("phone", "").strip()[:40]
    e.salary, e.allowances = _dec(P.get("salary"), "المرتب"), _dec(P.get("allowances"), "البدلات")
    e.insurable_wage = _dec(P.get("insurable_wage"), "أجر الاشتراك", None)
    e.insured, e.taxable = P.get("insured") == "1", P.get("taxable") == "1"
    e.insurance_no, e.pay_to, e.notes = P.get("insurance_no", "").strip()[:30], P.get("pay_to", "").strip()[:80], P.get("notes", "").strip()[:300]
    e.hire_date, e.end_date = _date(P.get("hire_date")), _date(P.get("end_date"))
    e.active = P.get("active", "1") == "1"
    if e.salary < 0 or e.allowances < 0:
        raise ValidationError("لا تُقبل أرقام سالبة")
    e.save()
    if e.partner_id and e.partner.name != e.name:
        e.partner.name = e.name
        e.partner.save(update_fields=["name"])
    return e


@_owner
def staff_list(request):
    if request.method == "POST":
        try:
            from django.db import transaction

            with transaction.atomic():
                e = _fill(Employee(), request.POST)
                S.partner_of(e)
            messages.success(request, f"تمت الإضافة {e.name} ✔")
        except ValidationError as err:
            messages.error(request, err.messages[0])
        return redirect("staff_list")
    y = timezone.localdate().year
    rows = []
    for e in Employee.objects.all():
        ins = R.insurance(e.gross, y, e.insurable_wage) if e.insured else dict(emp=0, er=0, insurable=0)
        tax = R.salary_tax_month(e.gross, ins["emp"], y) if e.taxable else 0
        rows.append(dict(e=e, ins=ins, tax=tax, net=e.gross - ins["emp"] - tax - (R.martyrs(e.gross, y) if e.taxable else 0),
                         adv=S.advance_balance(e)))
    return render(request, "staff/list.html", dict(rows=rows, f=R.year(y)))


@_owner
def staff_edit(request, pk):
    e = get_object_or_404(Employee, pk=pk)
    if request.method == "POST":
        try:
            if request.POST.get("op") == "delete":
                if e.slips.exists():
                    e.active, e.end_date = False, e.end_date or timezone.localdate()
                    e.save(update_fields=["active", "end_date"])
                    messages.success(request, f"{e.name} له مرتبات مسجّلة — سُجّل أنه ترك العمل بدلًا من حذفه")
                else:
                    e.delete()
                    messages.success(request, "حُذف")
                return redirect("staff_list")
            _fill(e, request.POST)
            messages.success(request, "تم الحفظ ✔")
            return redirect("staff_list")
        except ValidationError as err:
            messages.error(request, err.messages[0])
    return render(request, "staff/edit.html", dict(e=e))


def _month(request):
    try:
        return dt.date.fromisoformat((request.GET.get("m") or request.POST.get("m") or "") + "-01")
    except ValueError:
        return timezone.localdate().replace(day=1)


@_owner
def payroll(request):
    m = _month(request)
    run = PayRun.objects.filter(month=m).first()
    if request.method == "POST":
        a = request.POST.get("op")
        try:
            if a == "prepare":
                run = S.prepare(m, request.user)
                messages.success(request, "تم إنشاء مسير الشهر — راجع الإضافي والخصومات واعتمده")
            elif a == "save" and run:
                for slip in run.slips.all():
                    S.update_slip(slip, {f: request.POST.get(f"{f}_{slip.pk}") for f in ("bonus", "absence", "penalty", "advance")
                                         if f"{f}_{slip.pk}" in request.POST})
                messages.success(request, "أُعيد الحساب ✔")
            elif a == "approve" and run:
                S.approve(run, request.user)
                messages.success(request, "اعتُمد المسير وسُجّل في الحسابات ✔ — اصرف المرتبات متى شئت")
            elif a == "pay" and run:
                S.pay(run, get_object_or_404(CashBox, pk=request.POST.get("box"), active=True), request.user)
                messages.success(request, "صُرفت المرتبات ✔")
        except ValidationError as err:
            messages.error(request, err.messages[0])
        return redirect(f"{request.path}?m={m:%Y-%m}")
    slips = list(run.slips.select_related("employee")) if run else []
    return render(request, "staff/payroll.html", dict(
        m=m, run=run, slips=slips, t=S.totals(run) if run else None, boxes=CashBox.objects.filter(active=True).order_by("order", "pk"),
        prev=(m - dt.timedelta(days=1)).replace(day=1), next=(m + dt.timedelta(days=32)).replace(day=1), f=R.year(m.year),
        employees=Employee.objects.filter(active=True).count(), printing=bool(request.GET.get("print"))))


@_owner
def tax_home(request):
    s = CompanySettings.get()
    today = timezone.localdate()
    if request.method == "POST":
        try:
            S.pay_authority(request.POST.get("what"), _dec(request.POST.get("amount"), "المبلغ"),
                            get_object_or_404(CashBox, pk=request.POST.get("box"), active=True), request.user, request.POST.get("ref", ""))
            messages.success(request, "تم تسجيل السداد ✔")
        except (ValidationError, KeyError) as err:
            messages.error(request, err.messages[0] if isinstance(err, ValidationError) else "اختيار غير صحيح")
        return redirect(request.get_full_path())
    quarterly = s.tax_regime == "simplified"
    try:
        p = dt.date.fromisoformat((request.GET.get("p") or "") + "-01")
    except ValueError:
        last = (today.replace(day=1) - dt.timedelta(days=1)).replace(day=1)
        p = last.replace(month=((last.month - 1) // 3) * 3 + 1) if quarterly else last
    months = 3 if quarterly else 1
    d1 = p
    m_last = d1.month + months - 1
    first_after = dt.date(d1.year + (m_last // 12), m_last % 12 + 1, 1)
    d2 = first_after - dt.timedelta(days=1)
    y = int(request.GET.get("y") or (today.year - 1 if today.month < 5 else today.year))
    form = (s.onboarding or {}).get("legal_form", "individual")
    if request.GET.get("form") in ("individual", "company"):
        form = request.GET["form"]
        ob = dict(s.onboarding or {})
        ob["legal_form"] = form
        s.onboarding = ob
        s.save(update_fields=["onboarding"])
    return render(request, "staff/tax.html", dict(
        s=s, quarterly=quarterly, d1=d1, d2=d2, vat=S.vat_return(d1, d2) if s.vat_registered else None,
        income=S.income_tax(y, s.tax_regime if s.tax_regime != "none" else "normal", form), y=y, form=form,
        payroll=S.payroll_summary(y), owed=dict(ins=S.owed(S.INS_PAY), tax=S.owed(S.TAX_PAY), martyrs=S.owed(S.MARTYRS_PAY), vat=S.owed(S.VAT)),
        boxes=CashBox.objects.filter(active=True).order_by("order", "pk"), years=list(range(today.year, today.year - 4, -1)),
        brackets=R.BRACKETS, simplified=R.SIMPLIFIED, f=R.year(y)))
