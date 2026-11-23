"""1.25 comparisons for the owner's phone (and the daily message): today against the same weekday last week (up to the same hour),
this month against last month (the same number of days), the best-selling and the most profitable items, the cashiers' drawer
shortages, and the same per branch. Plain numbers — the Worker phone page draws them."""
import datetime as dt
from collections import defaultdict
from decimal import Decimal

from django.utils import timezone

Z = Decimal(0)
DAYS = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]


def _f(x):
    return float(Decimal(x or 0).quantize(Decimal("0.01")))


def _net(orders):
    from ui.shop import _sales

    t = _sales(orders)
    return t["net"], t["n"]


def _pct(now, prev):
    if not prev:
        return None
    return round(float((Decimal(now) - Decimal(prev)) / abs(Decimal(prev)) * 100), 1)


def _orders(qs, d1, d2, upto=None):
    q = qs.filter(created_at_device__date__range=(d1, d2))
    if upto is not None:
        q = q.filter(created_at_device__lte=upto)
    return list(q.only("total", "vat", "paid_cash", "paid_card", "paid_credit", "paid_rider", "paid_insta", "is_return", "cancelled", "raw",
                       "stock_move_id", "device_id"))


def month_back(day):
    """The same stretch of last month: 1st … same day (capped at the month's end)."""
    first = day.replace(day=1)
    last_end = first - dt.timedelta(days=1)
    start = last_end.replace(day=1)
    return start, min(start.replace(day=min(day.day, last_end.day)), last_end), last_end


def periods(qs, now):
    today = now.date()
    wk = today - dt.timedelta(days=7)
    d_now = _net(_orders(qs, today, today))
    d_prev = _net(_orders(qs, wk, wk, upto=now - dt.timedelta(days=7)))
    d_full = _net(_orders(qs, wk, wk))
    m1, m2, mend = month_back(today)
    m_now = _net(_orders(qs, today.replace(day=1), today))
    m_prev = _net(_orders(qs, m1, m2))
    m_full = _net(_orders(qs, m1, mend))
    return dict(d=dict(now=_f(d_now[0]), n=d_now[1], prev=_f(d_prev[0]), pn=d_prev[1], full=_f(d_full[0]), pct=_pct(d_now[0], d_prev[0]),
                       label=f"{DAYS[wk.weekday()]} الماضي حتى نفس الساعة"),
                m=dict(now=_f(m_now[0]), n=m_now[1], prev=_f(m_prev[0]), pn=m_prev[1], full=_f(m_full[0]), pct=_pct(m_now[0], m_prev[0]),
                       label=f"أول {today.day} يوم من الشهر الماضي"))


def items(orders):
    """Best sellers (by money) and the most profitable items of these orders. Cost: the goods that left the store for that item;
    a made-to-order item (recipe) at its ingredients' cost; a service costs nothing."""
    from inventory.models import StockMoveLine
    from inventory.recipes import costing
    from pos.models import OrderLine

    ok = [o for o in orders if not o.cancelled]
    sign = {o.pk: (-1 if o.is_return else 1) for o in ok}
    exv = {o.pk: ((o.total - o.vat) / o.total) if o.total else Decimal(1) for o in ok}
    rev, qty, rev_ex = defaultdict(lambda: Z), defaultdict(lambda: Z), defaultdict(lambda: Z)
    names, kinds = {}, {}
    for ln in OrderLine.objects.filter(order_id__in=list(sign)).select_related("item"):
        s = sign[ln.order_id]
        v = ln.qty * ln.price - ln.discount
        rev[ln.item_id] += s * v
        rev_ex[ln.item_id] += s * v * exv[ln.order_id]
        qty[ln.item_id] += s * ln.qty
        names[ln.item_id], kinds[ln.item_id] = ln.item.name, ln.item
    cost = defaultdict(lambda: Z)
    moves = {o.stock_move_id: sign[o.pk] for o in ok if o.stock_move_id}
    for ln in StockMoveLine.objects.filter(move_id__in=list(moves)).only("item_id", "value", "move_id"):
        if ln.item_id in rev:
            cost[ln.item_id] += moves[ln.move_id] * (ln.value or Z)
    for pk, it in kinds.items():
        if it.item_type == "recipe" and not cost[pk]:
            try:
                cost[pk] = costing(it.recipe)["cost"] * qty[pk]
            except Exception:  # noqa: BLE001 — no recipe written yet
                pass
    best = sorted(rev, key=lambda k: -rev[k])[:6]
    profit = {k: rev_ex[k] - cost[k] for k in rev}
    prof = sorted(profit, key=lambda k: -profit[k])[:6]
    return ([dict(n=names[k], q=_f(qty[k]), v=_f(rev[k])) for k in best if rev[k] > 0],
            [dict(n=names[k], p=_f(profit[k]), v=_f(rev[k]), m=round(float(profit[k] / rev_ex[k] * 100), 1) if rev_ex[k] else 0)
             for k in prof if profit[k] > 0])


def shortages(now, days=30, branch=None):
    """Each cashier's drawer differences at shift close over the last days: worst shortage first."""
    from pos.models import Shift

    qs = Shift.objects.filter(closed_at__date__gte=now.date() - dt.timedelta(days=days - 1), counted_cash__isnull=False, expected_cash__isnull=False)
    if branch is not None:
        qs = qs.filter(device__branch=branch)
    by = defaultdict(lambda: dict(n=0, short=Z, over=Z, k=0))
    for sh in qs.select_related("device__branch"):
        d = (sh.counted_cash or Z) - (sh.expected_cash or Z)
        r = by[sh.cashier]
        r["n"] += 1
        if d < 0:
            r["short"] += d
            r["k"] += 1
        elif d > 0:
            r["over"] += d
    rows = sorted(by.items(), key=lambda kv: kv[1]["short"])
    return [dict(c=c, n=r["n"], k=r["k"], s=_f(r["short"]), o=_f(r["over"])) for c, r in rows if r["short"] or r["over"]][:10]


def comparisons(now=None):
    from pos.models import Branch, Order

    now = now or timezone.localtime()
    today = now.date()
    qs = Order.objects.all()
    out = periods(qs, now)
    month = _orders(qs, today.replace(day=1), today)
    out["best"], out["prof"] = items(month)
    out["short"] = shortages(now)
    branches = list(Branch.objects.filter(active=True).order_by("id"))
    if len(branches) > 1:
        out["br"] = []
        for b in branches:
            p = periods(qs.filter(device__branch=b), now)
            sh = shortages(now, branch=b)
            out["br"].append(dict(n=b.name, d=p["d"]["now"], dp=p["d"]["prev"], dpct=p["d"]["pct"], m=p["m"]["now"], mp=p["m"]["prev"],
                                  mpct=p["m"]["pct"], s=_f(sum((Decimal(str(x["s"])) for x in sh), Z))))
    return out
