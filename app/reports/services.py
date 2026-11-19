"""Report calculations. All figures come from posted journal lines (constitution I)."""
from collections import defaultdict
from decimal import Decimal

from django.db.models import Q, Sum

from ledger.models import ZERO, Account, DimensionValue, JournalLine

POSTED = Q(entry__state__in=["posted", "reversed"])


def _sum(qs):
    a = qs.aggregate(d=Sum("debit"), c=Sum("credit"))
    return (a["d"] or ZERO), (a["c"] or ZERO)


def trial_balance(date_from, date_to, max_level=6, hide_zero=True):
    """Rows per account (rolled up to parents): opening, period debit/credit, closing."""
    base = JournalLine.objects.filter(POSTED)
    opening = dict((r["account__code"], (r["d"] or ZERO) - (r["c"] or ZERO)) for r in
                   base.filter(entry__date__lt=date_from).values("account__code").annotate(d=Sum("debit"), c=Sum("credit")))
    period = dict((r["account__code"], (r["d"] or ZERO, r["c"] or ZERO)) for r in
                  base.filter(entry__date__range=(date_from, date_to)).values("account__code").annotate(d=Sum("debit"), c=Sum("credit")))
    accounts = list(Account.objects.all().order_by("code"))
    agg = defaultdict(lambda: [ZERO, ZERO, ZERO])
    for acc in accounts:
        if not acc.is_postable:
            continue
        o = opening.get(acc.code, ZERO)
        d, c = period.get(acc.code, (ZERO, ZERO))
        if not (o or d or c):
            continue
        # roll up to every ancestor by code prefix
        for anc in accounts:
            if acc.code.startswith(anc.code):
                a = agg[anc.code]
                a[0] += o
                a[1] += d
                a[2] += c
    rows = []
    for acc in accounts:
        if acc.level > max_level:
            continue
        o, d, c = agg.get(acc.code, (ZERO, ZERO, ZERO))
        close = o + d - c
        if hide_zero and not (o or d or c):
            continue
        rows.append(dict(account=acc, opening=o, debit=d, credit=c, closing=close))
    tops = [r for r in rows if r["account"].level == 1]
    totals = dict(opening=sum((r["opening"] for r in tops), ZERO), debit=sum((r["debit"] for r in tops), ZERO),
                  credit=sum((r["credit"] for r in tops), ZERO), closing=sum((r["closing"] for r in tops), ZERO))
    return rows, totals


def account_ledger(account, date_from, date_to, partner=None, dim1=None):
    qs = JournalLine.objects.filter(POSTED, account__code__startswith=account.code)
    if partner:
        qs = qs.filter(partner=partner)
    if dim1:
        qs = qs.filter(dim1_id__in=subtree_ids(dim1))
    o_d, o_c = _sum(qs.filter(entry__date__lt=date_from))
    running = o_d - o_c
    rows = []
    for ln in qs.filter(entry__date__range=(date_from, date_to)).select_related("entry", "partner", "dim1", "dim2", "account").order_by("entry__date", "entry_id", "id"):
        running += ln.debit - ln.credit
        rows.append(dict(line=ln, balance=running))
    return o_d - o_c, rows, running


def partner_statement(partner, date_from, date_to):
    qs = JournalLine.objects.filter(POSTED, partner=partner)
    o_d, o_c = _sum(qs.filter(entry__date__lt=date_from))
    running = o_d - o_c
    rows = []
    for ln in qs.filter(entry__date__range=(date_from, date_to)).select_related("entry", "account").order_by("entry__date", "entry_id", "id"):
        running += ln.debit - ln.credit
        rows.append(dict(line=ln, balance=running))
    return o_d - o_c, rows, running


def _pl_amount(qs):
    d, c = _sum(qs)
    return d - c


def income_statement(date_from, date_to):
    """Revenue (4) and expenses (3) by level-2 group. Positive = profit."""
    qs = JournalLine.objects.filter(POSTED, entry__date__range=(date_from, date_to), account__report="profit_loss")
    groups = []
    for grp in Account.objects.filter(level=2, report="profit_loss").order_by("code"):
        amt = _pl_amount(qs.filter(account__code__startswith=grp.code))
        if amt:
            groups.append(dict(account=grp, amount=-amt if grp.account_type == "income" else amt))
    revenue = sum((g["amount"] for g in groups if g["account"].account_type == "income"), ZERO)
    expenses = sum((g["amount"] for g in groups if g["account"].account_type == "expense"), ZERO)
    return dict(groups=groups, revenue=revenue, expenses=expenses, net=revenue - expenses)


def balance_sheet(as_of):
    qs = JournalLine.objects.filter(POSTED, entry__date__lte=as_of)
    sections = []
    for top in Account.objects.filter(level=1, report="balance_sheet").order_by("code"):
        items = []
        for grp in Account.objects.filter(level=3, code__startswith=top.code).order_by("code"):
            d, c = _sum(qs.filter(account__code__startswith=grp.code))
            amt = d - c if top.code.startswith("1") else c - d
            if amt:
                items.append(dict(account=grp, amount=amt))
        sections.append(dict(account=top, items=items, total=sum((i["amount"] for i in items), ZERO)))
    pl = qs.filter(account__report="profit_loss")
    d, c = _sum(pl)
    current_profit = c - d  # unclosed P&L shown under equity
    assets = sum((s["total"] for s in sections if s["account"].code.startswith("1")), ZERO)
    liab_eq = sum((s["total"] for s in sections if not s["account"].code.startswith("1")), ZERO) + current_profit
    return dict(sections=sections, current_profit=current_profit, assets=assets, liab_eq=liab_eq, balanced=abs(assets - liab_eq) < Decimal("0.01"))


def cash_flow(date_from, date_to):
    """Direct method: each cash line is classified by the cash-flow category of the other accounts in its entry."""
    cash_q = Q(account__cash_flow_category="cash")
    cash_lines = JournalLine.objects.filter(POSTED, cash_q, entry__date__range=(date_from, date_to)).select_related("entry")
    entries = defaultdict(lambda: ZERO)
    for ln in cash_lines:
        entries[ln.entry_id] += ln.debit - ln.credit
    by_cat = defaultdict(lambda: defaultdict(lambda: ZERO))
    for entry_id, net_cash in entries.items():
        if not net_cash:
            continue  # pure transfers between cash accounts
        others = list(JournalLine.objects.filter(entry_id=entry_id).exclude(cash_q).select_related("account__parent"))
        tot = sum((o.debit + o.credit for o in others), ZERO)
        if not tot:
            continue
        for o in others:
            share = net_cash * (o.debit + o.credit) / tot
            cat = o.account.cash_flow_category or "operating"
            if cat in ("non_cash", "cash"):
                cat = "operating"
            label = o.account.parent.name if o.account.parent else o.account.name
            by_cat[cat][label] += share
    o_d, o_c = _sum(JournalLine.objects.filter(POSTED, cash_q, entry__date__lt=date_from))
    opening = o_d - o_c
    sections = []
    for cat, title in (("operating", "التدفقات من الأنشطة التشغيلية"), ("investing", "التدفقات من الأنشطة الاستثمارية"), ("financing", "التدفقات من الأنشطة التمويلية")):
        items = sorted(((k, v.quantize(Decimal("0.01"))) for k, v in by_cat[cat].items() if v.quantize(Decimal("0.01"))), key=lambda x: x[1])
        sections.append(dict(title=title, items=items, total=sum((v for _, v in items), ZERO)))
    net = sum((s["total"] for s in sections), ZERO)
    c_d, c_c = _sum(JournalLine.objects.filter(POSTED, cash_q, entry__date__lte=date_to))
    return dict(sections=sections, opening=opening, net=net, closing=c_d - c_c, computed_closing=opening + net)
