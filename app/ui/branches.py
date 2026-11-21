"""Branches (module «branches», plan «شامل»): the owner's view of every branch, each branch's report, and opening a new branch."""
import datetime as dt
from collections import defaultdict
from decimal import Decimal

from core.middleware import safe_next
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Sum
from django.shortcuts import redirect, render
from django.utils import timezone

from core.models import CompanySettings
from inventory.models import StockMove
from ledger.models import JournalLine
from pos import branches as BR
from pos.models import Branch, Device, Order, OrderLine, Shift
from treasury import services as T

from .shop import _branch_ccs, _branch_stock_value, _cogs, _need_cash, _sales

Z = Decimal(0)
MONTHS = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"]


def period(kind, day):
    if kind == "month":
        d1 = day.replace(day=1)
        d2 = (d1 + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)
        return d1, d2, f"شهر {MONTHS[d1.month - 1]} {d1.year}"
    if kind == "year":
        return dt.date(day.year, 1, 1), dt.date(day.year, 12, 31), f"سنة {day.year}"
    return day, day, f"يوم {day:%d/%m/%Y}"


def report(b, d1, d2):
    """Everything about one branch for a period — used by the screen, the e-mails and Telegram."""
    orders = list(Order.objects.filter(device__branch=b, created_at_device__date__range=(d1, d2)))
    t = _sales(orders)
    cogs = _cogs(orders)
    ccs = _branch_ccs(b)
    exp = defaultdict(lambda: Z)
    for r in (JournalLine.objects.filter(entry__state__in=["posted", "reversed"], entry__date__range=(d1, d2), account__report="profit_loss",
                                         account__account_type="expense", dim1__in=ccs).exclude(account__code="3101010101")
              .values("account__name").annotate(d=Sum("debit"), c=Sum("credit"))):
        v = (r["d"] or Z) - (r["c"] or Z)
        if v:
            exp[r["account__name"]] += v
    exp_total = sum(exp.values(), Z)
    gross = t["net_ex_vat"] - cogs
    top = defaultdict(lambda: [Z, Z])
    for ln in OrderLine.objects.filter(order__in=[o for o in orders if not o.cancelled]).select_related("item", "order"):
        sg = -1 if ln.order.is_return else 1
        top[ln.item.name][0] += sg * ln.qty
        top[ln.item.name][1] += sg * (ln.qty * ln.price - ln.discount)
    damage = sum((ln.value or Z for m in StockMove.objects.filter(kind__in=["expired", "adjust_out"], warehouse=b.warehouse, state="posted",
                                                                   date__range=(d1, d2)) for ln in m.lines.all()), Z)
    bought = sum((ln.value or Z for m in StockMove.objects.filter(kind="receipt", warehouse=b.warehouse, state="posted", date__range=(d1, d2))
                  for ln in m.lines.all()), Z)
    return dict(branch=b, t=t, cogs=cogs, gross=gross, expenses=sorted(exp.items(), key=lambda x: -x[1]), exp_total=exp_total, net=gross - exp_total,
                margin=(gross / t["net_ex_vat"] * 100) if t["net_ex_vat"] else Z, drawer=T.cashbox_balance(b.cashbox, upto=d2),
                stock=_branch_stock_value(b), top=sorted(top.items(), key=lambda x: -x[1][1])[:8], damage=damage, bought=bought,
                shifts=Shift.objects.filter(device__branch=b, opened_at__date__range=(d1, d2)).count(),
                open_shifts=Shift.objects.filter(device__branch=b, closed_at__isnull=True).count(),
                last_sync=Device.objects.filter(branch=b, active=True).order_by("-last_sync").values_list("last_sync", flat=True).first())


def all_report(d1, d2):
    rows = [report(b, d1, d2) for b in Branch.objects.filter(active=True).order_by("id")]
    keys = ("cogs", "gross", "exp_total", "net", "drawer", "stock", "damage", "bought")
    tot = {k: sum((r[k] for r in rows), Z) for k in keys}
    tot["sales"] = sum((r["t"]["net"] for r in rows), Z)
    tot["n"] = sum((r["t"]["n"] for r in rows), 0)
    best = max(rows, key=lambda r: r["net"]) if rows else None
    return rows, tot, best


def text(rows, tot, title, company, single=False):
    """Plain text for Telegram / e-mail."""
    out = [f"{company} — {title}", ""]
    for r in rows:
        t = r["t"]
        out += [f"▪ {r['branch'].name}", f"  المبيعات: {t['net']:,.2f} ({t['n']} فاتورة) — نقدي {t['cash']:,.2f} / بطاقة {t['card']:,.2f} / آجل {t['credit']:,.2f}",
                f"  مجمل الربح: {r['gross']:,.2f} ({r['margin']:.1f}%) — المصروفات: {r['exp_total']:,.2f} — صافي: {r['net']:,.2f}",
                f"  الدرج: {r['drawer']:,.2f} — المخزون: {r['stock']:,.2f} — تالف/عجز: {r['damage']:,.2f}"]
        if r["top"]:
            out.append("  الأكتر مبيعًا: " + "، ".join(f"{n} ({v[1]:,.0f})" for n, v in r["top"][:3]))
        if r["open_shifts"]:
            out.append(f"  ⚠ ورديات مفتوحة: {r['open_shifts']}")
        out.append("")
    if not single:
        out += [f"الإجمالي: مبيعات {tot['sales']:,.2f} ({tot['n']} فاتورة) — صافي الربح {tot['net']:,.2f}",
                f"النقدية في الأدراج {tot['drawer']:,.2f} — المخزون {tot['stock']:,.2f}"]
    out.append("بواسطة برنامج Trio")
    return "\n".join(out)


def _owner(request):
    _need_cash(request)
    if BR.home_branch(request.user):
        raise PermissionDenied("هذه الصفحة لصاحب النشاط")


@login_required
def dashboard(request):
    _owner(request)
    try:
        day = dt.date.fromisoformat(request.GET.get("date") or "")
    except ValueError:
        day = timezone.localdate()
    kind = request.GET.get("p") if request.GET.get("p") in ("day", "month", "year") else "day"
    d1, d2, title = period(kind, day)
    rows, tot, best = all_report(d1, d2)
    top = max([r["t"]["net"] for r in rows] + [Decimal(1)])
    for r in rows:
        r["bar"] = float(r["t"]["net"] / top * 100) if top else 0
        r["staff"] = list(r["branch"].staff.select_related("user").order_by("role"))
    from pos.models import Staff

    return render(request, "ui/branches.html", dict(rows=rows, tot=tot, best=best, title=title, kind=kind, day=day, caps=BR.CAPS,
                                                    templates=list(BR.templates_for(CompanySettings.get().business).items()), branches=Branch.objects.filter(active=True).order_by("id"),
                                                    head_office=Staff.objects.filter(branch__isnull=True).select_related("user")))


@login_required
def branch_report(request, pk):
    _need_cash(request)
    b = Branch.objects.get(pk=pk)
    mine = BR.home_branch(request.user)
    if mine and mine.pk != b.pk:
        raise PermissionDenied
    try:
        day = dt.date.fromisoformat(request.GET.get("date") or "")
    except ValueError:
        day = timezone.localdate()
    kind = request.GET.get("p") if request.GET.get("p") in ("day", "month", "year") else "day"
    d1, d2, title = period(kind, day)
    r = report(b, d1, d2)
    return render(request, "ui/branch_report.html", dict(r=r, title=title, kind=kind, day=day, b=b,
                                                         staff=b.staff.select_related("user").order_by("role")))


@login_required
def switch(request):
    """The owner looks at one branch (every screen follows) or at all of them."""
    _owner(request)
    bid = request.POST.get("b") or request.GET.get("b") or ""
    if bid and Branch.objects.filter(pk=bid, active=True).exists():
        request.session["branch_view"] = int(bid)
    else:
        request.session.pop("branch_view", None)
    return redirect(safe_next(request))


@login_required
def add_branch(request):
    _owner(request)
    if request.method != "POST":
        return redirect("branches")
    P = request.POST
    try:
        from django.db import transaction

        with transaction.atomic():
            b = BR.create_branch(P.get("name", ""), P.get("address", ""), P.get("phone", ""), int(P.get("devices") or 1))
            if P.get("m_username", "").strip():
                BR.create_user(P["m_username"].strip(), P.get("m_name", ""), P.get("m_password", ""), "branch_manager", b, P.get("m_title", "") or "مدير فرع",
                               None, P.get("m_email", ""), P.get("m_phone", ""))
        messages.success(request, f"فُتح فرع «{b.name}» ✔ — مخزن وخزينة وأجهزة كاشير ({b.devices.count()}) جاهزة. مفاتيح الأجهزة في الإعدادات.")
    except (ValidationError, ValueError) as e:
        messages.error(request, e.messages[0] if hasattr(e, "messages") else str(e))
    return redirect("branches")


@login_required
def add_staff(request):
    """A user with a job title, a branch and ticked permissions."""
    _owner(request)
    if request.method != "POST":
        return redirect("branches")
    P = request.POST
    try:
        b = Branch.objects.filter(pk=P.get("branch") or 0).first()
        BR.create_user(P.get("username", "").strip(), P.get("name", ""), P.get("password", ""), P.get("role", "custom"), b, P.get("title", ""),
                       P.getlist("caps") if P.get("role") == "custom" or P.getlist("caps") else None, P.get("email", ""), P.get("phone", ""))
        messages.success(request, f"تمت الإضافة «{P.get('name') or P.get('username')}» ✔")
    except ValidationError as e:
        messages.error(request, e.messages[0])
    return redirect("/ui/shop/branches/#staff")
