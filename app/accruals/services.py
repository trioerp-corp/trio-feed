import calendar
import datetime as dt
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from ledger import services as L

from .models import Accrual, AccrualLine, AccrualSettlement, RecurringAccrual

Z = Decimal("0")


@transaction.atomic
def post_accrual(acc: Accrual, user=None):
    acc = Accrual.objects.select_for_update().get(pk=acc.pk)
    if acc.state != "draft":
        raise ValidationError("الاستحقاق مترحّل بالفعل")
    lines = list(acc.lines.select_related("account"))
    if not lines:
        raise ValidationError("حدد حساب المصروف والمبلغ")
    total = sum((ln.amount for ln in lines), Z)
    if total <= 0:
        raise ValidationError("يجب أن يكون المبلغ أكبر من صفر")
    gl = [dict(account=ln.account, debit=ln.amount, dim1=ln.dim1, dim2=ln.dim2, description=ln.description or acc.description) for ln in lines]
    gl.append(dict(account=acc.liability_account, credit=total, partner=acc.partner, description=acc.description))
    entry = L.create_entry("JV", acc.date, f"استحقاق: {acc.description}", gl, source=f"accruals.accrual:{acc.pk}", user=user)
    acc.amount, acc.entry, acc.number, acc.state = total, entry, entry.number, "open"
    acc.save(update_fields=["amount", "entry", "number", "state"])
    return acc


def refresh(acc: Accrual):
    if acc.state in ("open", "closed"):
        new = "closed" if acc.remaining <= 0 else "open"
        if new != acc.state:
            acc.state = new
            acc.save(update_fields=["state"])


def check_payment_line(line, voucher):
    """Called before a treasury voucher is posted: a line that pays an accrual books on its liability."""
    acc = line.accrual
    if voucher.kind != "payment":
        raise ValidationError("يتم سداد الاستحقاق بسند صرف")
    if acc.state != "open":
        raise ValidationError(f"الاستحقاق {acc.number} غير مفتوح ({acc.get_state_display()})")
    if line.account_id != acc.liability_account_id:
        raise ValidationError(f"سطر سداد {acc.number} يجب أن يكون على حساب {acc.liability_account}")
    others = sum((ln.amount for ln in voucher.lines.all() if ln.accrual_id == acc.pk and ln.pk != line.pk), Z)
    if line.amount + others > acc.remaining:
        raise ValidationError(f"المبلغ أكبر من الباقي على الاستحقاق {acc.number} ({acc.remaining:,.2f})")


def month_end(year, month):
    return dt.date(year, month, calendar.monthrange(year, month)[1])


@transaction.atomic
def generate_recurring(year, month, user=None):
    first, made = dt.date(year, month, 1), []
    for t in RecurringAccrual.objects.filter(active=True, start__lte=month_end(year, month)):
        if t.end and t.end < first or Accrual.objects.filter(template=t, month=first).exists():
            continue
        a = Accrual.objects.create(date=month_end(year, month), month=first, partner=t.partner, description=f"{t.name} عن شهر {month}/{year}",
                                   liability_account=t.liability_account, source="recurring", template=t)
        AccrualLine.objects.create(accrual=a, account=t.account, dim1=t.dim1, amount=t.amount)
        made.append(post_accrual(a, user=user))
    return made


@transaction.atomic
def settle_by_reclass(acc: Accrual, amount, account, date, dim1=None, note="", user=None):
    """The payment was booked on an expense (e.g. salaries paid on «عمالة»): move it onto the accrual."""
    acc = Accrual.objects.select_for_update().get(pk=acc.pk)
    amount = L.q2(amount)
    if acc.state != "open":
        raise ValidationError("الاستحقاق غير مفتوح")
    if amount <= 0 or amount > acc.remaining:
        raise ValidationError(f"يجب أن يكون المبلغ بين صفر والمتبقي ({acc.remaining:,.2f})")
    desc = f"ربط صرف مسجّل على {account.name} باستحقاق: {acc.description}"
    entry = L.create_entry("JV", date, desc, [
        dict(account=acc.liability_account, debit=amount, partner=acc.partner, description=note or desc),
        dict(account=account, credit=amount, dim1=dim1, description=note or desc),
    ], source=f"accruals.settle:{acc.pk}", user=user)
    AccrualSettlement.objects.create(accrual=acc, date=date, amount=amount, account=account, entry=entry, note=note[:300])
    refresh(acc)
    return entry
