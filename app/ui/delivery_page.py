"""1.24 owner pages: «الدليفري» (settings + per-rider report) and «الخدمة ومحطات المطبخ» (service charge %, kitchen stations)."""
import datetime as dt

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils import timezone

from core.models import CompanySettings
from pos import delivery as D
from pos import restaurant as R


def _owner(user):
    from pos.voids import is_owner

    return user.is_superuser or is_owner(user)


def _date(v, default):
    try:
        return dt.date.fromisoformat(str(v))
    except (TypeError, ValueError):
        return default


@login_required
def delivery(request):
    s = CompanySettings.get()
    if s.business not in D.BIZ:
        raise Http404("الدليفري غير متاح لهذا النشاط")
    owner = _owner(request.user)
    if not (owner or request.user.has_perm("treasury.view_voucher")):
        raise PermissionDenied
    if request.method == "POST":
        if not owner:
            raise PermissionDenied
        try:
            D.set_cfg(on=request.POST.get("on") == "1", fee=request.POST.get("fee") or "0", riders_text=request.POST.get("riders", ""))
            messages.success(request, "تم الحفظ ✔ — تظهر التغييرات في شاشة الكاشير خلال دقائق أو بعد تحديث الصفحة")
        except ValidationError as e:
            messages.error(request, e.messages[0])
        return redirect(request.path)
    today = timezone.localdate()
    start = _date(request.GET.get("from"), today.replace(day=1))
    end = _date(request.GET.get("to"), today)
    rows, tot, orders = D.report(start, end)
    raw = D.raw_cfg(s)
    return render(request, "ui/delivery.html", dict(raw=raw, riders_text="\n".join(raw.get("riders") or []), employees=D.riders(s) if not raw.get("riders") else [],
                                                    rows=rows, tot=tot, orders=orders, start=start, end=end, owner=owner,
                                                    open_n=D.open_orders().count()))


@login_required
def restaurant(request):
    s = CompanySettings.get()
    if s.business not in R.BIZ:
        raise Http404("للكافيهات والمطاعم")
    if not _owner(request.user):
        raise PermissionDenied
    from inventory.models import ItemCategory

    if request.method == "POST":
        P = request.POST
        try:
            if P.get("op") == "svc":
                R.set_svc(pct=P.get("pct", ""), dine=P.get("dine") == "1")
            else:
                rows = []
                for i in range(int(P.get("n") or 0)):
                    rows.append(dict(name=P.get(f"name{i}", ""), printer=P.get(f"printer{i}", ""), cats=P.getlist(f"cats{i}")))
                R.set_stations(rows)
            messages.success(request, "تم الحفظ ✔")
        except (ValidationError, ValueError) as e:
            messages.error(request, e.messages[0] if hasattr(e, "messages") else "بيانات غير صحيحة")
        return redirect(request.path)
    from pos import kitchen

    sts = R.stations(s)
    cats = list(ItemCategory.objects.exclude(name__in=["خدمات"]).exclude(name__startswith="مستلزمات تشغيل").order_by("name").values_list("name", flat=True))
    blank = 2 if len(sts) < 2 else 1
    rows = sts + [dict(name="", printer="", cats=[]) for _ in range(blank)]
    return render(request, "ui/restaurant_cfg.html", dict(svc=R.svc_cfg(s), raw=(s.menu_cfg or {}).get("service") or {}, rows=rows, cats=cats,
                                                          printers=kitchen.printers(), kitchen=s.get_kitchen_ticket_display(),
                                                          main_printer=s.kitchen_printer))
