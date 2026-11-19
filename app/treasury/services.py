from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum

from core import audit
from core.models import CompanySettings
from core.middleware import current_user
from ledger import services as L
from ledger.models import ZERO, JournalLine

from .models import CashBox, DayClose, Voucher

JOURNAL = {"receipt": "RV", "payment": "PV", "transfer": "TR"}


def cashbox_balance(cashbox: CashBox, upto=None, in_currency=False):
    qs = JournalLine.objects.filter(account=cashbox.account, entry__state__in=["posted", "reversed"])
    if upto:
        qs = qs.filter(entry__date__lte=upto)
    if in_currency and cashbox.currency != "EGP":
        tot = ZERO
        for ac, d, c in qs.values_list("amount_currency", "debit", "credit"):
            tot += abs(ac or 0) * (1 if d > 0 else -1)
        return tot
    agg = qs.aggregate(d=Sum("debit"), c=Sum("credit"))
    return (agg["d"] or ZERO) - (agg["c"] or ZERO)


def last_closed_day():
    d = DayClose.objects.order_by("-date").first()
    return d.date if d else None


def _fx(voucher, amount):
    """Return EGP amount and currency fields for a voucher line amount."""
    if voucher.currency == "EGP":
        return L.q2(amount), {"currency": "EGP"}
    egp = L.q2(amount * voucher.rate)
    return egp, {"currency": voucher.currency, "amount_currency": L.q2(amount), "rate": voucher.rate}


@transaction.atomic
def post_voucher(voucher: Voucher, user=None, skip_lock=False, skip_balance=False, number=None):
    voucher = Voucher.objects.select_for_update().get(pk=voucher.pk)
    if voucher.state != "draft":
        raise ValidationError("السند مرحّل بالفعل")
    lcd = last_closed_day()
    if not skip_lock and lcd and voucher.date <= lcd:
        raise ValidationError(f"اليوم {voucher.date} مقفول (الأيام مقفولة حتى {lcd})")
    box = voucher.cashbox
    if voucher.currency != box.currency:
        raise ValidationError(f"عملة السند ({voucher.currency}) غير عملة الخزينة ({box.currency})")
    egp_total, fx = _fx(voucher, voucher.amount)
    lines = []
    desc = voucher.description
    if voucher.kind == "transfer":
        if not voucher.to_cashbox:
            raise ValidationError("حدد الخزينة/البنك المحوَّل إليه")
        if voucher.to_cashbox_id == voucher.cashbox_id:
            raise ValidationError("لا يمكن التحويل لنفس الخزينة")
        to_fx = dict(fx)
        if voucher.to_cashbox.currency != voucher.currency:
            raise ValidationError("التحويل بين عملتين مختلفتين يتم بسندين (صرف وقبض) بسعر الصرف")
        lines.append(dict(account=voucher.to_cashbox.account, debit=egp_total, description=desc, **to_fx))
        lines.append(dict(account=box.account, credit=egp_total, description=desc, **fx))
    else:
        vlines = list(voucher.lines.select_related("account"))
        if not vlines:
            raise ValidationError("يجب أن يحتوي السند على سطر واحد على الأقل (الحساب المقابل)")
        if L.q2(sum((l.amount for l in vlines), ZERO)) != L.q2(voucher.amount):
            raise ValidationError("مجموع السطور لا يساوي مبلغ السند")
        from accruals import services as ACC

        for vl in vlines:
            if vl.accrual_id:
                ACC.check_payment_line(vl, voucher)
        egp_sum = ZERO
        for i, vl in enumerate(vlines):
            egp, lfx = _fx(voucher, vl.amount)
            if i == len(vlines) - 1:
                egp = egp_total - egp_sum  # rounding to last line
            egp_sum += egp
            side = "debit" if voucher.kind == "payment" else "credit"
            # counter-account currency: if account does not allow the foreign currency, book it in EGP
            line_fx = lfx if voucher.currency in vl.account.currency_list else {"currency": "EGP"}
            lines.append(dict(account=vl.account, partner=vl.partner or (vl.accrual.partner if vl.accrual_id else None) or voucher.partner, description=vl.description or desc,
                              dim1=vl.dim1, dim2=vl.dim2, dim3=vl.dim3, **{side: egp}, **line_fx))
        side = "credit" if voucher.kind == "payment" else "debit"
        lines.append(dict(account=box.account, description=desc, **{side: egp_total}, **fx))
    # no negative cash
    settings_ = CompanySettings.get()
    if voucher.kind in ("payment", "transfer") and not (skip_balance or box.allow_negative or settings_.allow_negative_cash):
        bal = cashbox_balance(box, in_currency=True)
        if bal < voucher.amount:
            raise ValidationError(f"رصيد {box.name} ({bal}) لا يكفي لصرف {voucher.amount}")
    entry = L.create_entry(JOURNAL[voucher.kind], voucher.date, f"{voucher.get_kind_display()}: {desc}", lines,
                           source=f"treasury.voucher:{voucher.pk}", legacy_ref=voucher.legacy_number, user=user, number=number)
    voucher.entry = entry
    voucher.number = entry.number
    voucher.state = "posted"
    voucher.save(update_fields=["entry", "number", "state"])
    _refresh_accruals(voucher)
    return voucher


def _refresh_accruals(voucher):
    from accruals import services as ACC

    for vl in voucher.lines.exclude(accrual=None).select_related("accrual"):
        ACC.refresh(vl.accrual)


@transaction.atomic
def cancel_voucher(voucher: Voucher, reason, user=None):
    if voucher.state != "posted":
        raise ValidationError("لا يمكن إلغاء إلا سند مرحّل")
    L.reverse(voucher.entry, reason=f"إلغاء {voucher}: {reason}", user=user)
    voucher.state = "cancelled"
    voucher.save(update_fields=["state"])
    _refresh_accruals(voucher)
    audit.log("cancel", voucher, summary=f"إلغاء {voucher}: {reason}")


@transaction.atomic
def close_day(date, counts=None, notes="", user=None, diff_account=None):
    """counts: {cashbox_id: counted_amount}. Differences post an adjustment entry against diff_account."""
    from .models import DayCloseCount

    if DayClose.objects.filter(date__gte=date).exists():
        raise ValidationError("هذا اليوم أو يوم بعده مُقفل بالفعل")
    if Voucher.objects.filter(date__lte=date, state="draft").exists():
        raise ValidationError("توجد سندات مسودة حتى هذا اليوم — رحّلها أو احذفها أولًا")
    dc = DayClose.objects.create(date=date, closed_by=user or current_user(), notes=notes)
    adj_lines = []
    for box in CashBox.objects.filter(active=True, kind__in=["cash", "wallet"]):
        book = cashbox_balance(box, upto=date)
        counted = (counts or {}).get(box.pk, book)
        DayCloseCount.objects.create(day=dc, cashbox=box, book_balance=book, counted=counted)
        diff = L.q2(counted - book)
        if diff and diff_account is not None:
            if diff > 0:
                adj_lines += [dict(account=box.account, debit=diff), dict(account=diff_account, credit=diff)]
            else:
                adj_lines += [dict(account=diff_account, debit=-diff), dict(account=box.account, credit=-diff)]
    if adj_lines:
        dc.adjustment = L.create_entry("JV", date, f"فروق جرد الخزينة يوم {date}", adj_lines, source=f"treasury.dayclose:{dc.pk}", user=user)
        dc.save(update_fields=["adjustment"])
    audit.log("close_day", dc, summary=f"إقفال يوم {date}")
    return dc
