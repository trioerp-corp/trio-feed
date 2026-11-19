"""Owner drawings, running expenses, wages/advances and supplier payments — from the drawer or the owner's own pocket.

Every movement is a posted journal entry:
  drawing   Dr جاري صاحب المحل            / Cr الخزينة
  deposit   Dr الخزينة                     / Cr جاري صاحب المحل
  expense   Dr بند المصروف (مركز الفرع)    / Cr الخزينة أو جاري صاحب المحل
  wage      Dr مرتبات وأجور (الإجمالي)     / Cr سلف العاملين (خصم سلفة) / Cr إيرادات أخرى (جزاء) / Cr الخزينة أو جاري صاحب المحل (الصافي)
  advance   Dr سلف العاملين                 / Cr الخزينة أو جاري صاحب المحل
  supplier  Dr الموردين                     / Cr الخزينة أو جاري صاحب المحل
The owner's balance is the «جاري صاحب المحل» account: credit = the shop owes him (he paid for it), debit = he drew more.
"""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from ledger import services as L
from ledger.models import Account, Partner

from .models import CashMove

Z = Decimal("0")
OWNER, WAGES, ADV, PENALTY, SUPPLIERS = "2304010101", "3201010101", "1203030115", "4401010101", "2301020101"
CUSTOMERS = "1203010101"  # credit customers (patients and insurance companies too)


def A(code):
    return Account.objects.get(code=code)


def expense_accounts():
    return Account.objects.filter(is_postable=True, active=True, report="profit_loss", account_type="expense").exclude(
        code__in=["3101010101", "3801010102", "3801010103", WAGES]).order_by("code")


@transaction.atomic
def post(kind, amount, date, *, cash_account=None, from_owner=False, partner=None, account=None, deduct_advance=Z, deduct_penalty=Z,
         note="", uid, shift=None, cost_center=None, by="", user=None, strict_partner=False):
    if CashMove.objects.filter(uid=uid).exists():
        return CashMove.objects.get(uid=uid)
    amount, deduct_advance, deduct_penalty = (L.q2(Decimal(str(x or 0))) for x in (amount, deduct_advance, deduct_penalty))
    if amount <= 0:
        raise ValidationError("يجب أن يكون المبلغ أكبر من صفر")
    if kind in ("drawing", "deposit") and from_owner:
        raise ValidationError("المسحوبات والإيداع تكون من/إلى الخزينة")
    if kind in ("wage", "advance", "supplier", "doctor", "collect") and not partner:
        raise ValidationError("اختر " + {"supplier": "المورد", "doctor": "الطبيب", "collect": "العميل"}.get(kind, "الموظف"))
    if kind == "expense" and not account:
        raise ValidationError("اختر بند المصروف")
    if kind in ("drawing", "deposit"):
        partner = owner_partner(partner, strict_partner)
    source = A(OWNER) if from_owner else cash_account
    if source is None:
        raise ValidationError("حدد الخزينة")
    label = dict(CashMove.KINDS)[kind]
    desc = f"{label}{' — ' + partner.name if partner else ''}{' — ' + note if note else ''}"
    lines, out = [], amount
    if kind == "drawing":
        lines = [dict(account=A(OWNER), debit=amount, partner=partner), dict(account=source, credit=amount)]
    elif kind == "deposit":
        lines, out = [dict(account=source, debit=amount), dict(account=A(OWNER), credit=amount, partner=partner)], -amount
    elif kind == "expense":
        lines = [dict(account=account, debit=amount, dim1=cost_center), dict(account=source, credit=amount)]
    elif kind == "wage":
        net = amount - deduct_advance - deduct_penalty
        if net < 0:
            raise ValidationError("الخصومات أكبر من الأجر")
        lines = [dict(account=A(WAGES), debit=amount, partner=partner, dim1=cost_center)]
        if deduct_advance:
            lines.append(dict(account=A(ADV), credit=deduct_advance, partner=partner, description=f"خصم سلفة — {partner.name}"))
        if deduct_penalty:
            lines.append(dict(account=A(PENALTY), credit=deduct_penalty, dim1=cost_center, description=f"خصم/جزاء — {partner.name}"))
        if net:
            lines.append(dict(account=source, credit=net))
        out = net
    elif kind == "advance":
        lines = [dict(account=A(ADV), debit=amount, partner=partner), dict(account=source, credit=amount)]
    elif kind == "supplier":
        lines = [dict(account=A(SUPPLIERS), debit=amount, partner=partner), dict(account=source, credit=amount)]
    elif kind == "collect":  # money in from a credit customer (patient / insurance company): his balance goes down
        lines, out = [dict(account=source, debit=amount), dict(account=A(CUSTOMERS), credit=amount, partner=partner)], -amount
    elif kind == "doctor":  # clinic: paying a doctor what his visits earned him
        from pos.clinic.services import payable_account

        lines = [dict(account=payable_account(), debit=amount, partner=partner), dict(account=source, credit=amount)]
    for ln in lines:
        ln.setdefault("description", desc)
    entry = L.create_entry("JV", date, desc, lines, source=f"pos.cashmove:{uid}", legacy_ref=uid[:30], user=user)
    return CashMove.objects.create(uid=uid, kind=kind, date=date, shift=shift, from_owner=from_owner, cash_effect=Z if from_owner else -out,
                                   amount=amount, deduct_advance=deduct_advance, deduct_penalty=deduct_penalty, partner=partner, account=account,
                                   note=note[:200], entry=entry, by=by[:100])


def owner_balance():
    from django.db.models import Sum

    from ledger.models import JournalLine

    r = JournalLine.objects.filter(account__code=OWNER, entry__state__in=["posted", "reversed"]).aggregate(d=Sum("debit"), c=Sum("credit"))
    return (r["c"] or Z) - (r["d"] or Z)  # positive: the shop owes the owner


def owner_partner(partner=None, strict=False):
    """Drawings/deposits belong to a partner when the business has partners (one partner → him automatically)."""
    from .models import ShopPartner

    sps = list(ShopPartner.objects.filter(active=True).select_related("partner"))
    if partner is not None:
        if sps and partner.pk not in {sp.partner_id for sp in sps}:
            raise ValidationError("اختر الشريك")
        return partner
    if len(sps) == 1:
        return sps[0].partner
    if len(sps) > 1 and strict:  # the cashier drawer (offline) may record it untagged — the report shows it apart
        raise ValidationError("اختر الشريك الذي سحب أو أودع")
    return None
