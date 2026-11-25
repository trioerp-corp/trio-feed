"""Hall bookings (a device, or «any PS5», for a time and a duration) and prepaid hours (packages → a customer's balance of minutes).

Money: a package is sold as a normal sale of its own service item (cash drawer / card, revenue on the day it is sold — the
simplest rule, as most Egyptian halls count it). Using the balance later charges only the minutes that are not covered, so the
same hour is never counted twice as revenue."""
from core import currency as _CUR  # 1.26: the shop's currency (display only)
import datetime as dt
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from ledger.services import q2

from .models import HallCustomer, MinuteMove, Package, PlaySession, Reservation, Station

Z = Decimal(0)


def _name(user):
    return (user.first_name or user.username) if user else ""


def t12(t):
    """«8:00 م» / «11:30 ص» — the hall's way of saying the time."""
    t = timezone.localtime(t) if isinstance(t, dt.datetime) else t
    h = t.hour % 12 or 12
    return f"{h}:{t.minute:02d} {'م' if t.hour >= 12 else 'ص'}"


def kind_name(kind):
    return dict(Station.KINDS).get(kind, kind)


# ---------------------------------------------------------------- bookings
def _overlapping(start, minutes, exclude=None):
    end = start + dt.timedelta(minutes=minutes)
    qs = Reservation.objects.filter(state="booked", start__lt=end, start__gte=start - dt.timedelta(hours=12)).select_related("station")
    return [r for r in qs if r.end > start and r.pk != exclude]


def _kind_of(r):
    return r.station.kind if r.station_id else r.kind


@transaction.atomic
def reserve(customer, start, minutes=60, *, station=None, kind=None, phone="", note="", user=None, exclude=None):
    """Book a device (or any device of a kind). Refused when that device is booked then, or every device of the kind is."""
    customer = (customer or "").strip()[:80]
    if not customer:
        raise ValidationError("اكتب اسم العميل")
    try:
        minutes = int(minutes or 60)
    except (TypeError, ValueError):
        raise ValidationError("المدة غير صحيحة")
    if not 15 <= minutes <= 720:
        raise ValidationError("المدة من ربع ساعة إلى 12 ساعة")
    if start < timezone.now() - dt.timedelta(minutes=10):
        raise ValidationError("لا يمكن الحجز في وقت مضى")
    kind = station.kind if station else (kind if kind in dict(Station.KINDS) else None)
    if not kind:
        raise ValidationError("اختر الجهاز أو نوعه")
    n = Station.objects.filter(active=True, kind=kind).count()
    if not n:
        raise ValidationError(f"لا توجد أجهزة {kind_name(kind)} شغالة")
    others = _overlapping(start, minutes, exclude)
    if station:
        same = [r for r in others if r.station_id == station.pk]
        if same:
            r = same[0]
            raise ValidationError(f"{station.name} محجوز من {t12(r.start)} إلى {t12(r.end)} — {r.customer}")
    if len([r for r in others if _kind_of(r) == kind]) >= n:
        raise ValidationError(f"كل أجهزة {kind_name(kind)} محجوزة في هذا الوقت")
    return Reservation.objects.create(station=station, kind=kind, customer=customer, phone=_digits(phone)[:40], start=start, minutes=minutes,
                                      note=(note or "")[:200], created_by=_name(user))


def set_state(res, state):
    if state not in ("noshow", "cancelled", "booked"):
        raise ValidationError("حالة غير صحيحة")
    if res.state == "arrived":
        raise ValidationError("بدأ العميل اللعب — الحجز انتهى")
    res.state = state
    res.save(update_fields=["state"])
    return res


@transaction.atomic
def start_booking(res, station, user, mode="single"):
    """The booked customer arrived: his device starts with his name and the booked time (and his package balance, if any)."""
    from . import services as G

    if res.state != "booked":
        raise ValidationError("هذا الحجز ليس في الانتظار")
    ses = G.open_session(station, user, mode=mode, customer=res.customer, planned_minutes=res.minutes)
    cust = HallCustomer.objects.filter(phone=res.phone).first() if res.phone else None
    if cust:
        ses.hall_customer = cust
        ses.save(update_fields=["hall_customer"])
    res.state, res.session, res.station = "arrived", ses, station
    res.save(update_fields=["state", "session", "station"])
    return ses


def walkin_warning(station, planned_minutes=None, now=None):
    """Starting a walk-in now on this device: a booking that would be hit (the device's own, or every device of the kind taken)."""
    now = now or timezone.now()
    span = int(planned_minutes or 60)
    near = [r for r in _overlapping(now, span) if _kind_of(r) == station.kind]
    own = [r for r in near if r.station_id == station.pk]
    if own:
        r = own[0]
        return f"{station.name} محجوز {t12(r.start)} — {r.customer}" + ("" if planned_minutes else " (خلال الساعة القادمة)")
    anyk = [r for r in near if not r.station_id]
    if anyk:
        busy = set(PlaySession.objects.filter(state="open", segments__end__isnull=True).values_list("segments__station", flat=True))
        free = Station.objects.filter(active=True, kind=station.kind).exclude(pk__in=busy).exclude(pk=station.pk)
        free = [s for s in free if not any(r.station_id == s.pk for r in near)]
        if len(free) < len(anyk):
            r = anyk[0]
            return f"حجز «{r.where}» {t12(r.start)} — {r.customer}: لن يبقى جهاز متاح له"
    return ""


def day_bookings(day):
    tz = timezone.get_current_timezone()
    a = timezone.make_aware(dt.datetime.combine(day, dt.time.min), tz)
    return Reservation.objects.filter(start__gte=a, start__lt=a + dt.timedelta(days=1)).select_related("station", "session")


def upcoming(now=None):
    """Today's bookings still to come (or late by up to an hour), for the hall screen."""
    now = now or timezone.now()
    end = timezone.make_aware(dt.datetime.combine(timezone.localdate() + dt.timedelta(days=1), dt.time(3)), timezone.get_current_timezone())
    return list(Reservation.objects.filter(state="booked", start__gte=now - dt.timedelta(hours=1), start__lt=end).select_related("station"))


def as_json(r, now=None):
    now = now or timezone.now()
    return dict(id=r.pk, station=r.station_id, where=r.where, kind=r.kind, customer=r.customer, phone=r.phone, at=t12(r.start),
                start=r.start.isoformat(), minutes=r.minutes, late=r.start < now - dt.timedelta(minutes=10), note=r.note)


# ---------------------------------------------------------------- packages and the customer's balance
def _digits(s):
    s = str(s or "").translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    return "".join(ch for ch in s if "0" <= ch <= "9")


def phone_of(s):
    p = _digits(s)
    if p.startswith("20") and len(p) == 12:
        p = "0" + p[2:]
    if len(p) < 8:
        raise ValidationError("اكتب رقم موبايل العميل صحيحًا")
    return p


@transaction.atomic
def save_package(name, minutes, price, kind="", pkg=None):
    from inventory.models import Item

    from .services import play_item

    name = (name or "").strip()[:60]
    try:
        minutes, price = int(Decimal(str(minutes))), q2(Decimal(str(price)))
    except Exception:  # noqa: BLE001
        raise ValidationError("اكتب عدد الساعات والسعر أرقامًا")
    if not name:
        raise ValidationError("اكتب اسم الباقة")
    if not 15 <= minutes <= 100 * 60:
        raise ValidationError("عدد الساعات من ربع ساعة إلى 100 ساعة")
    if price <= 0:
        raise ValidationError("اكتب سعر الباقة")
    kind = kind if kind in dict(Station.KINDS) else ""
    if pkg is None:
        cat = play_item().category
        n = Package.objects.count() + 1
        while Item.objects.filter(code=f"PKG{n}").exists():
            n += 1
        # no sale price on the item: a package is sold only from the hall screen (never on the till or the QR menu)
        it = Item.objects.create(code=f"PKG{n}", name=f"باقة: {name}"[:200], category=cat, unit="باقة", sale_price=None, item_type="service")
        return Package.objects.create(name=name, minutes=minutes, price=price, kind=kind, item=it)
    pkg.name, pkg.minutes, pkg.price, pkg.kind = name, minutes, price, kind
    pkg.save()
    pkg.item.name = f"باقة: {name}"[:200]
    pkg.item.save(update_fields=["name"])
    return pkg


def balance(cust):
    """{kind: minutes} — kind "" = any device."""
    out = {}
    for row in cust.moves.values("kind").annotate(m=Sum("minutes")):
        if row["m"]:
            out[row["kind"]] = row["m"]
    return out


def usable(cust, kind):
    b = balance(cust)
    return (max(0, b.get(kind, 0)) if kind else 0) + max(0, b.get("", 0))


@transaction.atomic
def sell_package(pkg, name, phone, user, paid_card=0):
    """The customer pays the package at the hall: a normal sale on the hall's drawer, and his balance grows by its minutes."""
    from pos.services import ingest_order

    from . import services as G

    if not pkg.active:
        raise ValidationError("هذه الباقة موقوفة")
    sh = G.open_shift()
    if not sh:
        raise ValidationError("افتح الوردية أولًا")
    phone = phone_of(phone)
    name = (name or "").strip()[:80]
    cust = HallCustomer.objects.filter(phone=phone).first()
    if not cust:
        if not name:
            raise ValidationError("اكتب اسم العميل")
        cust = HallCustomer.objects.create(name=name, phone=phone)
    elif name and name != cust.name:
        cust.name = name
        cust.save(update_fields=["name"])
    card = q2(Decimal(str(paid_card or 0)))
    if card < 0 or card > pkg.price:
        raise ValidationError("المدفوع بالبطاقة أكبر من سعر الباقة")
    d, now = G.device(), timezone.now()
    n = MinuteMove.objects.filter(minutes__gt=0, order__isnull=False, at__date=timezone.localdate()).count() + 1
    from pos.models import Order

    while Order.objects.filter(uid=f"{d.branch.code}-{d.code}-K{now:%y%m%d}-{n}").exists():
        n += 1
    order, _ = ingest_order(d, dict(uid=f"{d.branch.code}-{d.code}-K{now:%y%m%d}-{n}", created_at=now.isoformat(), shift_uid=sh.uid,
                                    shift_seq=sh.orders.count() + 1, cashier=_name(user),
                                    lines=[dict(item_code=pkg.item.code, qty="1", price=str(pkg.price), discount="0")],
                                    paid_cash=str(pkg.price - card), paid_card=str(card), paid_credit="0"))
    return MinuteMove.objects.create(customer=cust, kind=pkg.kind, minutes=pkg.minutes, package=pkg, order=order, amount=pkg.price, by=_name(user))


def prepaid_part(cust, kind, billed, time_amount):
    """How much of this bill the balance covers → (minutes, value). Value = the time charge × covered / billed minutes."""
    from .services import half

    if not cust or not billed or not time_amount:
        return 0, Z
    use = min(usable(cust, kind), int(billed))
    if use <= 0:
        return 0, Z
    value = time_amount if use >= billed else min(time_amount, q2(half(time_amount * Decimal(use) / Decimal(billed))))
    return use, value


def deduct(cust, kind, minutes, ses, user):
    """Take the minutes off the balance: the device kind's own package first, then the «any device» one."""
    b = balance(cust)
    left = minutes
    for k in (kind, ""):
        if left <= 0:
            break
        take = min(left, max(0, b.get(k, 0)))
        if take:
            MinuteMove.objects.create(customer=cust, kind=k, minutes=-take, session=ses, by=_name(user))
            left -= take


def find_customer(phone):
    try:
        return HallCustomer.objects.filter(phone=phone_of(phone)).first()
    except ValidationError:
        return None


def customers(q=""):
    qs = HallCustomer.objects.all()
    if q:
        d = _digits(q)
        qs = qs.filter(Q(name__icontains=q) | (Q(phone__contains=d) if d else Q(pk=0)))
    rows = []
    for c in qs[:300]:
        b = balance(c)
        rows.append(dict(c=c, bal=[(kind_name(k) if k else "أي جهاز", m) for k, m in sorted(b.items()) if m], total=sum(b.values())))
    return rows


def on_order_void(order, editing=False):
    """An invoice is cancelled (pos.voids.void_order): a package sale takes its minutes back off the balance — refused when the
    customer has already played part of them (the money of a used package cannot come back whole); a play bill gives back the
    package minutes it had used."""
    moves = list(MinuteMove.objects.select_for_update().filter(order=order, minutes__gt=0).select_related("customer"))
    for mv in moves:
        if editing:
            raise ValidationError("فاتورة الباقة لا تُعدّل — ألغها ثم بع الباقة من جديد")
        left = balance(mv.customer).get(mv.kind, 0)
        if left < mv.minutes:
            used = mv.minutes - max(0, left)
            raise ValidationError(f"لا يمكن إلغاء فاتورة الباقة كلها: استخدم العميل {mv.customer.name} {used} دقيقة منها، "
                                  f"والمتبقي في رصيده {max(0, left)} دقيقة فقط — استخدم «استرداد الدقائق غير المستخدمة» في صفحة الفاتورة")
        MinuteMove.objects.create(customer=mv.customer, kind=mv.kind, minutes=-mv.minutes, package=mv.package, order=order,
                                  amount=-mv.amount, by="إلغاء الفاتورة")
    ses = PlaySession.objects.filter(order=order).first()
    if ses and not editing:
        for mv in MinuteMove.objects.filter(session=ses, minutes__lt=0):
            MinuteMove.objects.create(customer=mv.customer, kind=mv.kind, minutes=-mv.minutes, session=ses, by="إلغاء الفاتورة")


def refund_info(order):
    """A package invoice whose minutes were partly played: what may still come back = the unused minutes × the package's
    rate (price ÷ minutes). None when the invoice is not a package sale, or is cancelled / already refunded."""
    from pos.models import Order

    if not order or order.cancelled or order.is_return:
        return None
    mv = MinuteMove.objects.filter(order=order, minutes__gt=0).select_related("customer", "package").first()
    if not mv or Order.objects.filter(raw__pkg_refund_of=order.uid, cancelled=False).exists():
        return None
    left = min(mv.minutes, max(0, balance(mv.customer).get(mv.kind, 0)))
    amount = q2(mv.amount * Decimal(left) / Decimal(mv.minutes)) if mv.minutes else Z
    return dict(move=mv, customer=mv.customer, minutes=mv.minutes, used=mv.minutes - left, left=left, amount=amount)


@transaction.atomic
def refund_package(order, reason, user):
    """«استرداد الدقائق غير المستخدمة»: the customer gives back the rest of a package he partly played — a return invoice of
    the package item for the unused minutes × the package rate (cash out of the drawer, revenue back), and those minutes
    leave his balance. Owner only (the caller checks)."""
    from pos.services import ingest_order

    from . import services as G

    reason = str(reason or "").strip()
    if not reason:
        raise ValidationError("اكتب السبب")
    MinuteMove.objects.select_for_update().filter(order=order)
    info = refund_info(order)
    if not info:
        raise ValidationError("هذه الفاتورة ليست فاتورة باقة يمكن استرداد جزء منها")
    if info["left"] <= 0 or info["amount"] <= 0:
        raise ValidationError(f"استخدم العميل دقائق الباقة كلها — لا يوجد ما يُسترد")
    mv, d, now = info["move"], G.device(), timezone.now()
    sh = G.open_shift()
    uid = f"{order.uid}-R"
    from pos.models import Order

    n = 1
    while Order.objects.filter(uid=uid).exists():
        n += 1
        uid = f"{order.uid}-R{n}"
    ret, _ = ingest_order(d, dict(uid=uid, created_at=now.isoformat(), shift_uid=sh.uid if sh else "", cashier=_name(user), is_return=True,
                                  pkg_refund_of=order.uid, ret_of=order.uid, note=f"استرداد {info['left']} دقيقة غير مستخدمة: {reason}"[:200],
                                  lines=[dict(item_code=mv.package.item.code if mv.package_id else order.lines.first().item.code, qty="1",
                                              price=str(info["amount"]), discount="0")],
                                  paid_cash=str(info["amount"]), paid_card="0", paid_credit="0"))
    MinuteMove.objects.create(customer=mv.customer, kind=mv.kind, minutes=-info["left"], package=mv.package, order=ret,
                              amount=-info["amount"], by=_name(user))
    order.flags = (f"استُرد منها {_CUR.fmt(info['amount'])} عن {info['left']} دقيقة (فاتورة {uid})؛ " + order.flags)[:300]
    order.save(update_fields=["flags"])
    from core import audit

    audit.log("gaming.package_refund", ret, summary=f"استرداد {_CUR.fmt(info['amount'])} عن {info['left']} دقيقة من باقة {mv.customer.name} — {reason}")
    return ret
