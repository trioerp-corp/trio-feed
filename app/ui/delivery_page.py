"""1.24 owner pages: «الدليفري» (settings + per-rider report) and «الخدمة ومحطات المطبخ» (service charge %, kitchen stations)."""
import datetime as dt

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from core.models import CompanySettings
from pos import delivery as D
from pos import restaurant as R
from pos.credit.platforms import PLATFORMS
from pos.models import DeliveryOrder


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
        op = request.POST.get("op", "cfg")
        try:
            if op == "step":  # 1.26 the owner / accountant moves an order on the board (same rules as the till)
                d = get_object_or_404(DeliveryOrder, pk=request.POST.get("pk") or 0)
                _branch_ok(request.user, d)
                D.advance(d, request.POST.get("to", ""), by=_who(request.user), rider=request.POST.get("rider"))
                messages.success(request, f"الطلب {d.order.shift_seq or d.order.uid}: {D.STAGE_AR.get(request.POST.get('to', ''), '')} ✔")
            elif op == "cancel":  # «ملغي» = the owner cancels the bill (reversing entries, stock back, the collection back)
                if not owner:
                    raise PermissionDenied
                d = get_object_or_404(DeliveryOrder, pk=request.POST.get("pk") or 0)
                reason = (request.POST.get("reason") or "").strip()
                if not reason:
                    raise ValidationError("اكتب سبب الإلغاء")
                from pos.voids import void_order

                void_order(d.order, f"إلغاء طلب دليفري: {reason}", user=request.user)
                messages.success(request, "أُلغي الطلب والفاتورة بقيد عكسي ✔")
            else:
                if not owner:
                    raise PermissionDenied
                D.set_cfg(on=request.POST.get("on") == "1", fee=request.POST.get("fee") or "0", riders_text=request.POST.get("riders", ""),
                          late={k: request.POST.get(f"late_{k}") for k in D.LATE})
                messages.success(request, "تم الحفظ ✔ — تظهر التغييرات في شاشة الكاشير خلال دقائق أو بعد تحديث الصفحة")
        except ValidationError as e:
            messages.error(request, e.messages[0])
        return redirect(request.get_full_path())
    today = timezone.localdate()
    start = _date(request.GET.get("from"), today.replace(day=1))
    end = _date(request.GET.get("to"), today)
    branch = _my_branch(request.user)
    rows, tot, orders = D.report(start, end, branch)
    raw = D.raw_cfg(s)
    lim, now = D.late_cfg(s), timezone.now()
    board = []
    for d in D.active(branch):
        nxt = D.FLOW[D.FLOW.index(d.stage) + 1] if d.stage in D.FLOW[:3] else ""
        board.append(dict(d=d, since=D.since(d), late=D.late_by(d, now, lim), next=nxt, next_ar=D.STAGE_AR.get(nxt, ""),
                          steps=[dict(x, ar=D.STAGE_AR.get(x.get("s"), "")) for x in d.steps or []]))
    cols = [dict(key=k, ar=D.STAGE_AR[k], rows=[b for b in board if b["d"].stage == k]) for k in D.FLOW]
    return render(request, "ui/delivery.html", dict(raw=raw, riders_text="\n".join(raw.get("riders") or []), employees=D.riders(s) if not raw.get("riders") else [],
                                                    rows=rows, tot=tot, orders=orders, start=start, end=end, owner=owner, lim=lim, cols=cols,
                                                    riders=D.riders(s), open_n=D.open_orders(branch).count(), stage_ar=D.STAGE_AR,
                                                    plat_names={k: f"{v['icon']} {v['name']}" for k, v in PLATFORMS.items()}))


def _who(user):
    return (user.get_full_name() or user.username)[:100]


def _my_branch(user):
    from pos.voids import _my_branch as mine

    return mine(user)


def _branch_ok(user, d):
    b = _my_branch(user)
    if b is not None and d.order.device_id and d.order.device.branch_id != b.pk:
        raise PermissionDenied


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
            elif P.get("op") == "disc":
                from pos.services import set_till_cfg

                set_till_cfg(P.get("disc_max", ""))
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
    from pos.services import till_cfg

    sts = R.stations(s)
    # 1.24.1: only the café's own sections (its activity's + those holding something it sells) — no supermarket leftovers
    from core.activities import current
    from django.db.models import Q

    mine = set(current(s).get("categories") or []) | set(ItemCategory.objects.filter(
        Q(item__sale_price__isnull=False) | Q(item__item_type="recipe"), item__active=True).values_list("name", flat=True))
    cats = list(ItemCategory.objects.filter(name__in=mine).exclude(name__in=["خدمات", "أخرى"]).exclude(name__startswith="مستلزمات تشغيل")
                .exclude(name__startswith="خامات").order_by("name").values_list("name", flat=True))
    cats += [c for st in sts for c in st.get("cats") or [] if c not in cats]  # a section saved before stays visible
    blank = 2 if len(sts) < 2 else 1
    rows = sts + [dict(name="", printer="", cats=[]) for _ in range(blank)]
    prn = kitchen.printers()
    for r in rows:  # a saved printer that is not connected now stays choosable (with a note)
        r["missing"] = bool(prn and r.get("printer") and r["printer"] not in prn)
    return render(request, "ui/restaurant_cfg.html", dict(svc=R.svc_cfg(s), raw=(s.menu_cfg or {}).get("service") or {}, rows=rows, cats=cats,
                                                          till=till_cfg(s), printers=prn, kitchen=s.get_kitchen_ticket_display(),
                                                          main_printer=s.kitchen_printer))
