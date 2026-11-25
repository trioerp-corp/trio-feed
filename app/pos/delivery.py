"""Delivery (1.24) — restaurants / cafés, pharmacies, supermarkets and shops (not the clinic, not the gaming hall).

The cashier rings a delivery order like any sale, with the customer's phone / name / address, a delivery fee and the rider.
Its money is «مع الطيار»: the sale posts Dr «دليفري تحت التحصيل (مع الطيار)» instead of the drawer. When the rider comes back
with the money, «تم التحصيل» at a till is a cash paper (CashMove kind «delivery»): Dr the drawer / Cr the same account, so the
cash enters the shift that received it. The fee is a line of the service item «خدمة توصيل» (service revenue, like play time).

Settings live in CompanySettings.menu_cfg["delivery"] = {on, fee, riders: [names], late: {new, prep, out}}; no riders listed →
the employees.

1.26 tracking: every delivery (and every Talabat / Elmenus) order walks «جديد → قيد التحضير → خرج مع عامل التوصيل → تم التسليم»
(DeliveryOrder.stage + steps: when and who). The kitchen ticket printed with the sale is «قيد التحضير»; the rider is given at «خرج».
A till sends each step in its sync (dsteps); the program only moves forward (advance) — a step back is refused; «ملغي» is the
owner's cancel of the bill (voids.void_order, or his approval of the cashier's request). «تم التحصيل» (the money) stays apart.
"""
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone

from core.models import CompanySettings
from ledger import services as L
from ledger.models import Account

from .models import CashMove, DeliveryCustomer, DeliveryOrder

BIZ = ("market", "retail", "cafe")  # every business except the clinic and the gaming hall
FEE_CODE = "TRIO-DLV"
FLOW = ("new", "prep", "out", "done")
STAGE_AR = dict(new="جديد", prep="قيد التحضير", out="خرج مع عامل التوصيل", done="تم التسليم", void="ملغي")
LATE = dict(new=10, prep=20, out=45)  # minutes in a step before the order is flagged late (the owner changes them)
MAX_FEE = Decimal("5000")
Z = Decimal(0)


def _dec(x, default=Z):
    try:
        v = Decimal(str(x).replace(",", ".").strip())
    except (InvalidOperation, ValueError):
        return default
    return v if v.is_finite() else default


def raw_cfg(s=None):
    s = s or CompanySettings.get()
    return dict((s.menu_cfg or {}).get("delivery") or {})


def riders(s=None):
    s = s or CompanySettings.get()
    names = [str(x).strip()[:80] for x in raw_cfg(s).get("riders") or [] if str(x).strip()]
    if names:
        return names
    from ledger.models import Partner

    return list(Partner.objects.filter(kind="employee", active=True, merged_into__isnull=True).order_by("name").values_list("name", flat=True)[:50])


def cfg(s=None):
    s = s or CompanySettings.get()
    c = raw_cfg(s)
    on = s.business in BIZ and bool(c.get("on"))
    return dict(on=on, fee=str(L.q2(_dec(c.get("fee")))), riders=riders(s) if on else [], code=FEE_CODE if on else "", late=late_cfg(s))


def late_cfg(s=None):
    got = raw_cfg(s).get("late") or {}
    out = {}
    for k, v in LATE.items():
        try:
            out[k] = max(1, min(600, int(got.get(k, v))))
        except (TypeError, ValueError):
            out[k] = v
    return out


def set_cfg(on=None, fee=None, riders_text=None, late=None):
    s = CompanySettings.get()
    m = dict(s.menu_cfg or {})
    c = dict(m.get("delivery") or {})
    if on is not None:
        c["on"] = bool(on)
    if fee is not None:
        f = _dec(fee, None)
        if f is None or f < 0 or f > MAX_FEE:
            raise ValidationError("رسم التوصيل غير صحيح")
        c["fee"] = str(L.q2(f))
    if riders_text is not None:
        names = []
        for x in str(riders_text).replace("،", "\n").replace(",", "\n").splitlines():
            x = x.strip()[:80]
            if x and x not in names:
                names.append(x)
        c["riders"] = names[:50]
    if late is not None:
        new = {}
        for k in LATE:
            try:
                v = int(str(late.get(k) or "").strip() or LATE[k])
            except ValueError:
                raise ValidationError("مدة التأخير بالدقائق (رقم صحيح)")
            if not 1 <= v <= 600:
                raise ValidationError("مدة التأخير من 1 إلى 600 دقيقة")
            new[k] = v
        c["late"] = new
    m["delivery"] = c
    s.menu_cfg = m
    s.save(update_fields=["menu_cfg"])
    if c.get("on"):
        fee_item()
    return cfg(s)


def _services_category():
    from inventory.models import ItemCategory

    cat = ItemCategory.objects.filter(name="خدمات").first()
    if not cat:
        stock = Account.objects.filter(code="1202010101").first() or Account.objects.filter(is_postable=True, code__startswith="1202").first()
        cat = ItemCategory.objects.create(name="خدمات", stock_account=stock, needs_equipment=False)
    return cat


def service_item(code, name):
    """A service item the till adds by itself (delivery fee, service charge): no stock, service revenue, never on the grid
    (no sale price, so the catalog leaves it out)."""
    from inventory.models import Item

    it = Item.objects.filter(code=code).first()
    if it:
        if not it.active:
            it.active = True
            it.save(update_fields=["active"])
        return it
    return Item.objects.create(code=code, name=name, category=_services_category(), unit="خدمة", sale_price=None, item_type="service")


def fee_item():
    return service_item(FEE_CODE, "خدمة توصيل")


def clearing():
    """Dr when a delivery sale goes out with the rider, Cr when he hands the money in."""
    acc = Account.objects.filter(code="1207010109").first()
    if acc:
        return acc
    return Account.objects.create(code="1207010109", name="دليفري تحت التحصيل (مع الطيار)", parent=Account.objects.get(code="12070101"), level=6,
                                  is_postable=True, account_type="asset", normal_balance="debit", report="balance_sheet",
                                  cash_flow_category="cash")


def book(limit=2000):
    """The address book for the till (works offline): the most recent customers first."""
    return [dict(p=c.phone, n=c.name, a=c.address) for c in DeliveryCustomer.objects.order_by("-last_at")[:limit]]


def clean_info(d):
    d = d if isinstance(d, dict) else {}
    phone = "".join(ch for ch in str(d.get("phone") or "") if ch.isdigit() or ch == "+")[:30]
    return dict(phone=phone, name=str(d.get("name") or "").strip()[:120], address=str(d.get("address") or "").strip()[:300],
                rider=str(d.get("rider") or "").strip()[:80])


def _step(stage, at, by="", dev="", u=""):
    return {k: v for k, v in dict(s=stage, at=at.isoformat(timespec="seconds"), by=str(by or "")[:100], dev=dev, u=u).items() if v or k in ("s", "at")}


def record(order, data):
    """Called by ingest_order for a sale with «delivery» or a platform order: the delivery paper + the address book."""
    info = clean_info(data.get("delivery"))
    plat = str(data.get("platform") or "")[:12] if order.paid_credit > 0 else ""
    fee = sum((_dec(ln.get("qty"), Z) * _dec(ln.get("price"), Z) for ln in data.get("lines") or [] if str(ln.get("item_code")) == FEE_CODE), Z)
    state = "out" if order.paid_rider > 0 else "paid"
    by = order.shift.cashier if order.shift_id else ""
    dev = order.device.code if order.device_id else ""
    steps = [_step("new", order.created_at_device, by, dev)]
    s = CompanySettings.get()
    if s.kitchen_ticket != "off" and s.business in ("cafe", "gaming"):  # the kitchen ticket printed with the sale = «قيد التحضير»
        steps.append(_step("prep", order.created_at_device, "ورقة المطبخ", dev))
    d = DeliveryOrder.objects.create(order=order, fee=L.q2(fee), due=order.paid_rider, state=state, created_at=order.created_at_device,
                                     collected_at=order.created_at_device if state == "paid" else None, stage=steps[-1]["s"], steps=steps,
                                     platform=plat, **info)
    if info["phone"]:
        c, _ = DeliveryCustomer.objects.get_or_create(phone=info["phone"])
        c.name = info["name"] or c.name
        c.address = info["address"] or c.address
        c.orders += 1
        c.last_at = max(filter(None, [c.last_at, order.created_at_device]))
        c.save()
    return d


def open_orders(branch=None):
    """Still with a rider (money not handed in yet)."""
    qs = DeliveryOrder.objects.filter(state="out", order__cancelled=False).select_related("order", "order__device")
    if branch is not None:
        qs = qs.filter(order__device__branch=branch)
    return qs.order_by("created_at")


def active(branch=None):
    """The board: every order not delivered yet, and delivered ones whose money is still with the rider."""
    qs = DeliveryOrder.objects.filter(order__cancelled=False).exclude(stage="void").filter(Q(stage__in=FLOW[:3]) | Q(state="out"))
    if branch is not None:
        qs = qs.filter(order__device__branch=branch)
    return qs.select_related("order", "order__device").order_by("created_at")


def since(d):
    """When the order entered its current step."""
    for st in reversed(d.steps or []):
        if st.get("s") == d.stage:
            return st.get("at") or d.created_at.isoformat()
    return d.created_at.isoformat()


def late_by(d, now=None, lim=None):
    """Minutes over the owner's limit for the current step (0 = on time)."""
    lim = lim or late_cfg()
    if d.stage not in lim:
        return 0
    from .services import _dt

    m = int(((now or timezone.now()) - _dt(since(d))).total_seconds() // 60)
    return max(0, m - lim[d.stage])


def as_json(d):
    return dict(uid=d.order.uid, seq=d.order.shift_seq, name=d.name, phone=d.phone, address=d.address, rider=d.rider, due=str(d.due),
                total=str(d.order.total), at=d.created_at.isoformat(), stage=d.stage, since=since(d), plat=d.platform, paid=d.state == "paid",
                steps=[dict(s=x.get("s"), at=x.get("at"), by=x.get("by", "")) for x in d.steps or []])


def open_json(branch=None):
    return [as_json(d) for d in active(branch)[:300]]


@transaction.atomic
def advance(d, to, *, by="", at=None, rider=None, device=None, uid=""):
    """One step forward on the way to the customer. A step already made is a no-op (a till sends again); backwards is refused."""
    d = DeliveryOrder.objects.select_for_update().select_related("order", "order__device").get(pk=d.pk)
    if uid and any(x.get("u") == uid for x in d.steps or []):
        return d
    if d.stage == "void" or d.order.cancelled:
        raise ValidationError("الطلب ملغي")
    if to not in FLOW:
        raise ValidationError("مرحلة غير معروفة" if to != "void" else "الإلغاء من صاحب المحل فقط (طلب إلغاء الفاتورة)")
    if device is not None and d.order.device_id and d.order.device.branch_id != device.branch_id:
        raise ValidationError("طلب الدليفري من فرع آخر")
    if FLOW.index(to) <= FLOW.index(d.stage):
        if to == d.stage:
            return d
        raise ValidationError(f"الطلب «{STAGE_AR[d.stage]}» — لا يرجع إلى «{STAGE_AR[to]}»")
    fields = ["stage", "steps"]
    rider = str(rider or "").strip()[:80]
    if rider and to == "out":
        d.rider = rider
        fields.append("rider")
    if to == "out" and not d.rider and not d.platform and raw_cfg().get("riders"):
        raise ValidationError("اختر عامل التوصيل")
    d.stage = to
    d.steps = [*(d.steps or []), _step(to, at or timezone.now(), by, device.code if device is not None else "", uid)]
    d.save(update_fields=fields)
    return d


def ingest_step(device, data):
    """A step sent by a till (offline-safe). Only «not arrived yet» is retried; a refused step is answered, never retried."""
    uid = str(data.get("uid") or "")[:60]
    if not uid.startswith(f"{device.branch.code}-{device.code}-"):
        raise ValidationError("المعرف لا يخص هذا الجهاز")
    d = DeliveryOrder.objects.filter(order__uid=str(data.get("order_uid") or "")).first()
    if not d:
        raise ValidationError("طلب الدليفري لم يصل إلى البرنامج بعد — يُعاد تلقائيًا")
    from .models import Shift
    from .services import _dt

    sh = Shift.objects.filter(uid=str(data.get("shift_uid") or ""), device=device).first()
    try:
        advance(d, str(data.get("to") or ""), by=sh.cashier if sh else str(data.get("by") or "")[:100],
                at=_dt(data["at"]) if data.get("at") else None, rider=data.get("rider"), device=device, uid=uid)
    except ValidationError as e:
        return "؛ ".join(e.messages)
    return ""


@transaction.atomic
def collect(device, data, shift=None):
    """«تم التحصيل»: the rider handed the money in at this till. The amount is the program's, never the till's."""
    uid = str(data["uid"])
    ex = CashMove.objects.filter(uid=uid, kind="delivery").first()
    if ex:
        return ex
    if CashMove.objects.filter(uid=uid).exists():
        raise ValidationError("رقم الحركة مستخدم من قبل")
    d = DeliveryOrder.objects.select_for_update().select_related("order", "order__device__branch").filter(order__uid=str(data.get("order_uid") or "")).first()
    if not d:
        raise ValidationError("طلب الدليفري لم يصل إلى البرنامج بعد — يُعاد تلقائيًا")
    if d.order.cancelled or d.state == "void":
        raise ValidationError("طلب الدليفري ملغي")
    if d.state == "paid":
        raise ValidationError(f"طلب الدليفري {d.order.uid} حُصّل من قبل")
    if d.order.device.branch_id != device.branch_id:
        raise ValidationError("طلب الدليفري من فرع آخر")
    amount = d.due
    box = device.branch.cashbox.account
    when = timezone.now()
    created = data.get("created_at")
    if created:
        from .services import _dt

        when = _dt(created)
    desc = f"تحصيل دليفري {d.order.uid} — عامل التوصيل {d.rider or '—'}"
    entry = L.create_entry("JV", when.date(), desc, [dict(account=box, debit=amount, description=desc),
                                                     dict(account=clearing(), credit=amount, description=desc)],
                           source=f"pos.cashmove:{uid}", legacy_ref=uid[:30])
    mv = CashMove.objects.create(uid=uid, kind="delivery", date=when.date(), shift=shift, cash_effect=amount, amount=amount,
                                 note=f"{d.rider} — {d.order.uid}"[:200], entry=entry, by=(shift.cashier if shift else "")[:100])
    d.state, d.collected_at, d.collect_move = "paid", when, mv
    fields = ["state", "collected_at", "collect_move"]
    if d.stage in FLOW[:3]:  # the money is back: the order was delivered
        d.stage, d.steps = "done", [*(d.steps or []), _step("done", when, mv.by, device.code, uid)]
        fields += ["stage", "steps"]
    d.save(update_fields=fields)
    return mv


def on_order_void(order, reason=""):
    for d in DeliveryOrder.objects.filter(order=order).exclude(stage="void"):
        d.stage, d.steps = "void", [*(d.steps or []), _step("void", timezone.now(), str(reason)[:100])]
        if d.state == "out":
            d.state = "void"
        d.save(update_fields=["stage", "steps", "state"])


def on_move_void(mv):
    if mv.kind == "delivery":
        DeliveryOrder.objects.filter(collect_move=mv).update(state="out", collected_at=None, collect_move=None)


def _minutes(d, a, b):
    """Minutes between two steps of an order (None when one is missing)."""
    from .services import _dt

    at = {x.get("s"): x.get("at") for x in d.steps or []}
    if not at.get(a) or not at.get(b):
        return None
    return max(0, int((_dt(at[b]) - _dt(at[a])).total_seconds() // 60))


def report(start, end, branch=None):
    """Per rider: orders, total, collected, outstanding, delivered, average minutes order → customer (the owner's page);
    the history lists platform orders too."""
    qs = DeliveryOrder.objects.filter(created_at__date__gte=start, created_at__date__lte=end).exclude(stage="void").exclude(
        order__cancelled=True)
    if branch is not None:
        qs = qs.filter(order__device__branch=branch)
    own = qs.filter(platform="")
    rows = []
    for r in own.values("rider").annotate(n=Count("id"), total=Sum("order__total"), fees=Sum("fee"), done=Count("id", filter=Q(stage="done")),
                                          out=Sum("due", filter=Q(state="out")), got=Sum("order__total", filter=Q(state="paid"))).order_by("rider"):
        mins = [m for m in (_minutes(d, "new", "done") for d in own.filter(rider=r["rider"], stage="done").only("steps")[:2000]) if m is not None]
        rows.append(dict(rider=r["rider"] or "بدون طيار", n=r["n"], total=r["total"] or Z, fees=r["fees"] or Z, got=r["got"] or Z, out=r["out"] or Z,
                         done=r["done"], avg=round(sum(mins) / len(mins)) if mins else None))
    tot = dict(n=sum(r["n"] for r in rows), total=sum((r["total"] for r in rows), Z), fees=sum((r["fees"] for r in rows), Z),
               got=sum((r["got"] for r in rows), Z), out=sum((r["out"] for r in rows), Z), done=sum(r["done"] for r in rows))
    hist = DeliveryOrder.objects.filter(created_at__date__gte=start, created_at__date__lte=end)
    if branch is not None:
        hist = hist.filter(order__device__branch=branch)
    return rows, tot, hist.select_related("order").order_by("-created_at")[:300]
