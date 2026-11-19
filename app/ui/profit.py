"""Profit reports: the month (every plan) and the year (pro plan or a paid extra), with the partners' shares."""
import datetime as dt
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils import timezone

from ledger.models import Partner
from pos import profit as P
from pos.models import ShopPartner

from .shop import _need_cash, _is_manager

MONTHS = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"]


def _ym(request):
    today = timezone.localdate()
    try:
        y, m = (int(x) for x in request.GET.get("m", "").split("-"))
        dt.date(y, m, 1)
    except ValueError:
        y, m = today.year, today.month
    return y, m


def _bars(where, profit):
    """Widths for the «where is the profit» bar (only the positive parts, they add up to the profit or more)."""
    pos = [w for w in where if w["amount"] > 0]
    tot = sum((w["amount"] for w in pos), Decimal(0)) or Decimal(1)
    return [dict(w, width=float(w["amount"] / tot * 100)) for w in pos]


@login_required
def monthly(request):
    _need_cash(request)
    from pos.branches import home_branch

    if home_branch(request.user):  # a branch manager: his branch's month
        return redirect(f"/ui/shop/branches/{home_branch(request.user).pk}/?p=month")
    y, m = _ym(request)
    d1, d2 = P.month_range(y, m)
    s = P.summary(d1, d2)
    rows, untagged = P.split(s)
    prev = (d1 - dt.timedelta(days=1)).strftime("%Y-%m")
    nxt = (d2 + dt.timedelta(days=1)).strftime("%Y-%m")
    return render(request, "ui/shop_profit.html", dict(
        s=s, rows=rows, untagged=untagged, bars=_bars(s["where"], s["profit"]), title=f"{MONTHS[m - 1]} {y}", y=y,
        prev=prev, next=nxt if d2 < timezone.localdate() else "", sps=ShopPartner.objects.filter(active=True).select_related("partner"),
        can_edit=_is_manager(request.user), margin=(s["profit"] / s["income"] * 100) if s["income"] else Decimal(0)))


@login_required
def yearly(request):
    _need_cash(request)
    from pos.branches import home_branch

    if home_branch(request.user):
        return redirect(f"/ui/shop/branches/{home_branch(request.user).pk}/?p=year")
    today = timezone.localdate()
    try:
        y = int(request.GET.get("y") or today.year)
    except ValueError:
        y = today.year
    months, total = P.year(y, today)
    rows, untagged = P.split(total)
    top = max([abs(x["profit"]) for x in months] + [Decimal(1)])
    table = [dict(name=MONTHS[i], key=f"{y}-{i + 1:02d}", profit=x["profit"], income=x["income"], expense=x["expense"],
                  drawings=x["groups"]["drawings"], cash=x["groups"]["cash"], stock=x["groups"]["stock"],
                  rest=x["groups"]["receivable"] + x["groups"]["payable"] + x["groups"]["capital"] + x["groups"]["other"],
                  h=float(abs(x["profit"]) / top * 100)) for i, x in enumerate(months)]
    return render(request, "ui/shop_profit_year.html", dict(
        y=y, table=table, total=total, rows=rows, untagged=untagged, bars=_bars(total["where"], total["profit"]),
        prev=y - 1, next=y + 1 if y < today.year else ""))


@login_required
def partners_save(request):
    """Owner/manager: the partners and their shares (must add up to 100%)."""
    _need_cash(request)
    if request.method != "POST" or not _is_manager(request.user):
        return redirect("shop_profit")
    names, shares = request.POST.getlist("name"), request.POST.getlist("share")
    rows = []
    for n, sh in zip(names, shares):
        n = n.strip()[:200]
        if not n:
            continue
        try:
            v = Decimal(sh.replace(",", ".").strip())
        except InvalidOperation:
            messages.error(request, f"نسبة «{n}» ليس رقمًا")
            return redirect("/ui/shop/profit/#partners")
        rows.append((n, v))
    total = sum((v for _, v in rows), Decimal(0))
    if rows and total != 100:
        messages.error(request, f"يجب أن يكون مجموع النسب 100% (الآن {total.normalize()}%)")
        return redirect("/ui/shop/profit/#partners")
    with transaction.atomic():
        keep = []
        for i, (n, v) in enumerate(rows):
            sp = ShopPartner.objects.filter(partner__name=n).select_related("partner").first()
            if not sp:
                p = Partner.objects.filter(name=n, kind="related").first() or Partner.objects.create(name=n, kind="related")
                sp = ShopPartner(partner=p, share=v)
            sp.share, sp.active, sp.order = v, True, i
            sp.save()
            keep.append(sp.pk)
        ShopPartner.objects.exclude(pk__in=keep).update(active=False)
    messages.success(request, "تم حفظ الشركاء ونسبهم ✔" if rows else "أُلغيت الشراكة — الربح كله لصاحب المحل")
    return redirect("/ui/shop/profit/#partners")
