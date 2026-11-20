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


def _name(u):
    return (u.first_name or u.username) if u else ""


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
    from .models import HallSettings

    out = dict(now=timezone.now().isoformat(), stations=stations, takeaway=takeaway, owner=is_owner(request.user),
               max_disc=str(HallSettings.get().max_discount_pct),
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
            approver = _name(u) if is_owner(u) else None
            if not approver and d.get("owner"):  # a discount above the cap: the owner types his name and password on the hall screen
                from django.contrib.auth import authenticate

                o = authenticate(username=str(d.get("owner"))[:150], password=str(d.get("password") or ""))
                if not (o and is_owner(o)):
                    return JsonResponse({"error": "اسم المستخدم أو كلمة السر غير صحيحة، أو ليس له صلاحية الموافقة"}, status=403)
                approver = _name(o)
            new = d.get("customer_new") if isinstance(d.get("customer_new"), dict) else None
            ses = G.close_session(ses, u, paid_card=d.get("card") or 0, discount=d.get("discount") or 0,
                                  customer_id=d.get("customer_id"), paid_credit=d.get("credit") or 0, approver=approver, customer_new=new)
            if ses.discount_by and ses.discount_by != _name(u):
                from core import audit

                audit.log("gaming.discount", ses, summary=f"خصم {ses.discount} على {ses} — وافق {ses.discount_by}، العامل {_name(u)}")
            out = {"ok": True, "session": ses.pk, "total": str(ses.total), "receipt": f"/ui/play/receipt/{ses.pk}/"}
            if is_owner(u):  # shortages go to the owner (home page / phone), never a pop-up over the hall worker's bill
                from inventory.reorder import low

                out["low"] = low(ses.items.values_list("item_id", flat=True))
            return JsonResponse(out)
        elif name == "extend":
            G.extend(ses, int(d.get("minutes") or 30))
        elif name == "cancel":
            G.cancel_session(ses, u, owner=is_owner(u))
        elif name == "shift_open":
            G.start_shift(u, Decimal(str(d.get("float") or 0)))
        elif name == "shift_close":
            if PlaySession.objects.filter(state="open", station__isnull=True).exists():
                raise ValidationError("يوجد طلب تيك أواي مفتوح — أغلقه أولًا")
            sh = G.end_shift(u, Decimal(str(d.get("counted") or 0)))
            r = G.shift_report(sh)
            return JsonResponse({"ok": True, "expected": str(sh.expected_cash), "counted": str(sh.counted_cash),
                                 "diff": str((sh.counted_cash or 0) - (sh.expected_cash or 0)), "discount": str(r["discount"]),
                                 "voids": len(r["voids"]), "report": f"/ui/play/shift/{sh.pk}/"})
        else:
            return JsonResponse({"error": "عملية غير معروفة"}, status=404)
    except (ValidationError, KeyError, ValueError) as e:
        msg = e.messages[0] if isinstance(e, ValidationError) else "بيانات ناقصة"
        return JsonResponse({"error": msg}, status=400)
    return JsonResponse({"ok": True, "session": ses.pk if ses else None})


def _owner_or_cash(u):
    from pos.voids import is_owner

    return u.is_superuser or is_owner(u) or u.has_perm("treasury.view_voucher")


@_guard
def shift_print(request, pk):
    """The printed hall shift report (cash / card / credit / discounts / cancelled / still running)."""
    from pos.models import Shift

    sh = get_object_or_404(Shift, pk=pk, device__code="GM")
    return render(request, "gaming/shift.html", dict(r=G.shift_report(sh), s=CompanySettings.get(), hide_nav=True))


@login_required
def report(request):
    """«تقرير الصالة» for the owner: revenue / hours / utilisation per device, per worker, cancelled sessions, discounts."""
    if not _owner_or_cash(request.user):
        return HttpResponseForbidden("هذا التقرير لصاحب المحل")
    from pos.clinic.services import parse_date

    today = timezone.localdate()
    d1 = parse_date(request.GET.get("d1"), today) if request.GET.get("d1") else today
    d2 = parse_date(request.GET.get("d2"), today) if request.GET.get("d2") else today
    if d2 < d1:
        d1, d2 = d2, d1
    import datetime as dt

    return render(request, "gaming/report.html", dict(r=G.hall_report(d1, d2), d1=d1, d2=d2, today=today,
                                                      week=today - dt.timedelta(days=6), month=today.replace(day=1)))


@login_required
def devices(request):
    """«🎮 الأجهزة والأسعار»: the hall's devices with their single / multi prices, add several at once (PS5 × 4, PS4 × 3…),
    the time rule and the worker's discount cap."""
    from django.contrib import messages
    from django.shortcuts import redirect

    from pos.voids import is_owner

    from .models import HallSettings

    if not is_owner(request.user):
        return HttpResponseForbidden("هذه الصفحة لصاحب المحل")
    s, hs = CompanySettings.get(), HallSettings.get()

    def dec(x, need=False):
        x = str(x or "").replace("٫", ".").replace(",", ".").strip()
        if not x:
            if need:
                raise ValidationError("اكتب سعر الساعة")
            return None
        try:
            v = Decimal(x)
        except Exception:  # noqa: BLE001
            raise ValidationError(f"رقم غير صحيح: {x}")
        if v < 0:
            raise ValidationError("السعر بالسالب")
        return v

    if request.method == "POST":
        P = request.POST
        try:
            op = P.get("op")
            if op == "add":  # several devices of one kind at once
                kind = P.get("kind") if P.get("kind") in dict(Station.KINDS) else "ps5"
                n = int(P.get("count") or 0)
                if not 1 <= n <= 40:
                    raise ValidationError("العدد من 1 إلى 40")
                rate, multi = dec(P.get("rate"), True), dec(P.get("rate_multi"))
                label = {"ps5": "PS5", "ps4": "PS4", "billiard": "بلياردو", "pingpong": "بينج بونج", "vr": "VR", "pc": "كمبيوتر"}.get(kind, "جهاز")
                have = Station.objects.filter(kind=kind).count()
                base = Station.objects.count()
                for i in range(n):
                    Station.objects.create(name=f"{label} {have + i + 1}"[:40], kind=kind, rate=rate, rate_multi=multi, order=base + i + 1)
                messages.success(request, f"أُضيف {n} × {label} ✔")
            elif op == "save":
                st = get_object_or_404(Station, pk=P.get("pk"))
                st.name = (P.get("name") or st.name).strip()[:40] or st.name
                st.kind = P.get("kind") if P.get("kind") in dict(Station.KINDS) else st.kind
                st.rate, st.rate_multi = dec(P.get("rate"), True), dec(P.get("rate_multi"))
                st.active = P.get("active") == "1"
                if not st.active and PlaySession.objects.filter(state="open", segments__station=st, segments__end__isnull=True).exists():
                    raise ValidationError(f"{st.name} شغال الآن — أوقفه بعد قفل حسابه")
                st.save()
                messages.success(request, f"حُفظ {st.name} ✔")
            elif op == "same_price":  # one price for every device of a kind
                kind = P.get("kind")
                rate, multi = dec(P.get("rate"), True), dec(P.get("rate_multi"))
                n = Station.objects.filter(kind=kind, active=True).update(rate=rate, rate_multi=multi)
                messages.success(request, f"تغيّر سعر {n} جهاز — يسري على الجلسات الجديدة ✔")
            elif op == "rules":
                s.gaming_round_minutes, s.gaming_min_minutes, s.gaming_grace_minutes = G.check_rules(P.get("g_round"), P.get("g_min"), P.get("g_grace"))
                s.save(update_fields=["gaming_round_minutes", "gaming_min_minutes", "gaming_grace_minutes"])
                pct = dec(P.get("max_discount_pct")) or Decimal(0)
                if pct > 100:
                    raise ValidationError("النسبة من 0 إلى 100")
                hs.max_discount_pct = pct
                hs.save()
                messages.success(request, "حُفظت طريقة الحساب وحد الخصم ✔")
        except (ValidationError, ValueError) as e:
            for m in getattr(e, "messages", [str(e)]):
                messages.error(request, m)
        return redirect("play_devices")
    kinds = {}
    for st in Station.objects.filter(active=True):
        kinds.setdefault(st.kind, []).append(st)
    return render(request, "gaming/devices.html", dict(
        stations=Station.objects.all(), KINDS=Station.KINDS, hs=hs, s=s, g_round_choices=G.ROUND_CHOICES, g_min_choices=G.MIN_CHOICES,
        rule=G.rule_text(s), by_kind=[(k, dict(Station.KINDS).get(k, k), v) for k, v in kinds.items()]))


@_guard
def receipt(request, pk):
    ses = get_object_or_404(PlaySession.objects.select_related("station", "order"), pk=pk)
    b = G.bill(ses, ses.closed_at)
    return render(request, "gaming/receipt.html", dict(ses=ses, b=b, s=CompanySettings.get(), hide_nav=True, rule=G.rule_text()))
