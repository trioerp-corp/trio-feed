"""Delivery (1.24) — restaurants / cafés, pharmacies, supermarkets and shops (not the clinic, not the gaming hall).

The cashier rings a delivery order like any sale, with the customer's phone / name / address, a delivery fee and the rider.
Its money is «مع الطيار»: the sale posts Dr «دليفري تحت التحصيل (مع الطيار)» instead of the drawer. When the rider comes back
with the money, «تم التحصيل» at a till is a cash paper (CashMove kind «delivery»): Dr the drawer / Cr the same account, so the
cash enters the shift that received it. The fee is a line of the service item «خدمة توصيل» (service revenue, like play time).

Settings live in CompanySettings.menu_cfg["delivery"] = {on, fee, riders: [names]}; no riders listed → the employees.
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
    return dict(on=on, fee=str(L.q2(_dec(c.get("fee")))), riders=riders(s) if on else [], code=FEE_CODE if on else "")


def set_cfg(on=None, fee=None, riders_text=None):
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


def record(order, data):
    """Called by ingest_order for a sale with «delivery»: the delivery paper + the address book."""
    info = clean_info(data.get("delivery"))
    fee = sum((_dec(ln.get("qty"), Z) * _dec(ln.get("price"), Z) for ln in data.get("lines") or [] if str(ln.get("item_code")) == FEE_CODE), Z)
    state = "out" if order.paid_rider > 0 else "paid"
    d = DeliveryOrder.objects.create(order=order, fee=L.q2(fee), due=order.paid_rider, state=state, created_at=order.created_at_device,
                                     collected_at=order.created_at_device if state == "paid" else None, **info)
    if info["phone"]:
        c, _ = DeliveryCustomer.objects.get_or_create(phone=info["phone"])
        c.name = info["name"] or c.name
        c.address = info["address"] or c.address
        c.orders += 1
        c.last_at = max(filter(None, [c.last_at, order.created_at_device]))
        c.save()
    return d


def open_orders(branch=None):
    qs = DeliveryOrder.objects.filter(state="out", order__cancelled=False).select_related("order", "order__device")
    if branch is not None:
        qs = qs.filter(order__device__branch=branch)
    return qs.order_by("created_at")


def open_json(branch=None):
    return [dict(uid=d.order.uid, seq=d.order.shift_seq, name=d.name, phone=d.phone, address=d.address, rider=d.rider, due=str(d.due),
                 at=d.created_at.isoformat()) for d in open_orders(branch)[:200]]


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
    d.save(update_fields=["state", "collected_at", "collect_move"])
    return mv


def on_order_void(order):
    DeliveryOrder.objects.filter(order=order, state="out").update(state="void")


def on_move_void(mv):
    if mv.kind == "delivery":
        DeliveryOrder.objects.filter(collect_move=mv).update(state="out", collected_at=None, collect_move=None)


def report(start, end, branch=None):
    """Per rider: orders, total, collected, outstanding (the owner's page)."""
    qs = DeliveryOrder.objects.filter(created_at__date__gte=start, created_at__date__lte=end).exclude(state="void").exclude(order__cancelled=True)
    if branch is not None:
        qs = qs.filter(order__device__branch=branch)
    rows = []
    for r in qs.values("rider").annotate(n=Count("id"), total=Sum("order__total"), fees=Sum("fee"),
                                         out=Sum("due", filter=Q(state="out")), got=Sum("order__total", filter=Q(state="paid"))).order_by("rider"):
        rows.append(dict(rider=r["rider"] or "بدون طيار", n=r["n"], total=r["total"] or Z, fees=r["fees"] or Z, got=r["got"] or Z, out=r["out"] or Z))
    tot = dict(n=sum(r["n"] for r in rows), total=sum((r["total"] for r in rows), Z), fees=sum((r["fees"] for r in rows), Z),
               got=sum((r["got"] for r in rows), Z), out=sum((r["out"] for r in rows), Z))
    return rows, tot, qs.select_related("order").order_by("-created_at")[:300]
