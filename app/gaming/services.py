"""Gaming hall rules: hourly billing with rounding, food & drinks on the device bill, closing into a normal sales invoice."""
import math
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import CompanySettings
from inventory.models import Item, ItemCategory
from ledger.services import q2

from .models import PlayItem, PlaySegment, PlaySession, Station

PLAY_CODE = "PLAY"


ROUND_CHOICES = [(15, "ربع ساعة"), (30, "نصف ساعة"), (60, "ساعة")]
MIN_CHOICES = [(0, "بدون حد أدنى"), (15, "ربع ساعة"), (30, "نصف ساعة"), (60, "ساعة")]


def check_rules(round_m, min_m, grace_m):
    """The hall's billing rule, as chosen at setup (and changed any time): every started quarter / half / full hour is paid,
    after a few grace minutes; and a minimum charge. → (round, min, grace) or ValidationError."""
    from django.core.exceptions import ValidationError

    try:
        r, m, g = int(round_m), int(min_m or 0), int(grace_m or 0)
    except (TypeError, ValueError):
        raise ValidationError("طريقة حساب الوقت: أرقام فقط")
    if r not in dict(ROUND_CHOICES):
        raise ValidationError("يُحسب الوقت بربع الساعة أو بنصف الساعة أو بالساعة")
    if not 0 <= m <= 180:
        raise ValidationError("أقل وقت يُحاسب عليه من 0 إلى 180 دقيقة")
    if not 0 <= g < r:
        raise ValidationError(f"يجب أن يكون السماح أقل من {r} دقيقة")
    return r, m, g


def rule_text(s=None):
    """«يُحسب الوقت بربع الساعة · الحد الأدنى نصف ساعة · سماح 5 دقائق» — shown on the hall screen and the bill."""
    s = s or CompanySettings.get()
    r = dict(ROUND_CHOICES).get(s.gaming_round_minutes, f"{s.gaming_round_minutes} دقيقة")
    parts = ["يُحسب الوقت " + {15: "بربع الساعة", 30: "بنصف الساعة", 60: "بالساعة"}.get(s.gaming_round_minutes, f"كل {r}")]
    if s.gaming_min_minutes:
        parts.append("الحد الأدنى " + dict(MIN_CHOICES).get(s.gaming_min_minutes, f"{s.gaming_min_minutes} دقيقة"))
    if s.gaming_grace_minutes:
        parts.append(f"سماح {s.gaming_grace_minutes} دقائق")
    return " · ".join(parts)


def billed_minutes(raw, s=None):
    """Rounding the hall uses: minimum charge, then per started block (15 min by default) after a few grace minutes."""
    s = s or CompanySettings.get()
    raw = max(0, raw)
    if raw <= 0:
        return 0
    if raw < s.gaming_min_minutes:
        return s.gaming_min_minutes
    r = s.gaming_round_minutes
    if r <= 1:
        return math.ceil(raw)
    full = math.floor(raw / r) * r
    rem = raw - full
    return int(full + (r if rem > s.gaming_grace_minutes else 0))


def seg_minutes(seg, now=None):
    end = seg.end or now or timezone.now()
    return max(0.0, (end - seg.start).total_seconds() / 60)


def bill(session, now=None):
    """Live or final bill: minutes played, billed minutes, time amount (rates of every segment), items, total."""
    s = CompanySettings.get()
    segs = list(session.segments.select_related("station"))
    raw = sum(seg_minutes(g, now) for g in segs)
    billed = billed_minutes(raw, s) if segs else 0
    value = sum(Decimal(str(seg_minutes(g, now))) * g.rate / 60 for g in segs)
    time_amount = q2(value * Decimal(billed) / Decimal(str(raw))) if raw else Decimal(0)
    items = [dict(id=i.pk, code=i.item.code, name=i.item.name, qty=i.qty, price=i.price, total=q2(i.qty * i.price))
             for i in session.items.select_related("item")]
    items_amount = sum((i["total"] for i in items), Decimal(0))
    return dict(raw=raw, minutes=int(raw), billed=billed, time_amount=time_amount, items=items, items_amount=items_amount,
                total=time_amount + items_amount - session.discount, segments=[
                    dict(station=g.station.name, mode=g.mode, rate=g.rate, start=g.start, end=g.end, minutes=int(seg_minutes(g, now)))
                    for g in segs])


def play_item():
    """The service item every time charge is sold as (no stock, its own revenue account)."""
    it = Item.objects.filter(code=PLAY_CODE).first()
    if it:
        return it
    from ledger.models import Account

    cat = ItemCategory.objects.filter(name="خدمات").first()
    if not cat:
        stock = Account.objects.filter(code="1202010101").first() or Account.objects.filter(is_postable=True, code__startswith="1202").first()
        cat = ItemCategory.objects.create(name="خدمات", stock_account=stock, needs_equipment=False)
    return Item.objects.create(code=PLAY_CODE, name="وقت لعب", category=cat, unit="ساعة", sale_price=0, item_type="service")


def device():
    from pos.models import Branch, Device

    d = Device.objects.filter(code="GM").select_related("branch").first()
    if d:
        return d
    br = Branch.objects.filter(active=True).first()
    if not br:
        raise ValidationError("لا يوجد فرع بيع مسجّل")
    return Device.objects.create(branch=br, code="GM", name="شاشة الصالة")


def open_shift():
    from pos.models import Shift

    return Shift.objects.filter(device=device(), closed_at__isnull=True).order_by("-opened_at").first()


@transaction.atomic
def start_shift(user, opening_float=0):
    from pos.services import ingest_shift

    if open_shift():
        raise ValidationError("توجد وردية مفتوحة بالفعل")
    d, now = device(), timezone.now()
    return ingest_shift(d, dict(uid=f"{d.branch.code}-{d.code}-SH{now:%Y%m%d%H%M%S}", cashier=user.first_name or user.username, verified=True,
                                opened_at=now.isoformat(), opening_float=str(opening_float or 0)))


@transaction.atomic
def end_shift(user, counted):
    from pos.services import ingest_shift

    sh = open_shift()
    if not sh:
        raise ValidationError("لا توجد وردية مفتوحة")
    return ingest_shift(sh.device, dict(uid=sh.uid, closed_at=timezone.now().isoformat(), counted_cash=str(counted or 0)))


def _name(user):
    return (user.first_name or user.username) if user else ""


@transaction.atomic
def open_session(station, user, mode="single", customer="", planned_minutes=None):
    if station and PlaySession.objects.filter(state="open", segments__station=station, segments__end__isnull=True).exists():
        raise ValidationError(f"{station.name} شغال بالفعل")
    now = timezone.now()
    ses = PlaySession.objects.create(station=station, customer=customer[:80], opened_at=now, opened_by=_name(user),
                                     planned_minutes=planned_minutes or None)
    if station:
        PlaySegment.objects.create(session=ses, station=station, mode=mode, rate=station.rate_for(mode), start=now)
    return ses


def _live(ses):
    if ses.state != "open":
        raise ValidationError("هذه الجلسة أُغلقت")
    return ses.segments.filter(end__isnull=True).last()


@transaction.atomic
def switch(ses, user, mode=None, station=None):
    """Single ↔ multi, or move the players to another device: the old segment ends at its rate, a new one starts."""
    seg = _live(ses)
    if not seg:
        raise ValidationError("هذا الطلب بدون جهاز")
    station = station or seg.station
    mode = mode or seg.mode
    if station != seg.station and PlaySession.objects.filter(state="open", segments__station=station, segments__end__isnull=True).exists():
        raise ValidationError(f"{station.name} مشغول")
    now = timezone.now()
    seg.end = now
    seg.save(update_fields=["end"])
    PlaySegment.objects.create(session=ses, station=station, mode=mode, rate=station.rate_for(mode), start=now)
    ses.station = station
    ses.save(update_fields=["station"])


@transaction.atomic
def add_item(ses, item, qty, user, price=None):
    if ses.state != "open":
        raise ValidationError("هذه الجلسة أُغلقت")
    qty = Decimal(str(qty))
    if qty == 0:
        raise ValidationError("الكمية صفر")
    if item.sale_price is None and price is None:
        raise ValidationError(f"{item.name} ملوش سعر بيع")
    line = ses.items.filter(item=item, price=price if price is not None else item.sale_price).first()
    if line:
        line.qty += qty
        if line.qty <= 0:
            line.delete()
        else:
            line.save(update_fields=["qty"])
        return line
    if qty < 0:
        raise ValidationError("الصنف ليس في الطلب")
    return PlayItem.objects.create(session=ses, item=item, qty=qty, price=price if price is not None else item.sale_price, by=_name(user))


@transaction.atomic
def close_session(ses, user, paid_card=0, discount=0, customer_id=None, paid_credit=0):
    """Stop the clock and turn the bill into a normal sales invoice (cash drawer, stock of drinks / ingredients, VAT)."""
    from pos.services import ingest_order

    ses = PlaySession.objects.select_for_update().get(pk=ses.pk)
    if ses.state != "open":
        raise ValidationError("هذه الجلسة أُغلقت من قبل")
    now = timezone.now()
    ses.segments.filter(end__isnull=True).update(end=now)
    ses.discount = q2(Decimal(str(discount or 0)))
    b = bill(ses, now)
    if b["total"] < 0:
        raise ValidationError("الخصم أكبر من الحساب")
    if not b["time_amount"] and not b["items"]:
        ses.state, ses.closed_at, ses.closed_by = "void", now, _name(user)
        ses.save()
        return ses
    lines = []
    if b["time_amount"]:
        lines.append(dict(item_code=play_item().code, qty="1", price=str(b["time_amount"]), discount="0"))
    for i in b["items"]:
        lines.append(dict(item_code=i["code"], qty=str(i["qty"]), price=str(i["price"]), discount="0"))
    if ses.discount:  # the discount goes on the first line (time first, else the first item)
        lines[0]["discount"] = str(ses.discount)
    paid_card, paid_credit = q2(Decimal(str(paid_card or 0))), q2(Decimal(str(paid_credit or 0)))
    if paid_card + paid_credit > b["total"]:
        raise ValidationError("المدفوع بطاقة/آجل أكبر من الحساب")
    d, sh = device(), open_shift()
    seq = (sh.orders.count() + 1) if sh else None
    order, _ = ingest_order(d, dict(uid=f"{d.branch.code}-{d.code}-P{ses.pk}", created_at=now.isoformat(), shift_uid=sh.uid if sh else None,
                                    shift_seq=seq, lines=lines, paid_cash=str(b["total"] - paid_card - paid_credit), paid_card=str(paid_card),
                                    paid_credit=str(paid_credit), customer_id=customer_id, cashier=_name(user)))
    ses.state, ses.closed_at, ses.closed_by, ses.order = "closed", now, _name(user), order
    ses.minutes, ses.billed_minutes, ses.time_amount, ses.items_amount, ses.total = b["minutes"], b["billed"], b["time_amount"], b["items_amount"], b["total"]
    ses.save()
    return ses


@transaction.atomic
def cancel_session(ses, user, owner=False):
    """An open session opened by mistake: the cashier can cancel it in the first 5 minutes if nothing was ordered; the owner always."""
    if ses.state != "open":
        raise ValidationError("هذه الجلسة أُغلقت — إلغاء الفاتورة من «المبيعات» بحساب صاحب المحل")
    age = (timezone.now() - ses.opened_at).total_seconds() / 60
    if not owner and (age > 5 or ses.items.exists()):
        raise ValidationError("الإلغاء بعد 5 دقائق أو بعد إضافة طلبات يحتاج صاحب المحل")
    now = timezone.now()
    ses.segments.filter(end__isnull=True).update(end=now)
    ses.state, ses.closed_at, ses.closed_by = "void", now, _name(user)
    ses.save()


def stations_state(now=None):
    now = now or timezone.now()
    live = {}
    for ses in PlaySession.objects.filter(state="open").prefetch_related("segments__station", "items__item"):
        seg = ses.segments.filter(end__isnull=True).last()
        b = bill(ses, now)
        info = dict(id=ses.pk, customer=ses.customer, opened_at=ses.opened_at.isoformat(), mode=seg.mode if seg else "",
                    rate=str(seg.rate) if seg else "0", minutes=b["minutes"], billed=b["billed"], time_amount=str(b["time_amount"]),
                    items_amount=str(b["items_amount"]), total=str(b["total"]), planned=ses.planned_minutes,
                    items=[dict(id=i["id"], code=i["code"], name=i["name"], qty=str(i["qty"]), price=str(i["price"]), total=str(i["total"]))
                           for i in b["items"]], segments=[dict(station=g["station"], mode=g["mode"], rate=str(g["rate"]), minutes=g["minutes"])
                                                           for g in b["segments"]])
        live[seg.station_id if seg else f"t{ses.pk}"] = info
    out = []
    for st in Station.objects.filter(active=True):
        out.append(dict(id=st.pk, name=st.name, kind=st.kind, kind_name=st.get_kind_display(), rate=str(st.rate),
                        rate_multi=str(st.rate_multi) if st.rate_multi else None, session=live.pop(st.pk, None)))
    takeaway = [v for k, v in live.items() if isinstance(k, str)]
    return out, takeaway
