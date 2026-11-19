"""Payroll (1.21): a month's payslips → one entry in the books → salaries paid → insurance and tax paid to the government.
Plus the tax drafts: VAT (monthly / quarterly), the yearly income tax (simplified regime or the normal one), payroll tax."""
import datetime as dt
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from ledger import services as L
from ledger.models import Account, JournalLine, Partner

from . import rules as R
from .models import Employee, PayRun, Payslip

Z = Decimal(0)
WAGES, ADV, PENALTY, NET_PAYABLE = "3201010101", "1203030115", "4401010101", "2301050102"
ER_INS_EXP, INS_PAY, TAX_PAY, MARTYRS_PAY = "3201010102", "2301040401", "2301040302", "2301040402"
VAT = "2301040301"


def _acc(code, name, parent_code, kind="liability", parent_name=None):
    a = Account.objects.filter(code=code).first()
    if a:
        return a
    parent = Account.objects.filter(code=parent_code).first()
    if not parent:  # «الهيئة القومية للتأمين الاجتماعي» under «حسابات دائنة أخرى»
        grand = Account.objects.get(code=parent_code[:-2])
        parent = Account.objects.create(code=parent_code, name=parent_name or name, parent=grand, level=grand.level + 1, is_postable=False,
                                        account_type=grand.account_type, normal_balance=grand.normal_balance, report=grand.report)
    expense = kind == "expense"
    return Account.objects.create(code=code, name=name, parent=parent, level=parent.level + 1, is_postable=True,
                                  account_type="expense" if expense else "liability", normal_balance="debit" if expense else "credit",
                                  report="profit_loss" if expense else "balance_sheet")


def accounts():
    """The payroll accounts (made the first time a payroll is approved)."""
    return dict(
        wages=Account.objects.get(code=WAGES),
        er_exp=_acc(ER_INS_EXP, "حصة صاحب العمل في التأمينات الاجتماعية", "32010101", "expense"),
        ins=_acc(INS_PAY, "تأمينات اجتماعية مستحقة للهيئة", "23010404", parent_name="الهيئة القومية للتأمين الاجتماعي"),
        martyrs=_acc(MARTYRS_PAY, "صندوق تكريم الشهداء — مستحق", "23010404", parent_name="الهيئة القومية للتأمين الاجتماعي"),
        tax=_acc(TAX_PAY, "ضريبة كسب العمل المستحقة", "23010403"),
        net=Account.objects.get(code=NET_PAYABLE),
        adv=Account.objects.get(code=ADV),
        penalty=Account.objects.get(code=PENALTY),
    )


def partner_of(e: Employee):
    """The employee's account (advances, salary owed) — the same «موظف» the till and the money page use."""
    if e.partner_id:
        return e.partner
    # an older «موظف» of the same name (made from the till / money page) is reused — unless another employee file has him
    p = Partner.objects.filter(kind="employee", name=e.name, merged_into__isnull=True, employee_file__isnull=True).first() or Partner.objects.create(
        kind="employee", name=e.name, phone=e.phone)
    e.partner = p
    e.save(update_fields=["partner"])
    return p


def advance_balance(e: Employee):
    if not e.partner_id:
        return Z
    r = JournalLine.objects.filter(account__code=ADV, partner_id=e.partner_id, entry__state__in=["posted", "reversed"]).aggregate(
        d=Sum("debit"), c=Sum("credit"))
    return (r["d"] or Z) - (r["c"] or Z)


def compute(slip: Payslip, y):
    """Fills the payslip's figures from its inputs (salary, allowances, bonus, absence, penalty, advance)."""
    e = slip.employee
    slip.gross = R.q2(slip.salary + slip.allowances + slip.bonus - slip.absence)
    if slip.gross < 0:
        raise ValidationError(f"{e.name}: الخصم أكبر من المرتب")
    if e.insured:
        ins = R.insurance(slip.gross, y, e.insurable_wage)
        slip.insurable, slip.emp_ins, slip.er_ins = ins["insurable"], ins["emp"], ins["er"]
    else:
        slip.insurable = slip.emp_ins = slip.er_ins = Z
    slip.tax = R.salary_tax_month(slip.gross, slip.emp_ins, y) if e.taxable else Z
    slip.martyrs = R.martyrs(slip.gross, y) if e.taxable else Z
    slip.net = R.q2(slip.gross - slip.emp_ins - slip.tax - slip.martyrs - slip.penalty - slip.advance)
    if slip.net < 0:
        raise ValidationError(f"{e.name}: الخصومات أكبر من المرتب ({slip.net:,.2f})")
    return slip


@transaction.atomic
def prepare(month, user=None):
    """The month's draft payroll: one payslip per working employee (kept if already there, new ones added)."""
    month = month.replace(day=1)
    run, _ = PayRun.objects.get_or_create(month=month, defaults=dict(by=_name(user)))
    if run.state != "draft":
        return run
    end = (month + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)
    staff = Employee.objects.filter(Q(hire_date__isnull=True) | Q(hire_date__lte=end)).filter(Q(end_date__isnull=True) | Q(end_date__gte=month),
                                                                                                 active=True)
    for e in staff:
        slip, made = Payslip.objects.get_or_create(run=run, employee=e, defaults=dict(salary=e.salary, allowances=e.allowances))
        if made:
            slip.advance = min(advance_balance(e), max(e.gross, Z)) if advance_balance(e) > 0 else Z
        compute(slip, month.year)
        slip.save()
    return run


def _name(user):
    return (getattr(user, "first_name", "") or getattr(user, "username", "") or "")[:100] if user else ""


@transaction.atomic
def update_slip(slip, data):
    if slip.run.state != "draft":
        raise ValidationError("اعتُمد المسير — لا يمكن تعديله")
    for f in ("salary", "allowances", "bonus", "absence", "penalty", "advance"):
        if f in data:
            v = Decimal(str(data[f] or 0))
            if v < 0:
                raise ValidationError("لا تُقبل أرقام سالبة")
            setattr(slip, f, v)
    if slip.advance > advance_balance(slip.employee):
        raise ValidationError(f"{slip.employee.name}: خصم السلفة أكبر من السلفة المستحقة عليه ({advance_balance(slip.employee):,.2f})")
    compute(slip, slip.run.month.year)
    slip.save()
    return slip


def totals(run):
    keys = ("gross", "emp_ins", "er_ins", "tax", "martyrs", "penalty", "advance", "net")
    agg = run.slips.aggregate(**{k: Sum(k) for k in keys})
    return {k: agg[k] or Z for k in keys}


@transaction.atomic
def approve(run, user=None):
    """The month's payroll into the books: wages + the employer's insurance as expenses; what is owed to the insurance
    authority, the tax authority, the martyrs fund and each employee as liabilities; advances and penalties settled."""
    run = PayRun.objects.select_for_update().get(pk=run.pk)
    if run.state != "draft":
        raise ValidationError("اعتُمد هذا المسير من قبل")
    slips = list(run.slips.select_related("employee"))
    if not slips:
        raise ValidationError("لا يوجد موظفون في المسير")
    A = accounts()
    end = (run.month + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)
    lines = []
    for s in slips:
        p = partner_of(s.employee)
        if s.gross:
            lines.append(dict(account=A["wages"], debit=s.gross, partner=p, description=f"مرتب {s.employee.name} {run.month:%m/%Y}"))
        if s.er_ins:
            lines.append(dict(account=A["er_exp"], debit=s.er_ins, description=f"حصة صاحب العمل — {s.employee.name}"))
        if s.emp_ins + s.er_ins:
            lines.append(dict(account=A["ins"], credit=s.emp_ins + s.er_ins, description=f"تأمينات {s.employee.name}"))
        if s.tax:
            lines.append(dict(account=A["tax"], credit=s.tax, description=f"كسب عمل {s.employee.name}"))
        if s.martyrs:
            lines.append(dict(account=A["martyrs"], credit=s.martyrs, description=f"صندوق الشهداء {s.employee.name}"))
        if s.advance:
            lines.append(dict(account=A["adv"], credit=s.advance, partner=p, description=f"خصم سلفة {s.employee.name}"))
        if s.penalty:
            lines.append(dict(account=A["penalty"], credit=s.penalty, description=f"جزاءات {s.employee.name}"))
        if s.net:
            lines.append(dict(account=A["net"], credit=s.net, partner=p, description=f"صافي مرتب {s.employee.name}"))
    run.entry = L.create_entry("JV", end, f"مرتبات شهر {run.month:%m/%Y}", lines, source=f"staff.payrun:{run.pk}",
                               user=user if user and getattr(user, "is_authenticated", False) else None)
    run.state, run.by = "approved", _name(user)
    run.save(update_fields=["entry", "state", "by"])
    return run


@transaction.atomic
def pay(run, box, user=None, date=None):
    """The net salaries out of a cash box / wallet / bank."""
    run = PayRun.objects.select_for_update().get(pk=run.pk)
    if run.state != "approved":
        raise ValidationError("اعتمد المسير الأول" if run.state == "draft" else "صُرفت المرتبات من قبل")
    A = accounts()
    slips = [s for s in run.slips.select_related("employee") if s.net]
    total = sum((s.net for s in slips), Z)
    lines = [dict(account=A["net"], debit=s.net, partner=partner_of(s.employee), description=f"صرف مرتب {s.employee.name}") for s in slips]
    lines.append(dict(account=box.account, credit=total, description=f"صرف مرتبات {run.month:%m/%Y}"))
    run.pay_entry = L.create_entry("JV", date or timezone.localdate(), f"صرف مرتبات {run.month:%m/%Y}", lines, source=f"staff.pay:{run.pk}",
                                   user=user if user and getattr(user, "is_authenticated", False) else None)
    run.state = "paid"
    run.save(update_fields=["pay_entry", "state"])
    return run


def owed(code):
    r = JournalLine.objects.filter(account__code=code, entry__state__in=["posted", "reversed"]).aggregate(d=Sum("debit"), c=Sum("credit"))
    return (r["c"] or Z) - (r["d"] or Z)


@transaction.atomic
def pay_authority(what, amount, box, user=None, ref=""):
    """Paying the insurance authority / the tax authority / the martyrs fund what the payroll owes them."""
    code, label = {"ins": (INS_PAY, "سداد التأمينات الاجتماعية"), "tax": (TAX_PAY, "سداد ضريبة كسب العمل"),
                   "martyrs": (MARTYRS_PAY, "سداد صندوق الشهداء"), "vat": (VAT, "سداد ضريبة القيمة المضافة")}[what]
    amount = R.q2(amount)
    if amount <= 0:
        raise ValidationError("اكتب المبلغ")
    if amount > owed(code):
        raise ValidationError(f"المبلغ أكبر من المستحق ({owed(code):,.2f})")
    return L.create_entry("JV", timezone.localdate(), f"{label} {ref}".strip(), [
        dict(account=Account.objects.get(code=code), debit=amount, description=label),
        dict(account=box.account, credit=amount, description=label)], source=f"staff.authority:{what}",
        user=user if user and getattr(user, "is_authenticated", False) else None)


# ------------------------------------------------------------------ tax drafts
def _pl(d1, d2):
    """Revenue and expenses of the period from the books (posted entries)."""
    base = JournalLine.objects.filter(entry__state__in=["posted", "reversed"], entry__date__gte=d1, entry__date__lte=d2,
                                      account__report="profit_loss")
    rev = base.filter(account__account_type="revenue").aggregate(d=Sum("debit"), c=Sum("credit"))
    exp = base.filter(account__account_type="expense").aggregate(d=Sum("debit"), c=Sum("credit"))
    revenue = (rev["c"] or Z) - (rev["d"] or Z)
    expenses = (exp["d"] or Z) - (exp["c"] or Z)
    return revenue, expenses


def turnover(d1, d2):
    """Sales of the period (cash register, clinic, hall) before VAT, returns taken off."""
    from pos.models import Order

    sales = Order.objects.filter(created_at_device__date__gte=d1, created_at_device__date__lte=d2, cancelled=False)
    out, vat = Z, Z
    for ret, t, v in ((False, "t", "v"), (True, "t", "v")):
        agg = sales.filter(is_return=ret).aggregate(t=Sum("total"), v=Sum("vat"))
        sign = -1 if ret else 1
        out += sign * ((agg["t"] or Z) - (agg["v"] or Z))
        vat += sign * (agg["v"] or Z)
    return R.q2(out), R.q2(vat)


def vat_return(d1, d2):
    """VAT draft: sales by rate (taxable / zero / exempt), output VAT, input VAT booked on purchases, what is due."""
    from pos.models import OrderLine

    rows = {}
    for ln in OrderLine.objects.filter(order__created_at_device__date__gte=d1, order__created_at_device__date__lte=d2,
                                       order__cancelled=False).select_related("order", "item"):
        rate = ln.item.vat_rate if ln.item.vat_rate is not None else None
        rows.setdefault(rate, Z)
        rows[rate] += (ln.qty * ln.price - (ln.discount or Z)) * (-1 if getattr(ln.order, "is_return", False) else 1)
    from core.models import CompanySettings

    s = CompanySettings.get()
    out = []
    for rate, amt in sorted(rows.items(), key=lambda kv: (kv[0] is None, kv[0] or 0)):
        r = s.default_vat_rate if rate is None else rate
        base = amt / (1 + r / 100) if (s.pos_prices_include_vat and r) else amt
        out.append(dict(rate=r, sales=R.q2(base), vat=R.q2(base * r / 100)))
    j = JournalLine.objects.filter(account__code=VAT, entry__state__in=["posted", "reversed"], entry__date__gte=d1, entry__date__lte=d2)
    agg = j.aggregate(d=Sum("debit"), c=Sum("credit"))
    booked_out, booked_in = agg["c"] or Z, agg["d"] or Z
    calc_out = sum((r["vat"] for r in out), Z)
    return dict(rows=out, output=calc_out, booked_output=booked_out, input=booked_in, due=R.q2(max(booked_out or calc_out, Z) - booked_in),
                taxable=sum((r["sales"] for r in out if r["rate"]), Z), zero=sum((r["sales"] for r in out if not r["rate"]), Z))


def income_tax(y, regime, legal_form="individual"):
    """The year's income tax draft. Simplified: a rate on the whole turnover by band. Normal: on the book profit
    (progressive brackets for a person, 22.5% for a company)."""
    d1, d2 = dt.date(y, 1, 1), dt.date(y, 12, 31)
    revenue, expenses = _pl(d1, d2)
    sales, _ = turnover(d1, d2)
    profit = revenue - expenses
    out = dict(year=y, revenue=R.q2(revenue), expenses=R.q2(expenses), profit=R.q2(profit), turnover=sales, regime=regime, legal_form=legal_form)
    if regime == "simplified":
        rate = R.simplified_rate(sales)
        out.update(rate=rate, tax=R.q2(sales * rate / 100) if rate is not None else None, eligible=rate is not None)
    elif legal_form == "company":
        out.update(rate=R.COMPANY_RATE, tax=R.q2(max(profit, Z) * R.COMPANY_RATE / 100))
    else:
        out.update(rate=None, tax=R.progressive(max(profit, Z)))
    return out


def payroll_summary(y):
    """Per month: gross, insurance (both shares), salary tax, martyrs fund — for the payroll tax / insurance forms."""
    rows = []
    for run in PayRun.objects.filter(month__year=y).exclude(state="draft").order_by("month"):
        t = totals(run)
        rows.append(dict(month=run.month, n=run.slips.count(), **t))
    keys = ("gross", "emp_ins", "er_ins", "tax", "martyrs", "net")
    return dict(rows=rows, total={k: sum((r[k] for r in rows), Z) for k in keys})
