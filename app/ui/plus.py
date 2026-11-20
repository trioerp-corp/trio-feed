"""1.26 owner pages of the optional till features (pos/plus.py): salespeople commissions, season sale + slow movers, loyalty."""
import datetime as dt

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render
from django.utils import timezone

from pos import voids as V
from pos import plus as PL


def _owner(request):
    if not V.is_owner(request.user):
        raise PermissionDenied


def _errs(request, e):
    messages.error(request, "؛ ".join(getattr(e, "messages", None) or [str(e)]))


def settings_post(request):
    """The «مزايا الكاشير» card of the settings page: switches only (details on each feature's page)."""
    c = PL.cfg()
    on = set(request.POST.getlist("on"))
    PL.set_pharm("pharm" in on)
    sp = c["sp"]
    PL.set_sp("sp" in on, sp["pct"], sp["people"], sp["cats"], sp["need"])
    lo = c["loy"]
    PL.set_loy("loy" in on, lo["per"], lo["val"], lo["min"])
    messages.success(request, "تم الحفظ ✔ — تظهر التغييرات على شاشة الكاشير خلال دقائق أو عند إعادة فتحها")


def _dates(request, back=30):
    today = timezone.localdate()
    try:
        d1 = dt.date.fromisoformat(request.GET.get("from") or "")
    except ValueError:
        d1 = today.replace(day=1) if back == 0 else today - dt.timedelta(days=back)
    try:
        d2 = dt.date.fromisoformat(request.GET.get("to") or "")
    except ValueError:
        d2 = today
    return d1, d2


@login_required
def commission(request):
    _owner(request)
    from inventory.models import ItemCategory
    from ledger.models import Partner

    people = Partner.objects.filter(kind="employee", active=True, merged_into__isnull=True).order_by("name")
    cats = ItemCategory.objects.order_by("name")
    if request.method == "POST":
        try:
            PL.set_sp(request.POST.get("on") == "1", request.POST.get("pct") or "0",
                      {str(p.pk): request.POST.get(f"p{p.pk}", "") for p in people},
                      {c.name: request.POST.get(f"c{c.pk}", "") for c in cats}, request.POST.get("need") == "1")
            messages.success(request, "حُفظت نسب العمولة ✔")
        except ValidationError as e:
            _errs(request, e)
        return redirect("plus_commission")
    c = PL.cfg()["sp"]
    d1, d2 = _dates(request, 0)
    rows = PL.commissions(d1, d2)
    tot = {k: sum((r[k] for r in rows), 0) for k in ("sales", "ret", "net", "comm")}
    return render(request, "ui/plus_commission.html", dict(c=c, d1=d1, d2=d2, rows=rows, tot=tot,
                                                           people=[(p, c["people"].get(str(p.pk), "")) for p in people],
                                                           cats=[(x, c["cats"].get(x.name, "")) for x in cats]))


@login_required
def season(request):
    _owner(request)
    from inventory.models import Item, ItemCategory

    if request.method == "POST":
        try:
            if request.POST.get("op") in ("delete", "toggle"):
                PL.sale_action(request.POST.get("id", ""), request.POST["op"])
                messages.success(request, "تم")
            else:
                PL.save_sale(request.POST.get("name", ""), request.POST.get("pct"), request.POST.get("start"), request.POST.get("end"),
                             cats=request.POST.getlist("cats"), items=request.POST.getlist("items"))
                messages.success(request, "أُضيف التخفيض — يطبّقه الكاشير تلقائيًا في مدته ويظهر على الإيصال")
        except ValidationError as e:
            _errs(request, e)
        return redirect("plus_season")
    today = timezone.localdate()
    days = request.GET.get("days") if request.GET.get("days") in ("30", "60", "90", "180") else "60"
    slow = PL.slow_movers(int(days))
    pick = request.GET.getlist("pick")
    names = dict(Item.objects.filter(code__in=[c for r in PL.cfg()["sales"] for c in r.get("items") or []]).values_list("code", "name"))
    sales = [{**r, "names": [names.get(c, c) for c in r.get("items") or []], "live": PL._sale_ok(r, today)} for r in PL.cfg()["sales"]]
    return render(request, "ui/plus_season.html", dict(
        sales=sales, day_opts=["30", "60", "90", "180"], cats=ItemCategory.objects.order_by("name"), today=today, month=today + dt.timedelta(days=14), days=days, slow=slow,
        slow_value=sum((r["value"] for r in slow), 0), pick=Item.objects.filter(code__in=pick).order_by("name") if pick else []))


@login_required
def loyalty(request):
    _owner(request)
    if request.method == "POST":
        try:
            PL.set_loy(request.POST.get("on") == "1", request.POST.get("per"), request.POST.get("val"), request.POST.get("min") or 0)
            messages.success(request, "حُفظت إعدادات النقاط ✔")
        except ValidationError as e:
            _errs(request, e)
        return redirect("plus_loyalty")
    rows = PL.loyalty_rows()
    q = (request.GET.get("q") or "").strip()
    if q:
        rows = [r for r in rows if q in r["phone"] or q in (r["name"] or "")]
    c = PL.cfg()["loy"]
    return render(request, "ui/plus_loyalty.html", dict(c=c, rows=rows[:500], q=q, owed=sum((r["value"] for r in rows if r["bal"] > 0), 0),
                                                        example=int(100 / c["per"]) if c["per"] else 0))
