"""Profit of a month / a year, where it went, and each partner's share.

Profit = income − expenses (the profit & loss accounts) for the period. Because every entry balances, the same amount shows up as
the change in everything else, so «where is the profit» is read straight from the ledger:
  drawings      partners took it home            (جاري صاحب المحل / الشركاء, debit − credit)
  cash          more money in the drawer, wallets, bank
  stock         more goods on the shelves (at cost)
  receivable    customers owe it (credit sales)
  payable       used to pay suppliers' old debts (negative: goods bought and not paid yet)
  capital       opening balances / money put in as capital (not profit — shown so the totals match)
  other         staff advances, assets, VAT, accruals…
cash + stock + receivable + payable + capital + other + drawings == profit, to the piastre.

Partners: each gets profit × his %. What he drew is his; the rest of his share stays in the shop in the same mix
(cash / stock / other) as the business's retained profit.
"""
import calendar
import datetime as dt
from collections import defaultdict
from decimal import Decimal

from django.db.models import Q, Sum

from ledger.models import JournalLine

Z = Decimal("0")
STATES = ["posted", "reversed"]
OWNER = "2304010101"
CARD = "1207010108"  # card / wallet payments of the cashier not settled yet — money, not capital
GROUPS = [
    ("drawings", "مسحوبات شخصية للشركاء", "سُحبت من المحل للاستخدام الشخصي"),
    ("cash", "نقدية في الخزينة والمحافظ والبنك", "زيادة النقدية الموجودة (ومنها أرصدة البطاقات والمحافظ)"),
    ("stock", "بضاعة في المخزن", "زيادة قيمة البضاعة بالتكلفة"),
    ("receivable", "مستحقات لدى العملاء (آجل)", "مبيعات لم تُحصّل بعد"),
    ("payable", "سداد ديون الموردين", "دُفعت للموردين عن بضاعة سابقة"),
    ("capital", "رأس مال", "حركات على رأس المال مع أرباح وخسائر"),
    ("other", "بنود أخرى", "سلف العاملين، أجهزة وأصول، ضرائب ومستحقات"),
]
LABEL = {k: v for k, v, _ in GROUPS}
NEG = {"drawings": "أودع الشركاء في المحل أكثر مما سحبوا", "cash": "قلّت النقدية — صُرفت على بند من بنود القائمة",
       "stock": "قلّت البضاعة — بيع من بضاعة كانت موجودة من قبل", "receivable": "حُصّلت مبالغ من العملاء عن مبيعات سابقة",
       "payable": "بضاعة أو مصاريف لم يُدفع ثمنها للموردين بعد"}


def group_of(code):
    if code == OWNER:
        return "drawings"
    if code.startswith("1201") or code == CARD:
        return "cash"
    if code.startswith("1202"):
        return "stock"
    if code == "1203010101":
        return "receivable"
    if code == "2301020101":
        return "payable"
    if code.startswith("1207") or code.startswith("21"):
        return "capital"
    return "other"


def q(x):
    return Decimal(x).quantize(Decimal("0.01"))


def month_range(year, month):
    return dt.date(year, month, 1), dt.date(year, month, calendar.monthrange(year, month)[1])


def partners():
    from .models import ShopPartner

    return list(ShopPartner.objects.filter(active=True).select_related("partner"))


def summary(d1, d2):
    lines = JournalLine.objects.filter(entry__state__in=STATES, entry__date__range=(d1, d2))
    income = expense = Z
    for r in lines.filter(account__report="profit_loss").values("account__account_type").annotate(d=Sum("debit"), c=Sum("credit")):
        v = (r["c"] or Z) - (r["d"] or Z)
        if r["account__account_type"] == "income":
            income += v
        else:
            expense -= v
    profit = income - expense
    # opening balances / capital put in (no profit in them) are left out of «where», and shown apart
    capital = (Q(account__code__startswith="1207") & ~Q(account__code=CARD)) | Q(account__code__startswith="21")
    cap_q = lines.filter(capital)
    pl_entries = lines.filter(account__report="profit_loss").values("entry_id")
    cap_entries = set(cap_q.exclude(entry_id__in=pl_entries).values_list("entry_id", flat=True))
    capital_in = Z
    agg = lines.filter(entry_id__in=cap_entries).exclude(capital).aggregate(
        d=Sum("debit"), c=Sum("credit"))
    capital_in = (agg["d"] or Z) - (agg["c"] or Z)
    where = defaultdict(lambda: Z)
    for r in lines.exclude(account__report="profit_loss").exclude(entry_id__in=cap_entries).values("account__code").annotate(
            d=Sum("debit"), c=Sum("credit")):
        where[group_of(r["account__code"])] += (r["d"] or Z) - (r["c"] or Z)
    drawn = defaultdict(lambda: Z)  # partner id (None = not tagged) → net drawings
    for r in lines.filter(account__code=OWNER).values("partner_id").annotate(d=Sum("debit"), c=Sum("credit")):
        drawn[r["partner_id"]] += (r["d"] or Z) - (r["c"] or Z)
    return dict(d1=d1, d2=d2, income=q(income), expense=q(expense), profit=q(profit), capital_in=q(capital_in),
                where=[dict(key=k, label=lbl, hint=h if where[k] > 0 else NEG.get(k, h), amount=q(where[k]), pct=(where[k] / profit * 100) if profit else Z)
                       for k, lbl, h in GROUPS if where[k]],
                groups={k: where[k] for k, _, _ in GROUPS}, drawn=dict(drawn))


def split(s, sps=None):
    """Each partner's share, what he drew, and where the rest of his share is."""
    sps = partners() if sps is None else sps
    if not sps:  # no partners defined: the owner gets it all
        from types import SimpleNamespace

        sps = [SimpleNamespace(partner_id=None, share=Decimal(100), partner=SimpleNamespace(name="صاحب المحل"))]
    g, profit = s["groups"], s["profit"]
    retained = profit - g["drawings"]
    mix = dict(cash=g["cash"], stock=g["stock"], other=g["receivable"] + g["payable"] + g["capital"] + g["other"])
    drawn = dict(s["drawn"])
    if len(sps) == 1 and None in drawn and sps[0].partner_id is not None:  # one partner: untagged drawings are his
        drawn[sps[0].partner_id] = drawn.get(sps[0].partner_id, Z) + drawn.pop(None)
    rows = []
    for sp in sps:
        share = q(profit * sp.share / 100)
        took = q(drawn.get(sp.partner_id, Z))
        left = share - took
        parts = {k: (q(left * v / retained) if retained else Z) for k, v in mix.items()}
        rows.append(dict(name=sp.partner.name, pct=sp.share, share=share, drawn=took, left=left, **parts))
    untagged = q(drawn.get(None, Z)) if len(sps) != 1 else Z
    return rows, untagged


def year(y, today=None):
    today = today or dt.date.today()
    months = []
    for m in range(1, 13):
        d1, d2 = month_range(y, m)
        if d1 > today:
            break
        s = summary(d1, d2)
        months.append(s)
    total = summary(dt.date(y, 1, 1), min(dt.date(y, 12, 31), max(today, dt.date(y, 1, 1))))
    return months, total
