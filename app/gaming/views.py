import json
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.models import CompanySettings
from inventory.models import Item

from . import services as G
from .models import PlaySession, Station


def _allowed(u):
    return u.is_superuser or u.groups.filter(name__in=["كاشير", "مدير المحل"]).exists()


def _guard(view):
    @login_required
    def wrap(request, *a, **k):
        if not _allowed(request.user):
            return HttpResponseForbidden("هذه الشاشة للكاشير وصاحب المحل")
        return view(request, *a, **k)

    wrap.__name__ = view.__name__
    return wrap


@_guard
def screen(request):
    s = CompanySettings.get()
    return render(request, "gaming/screen.html", {"hide_nav": True, "s": s, "rule": G.rule_text(s)})


def _catalog():
    items = Item.objects.filter(active=True, sale_price__isnull=False).exclude(code=G.PLAY_CODE).exclude(code="0").exclude(
        category__name__startswith="مستلزمات تشغيل").exclude(category__name__startswith="خامات").select_related("category").order_by("category__name", "name")
    return [dict(code=i.code, name=i.name, price=str(i.sale_price), cat=i.category.name, barcode=i.barcode) for i in items]


@_guard
def state(request):
    from pos.voids import is_owner

    stations, takeaway = G.stations_state()
    sh = G.open_shift()
    today = timezone.localdate()
    done = PlaySession.objects.filter(state="closed", closed_at__date=today)
    agg = done.aggregate(t=Sum("time_amount"), i=Sum("items_amount"), n=Sum("total"))
    out = dict(now=timezone.now().isoformat(), stations=stations, takeaway=takeaway, owner=is_owner(request.user),
               shift=dict(uid=sh.uid, cashier=sh.cashier, opened_at=sh.opened_at.isoformat(), float=str(sh.opening_float)) if sh else None,
               today=dict(count=done.count(), time=str(agg["t"] or 0), items=str(agg["i"] or 0), total=str(agg["n"] or 0)),
               recent=[dict(id=x.pk, station=str(x.station or "تيك أواي"), customer=x.customer, total=str(x.total), closed=x.closed_at.isoformat(),
                            minutes=x.billed_minutes) for x in done.select_related("station").order_by("-closed_at")[:12]])
    if request.GET.get("catalog"):
        from ledger.models import Partner

        out["catalog"] = _catalog()
        out["customers"] = [dict(id=p.pk, name=p.name) for p in Partner.objects.filter(kind="customer", active=True, merged_into__isnull=True)
                            .order_by("name")[:500]]
    return JsonResponse(out)


@require_POST
@_guard
def action(request, name):
    from pos.voids import is_owner

    try:
        d = json.loads(request.body or b"{}")
    except ValueError:
        return JsonResponse({"error": "طلب غير صحيح"}, status=400)
    u = request.user
    try:
        ses = get_object_or_404(PlaySession, pk=d["session"]) if d.get("session") else None
        if name == "open":
            st = get_object_or_404(Station, pk=d.get("station"), active=True)
            if not G.open_shift():
                raise ValidationError("افتح الوردية الأول")
            ses = G.open_session(st, u, mode=d.get("mode", "single"), customer=d.get("customer", ""),
                                 planned_minutes=int(d.get("planned") or 0) or None)
        elif name == "takeaway":
            if not G.open_shift():
                raise ValidationError("افتح الوردية الأول")
            ses = G.open_session(None, u, customer=d.get("customer", ""))
        elif name == "add":
            item = Item.objects.filter(code=d.get("code")).first() or Item.objects.filter(barcode=d.get("code"), barcode__gt="").first()
            if not item:
                raise ValidationError("صنف غير موجود")
            G.add_item(ses, item, d.get("qty", 1), u)
        elif name == "switch":
            st = get_object_or_404(Station, pk=d["station"], active=True) if d.get("station") else None
            G.switch(ses, u, mode=d.get("mode"), station=st)
        elif name == "close":
            ses = G.close_session(ses, u, paid_card=d.get("card") or 0, discount=d.get("discount") or 0,
                                  customer_id=d.get("customer_id"), paid_credit=d.get("credit") or 0)
            from inventory.reorder import low

            return JsonResponse({"ok": True, "session": ses.pk, "total": str(ses.total), "receipt": f"/ui/play/receipt/{ses.pk}/",
                                 "low": low(ses.items.values_list("item_id", flat=True))})
        elif name == "cancel":
            G.cancel_session(ses, u, owner=is_owner(u))
        elif name == "shift_open":
            G.start_shift(u, Decimal(str(d.get("float") or 0)))
        elif name == "shift_close":
            if PlaySession.objects.filter(state="open", station__isnull=True).exists():
                raise ValidationError("يوجد طلب تيك أواي مفتوح — أغلقه أولًا")
            sh = G.end_shift(u, Decimal(str(d.get("counted") or 0)))
            return JsonResponse({"ok": True, "expected": str(sh.expected_cash), "counted": str(sh.counted_cash),
                                 "diff": str((sh.counted_cash or 0) - (sh.expected_cash or 0))})
        else:
            return JsonResponse({"error": "عملية غير معروفة"}, status=404)
    except (ValidationError, KeyError, ValueError) as e:
        msg = e.messages[0] if isinstance(e, ValidationError) else "بيانات ناقصة"
        return JsonResponse({"error": msg}, status=400)
    return JsonResponse({"ok": True, "session": ses.pk if ses else None})


@_guard
def receipt(request, pk):
    ses = get_object_or_404(PlaySession.objects.select_related("station", "order"), pk=pk)
    b = G.bill(ses, ses.closed_at)
    return render(request, "gaming/receipt.html", dict(ses=ses, b=b, s=CompanySettings.get(), hide_nav=True, rule=G.rule_text()))
