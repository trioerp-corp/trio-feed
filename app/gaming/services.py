"""Gaming hall rules: hourly billing with rounding, food & drinks on the device bill, closing into a normal sales invoice."""
from core import currency as _CUR  # 1.26: the shop's currency (display only)
import math
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import CompanySettings
from inventory.models import Item, ItemCategory
from ledger.services import q2

from .models import HallSettings, PlayItem, PlaySegment, PlaySession, Station

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


def half(x):
    """The time charge to the nearest half pound (no 15.03 / 15.81 after a move or a rate change)."""
    return (Decimal(str(x)) * 2).quantize(Decimal(1), rounding=ROUND_HALF_UP) / 2


def bill(session, now=None):
    """Live or final bill: minutes played, billed minutes, time amount (rates of every segment), items, total."""
    s = CompanySettings.get()
    segs = list(session.segments.select_related("station"))
    raw = sum(seg_minutes(g, now) for g in segs)
    billed = billed_minutes(raw, s) if segs else 0
    value = sum(Decimal(str(seg_minutes(g, now))) * g.rate / 60 for g in segs)
    time_amount = q2(half(value * Decimal(billed) / Decimal(str(raw)))) if raw else Decimal(0)
    items = [dict(id=i.pk, code=i.item.code, name=i.item.name, qty=i.qty, price=i.price, total=q2(i.qty * i.price))
             for i in session.items.select_related("item")]
    items_amount = sum((i["total"] for i in items), Decimal(0))
    return dict(raw=raw, minutes=int(raw), billed=billed, time_amount=time_amount, items=items, items_amount=items_amount,
                total=time_amount + items_amount - session.discount, segments=[
                    dict(station=g.station.name, mode=g.mode, rate=g.rate, start=g.start, end=g.end, minutes=int(seg_minutes(g, now)),
                         multi=bool(g.station.rate_multi))
                    for g in segs if seg_minutes(g, now) >= 1 or g.end is None])  # a move / switch within a minute is no line


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
    no = None
    if not station:  # takeaway: numbered from 1 every day («تيك أواي #3»), not by the database id
        no = PlaySession.objects.filter(station__isnull=True, opened_at__date=timezone.localdate()).count() + 1
    ses = PlaySession.objects.create(station=station, customer=customer[:80], opened_at=now, opened_by=_name(user),
                                     planned_minutes=planned_minutes or None, daily_no=no)
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
    if (now - seg.start).total_seconds() < 60:  # changed within the first minute (a mistake / move then multi): no zero-minute line
        seg.station, seg.mode, seg.rate = station, mode, station.rate_for(mode)
        seg.save(update_fields=["station", "mode", "rate"])
    else:
        seg.end = now
        seg.save(update_fields=["end"])
        PlaySegment.objects.create(session=ses, station=station, mode=mode, rate=station.rate_for(mode), start=now)
    ses.station = station
    ses.save(update_fields=["station"])


@transaction.atomic
def extend(ses, minutes=30):
    """«+30 د»: more booked time on a running device (or a booked time on an open one)."""
    if ses.state != "open":
        raise ValidationError("هذه الجلسة أُغلقت")
    if not ses.station_id:
        raise ValidationError("هذا الطلب بدون جهاز")
    if ses.planned_minutes:
        ses.planned_minutes += int(minutes)
    else:  # an open session: the booked time becomes what is played so far + 30
        played = int((timezone.now() - ses.opened_at).total_seconds() // 60)
        ses.planned_minutes = played + int(minutes)
    ses.save(update_fields=["planned_minutes"])
    return ses


def discount_cap(gross):
    """The largest discount the hall worker gives alone (the owner's % of the bill)."""
    return q2(Decimal(str(gross)) * HallSettings.get().max_discount_pct / 100)


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
def close_session(ses, user, paid_card=0, discount=0, customer_id=None, paid_credit=0, approver=None, customer_new=None, prepaid=None):
    """Stop the clock and turn the bill into a normal sales invoice (cash drawer, stock of drinks / ingredients, VAT).
    A discount above the owner's cap needs the owner (`approver` = his name, checked by the view). Play time is sold as hours:
    quantity = billed minutes / 60, so the sales show 1.25 + 1.75 hours, not «2 hours» for two sessions."""
    from pos.services import ingest_order

    ses = PlaySession.objects.select_for_update().get(pk=ses.pk)
    if ses.state != "open":
        raise ValidationError("هذه الجلسة أُغلقت من قبل")
    now = timezone.now()
    ses.segments.filter(end__isnull=True).update(end=now)
    ses.discount = q2(Decimal(str(discount or 0)))
    b = bill(ses, now)
    use, pre_value = 0, Decimal(0)
    if prepaid and ses.station_id:  # the customer's package balance covers part (or all) of the time
        from . import bookings as BK

        use, pre_value = BK.prepaid_part(prepaid, ses.station.kind, b["billed"], b["time_amount"])
        if use:
            b["full_time"], b["time_amount"] = b["time_amount"], b["time_amount"] - pre_value
            b["total"] -= pre_value
    if b["total"] < 0:
        raise ValidationError("الخصم أكبر من الحساب")
    if ses.discount < 0:
        raise ValidationError("الخصم بالسالب")
    gross = b["time_amount"] + b["items_amount"]
    if ses.discount and ses.discount > discount_cap(gross) and not approver:
        raise ValidationError(f"الخصم أكبر من {HallSettings.get().max_discount_pct.normalize():f}% من الحساب ({_CUR.fmt(discount_cap(gross))}) "
                              "— يحتاج إلى موافقة صاحب المحل")
    ses.discount_by = (approver or "")[:100] if ses.discount else ""
    if use:
        from . import bookings as BK

        BK.deduct(prepaid, ses.station.kind, use, ses, user)
        ses.hall_customer, ses.prepaid_minutes, ses.prepaid_value = prepaid, use, pre_value
    if not b["time_amount"] and not b["items"]:
        ses.state, ses.closed_at, ses.closed_by = ("closed" if use else "void"), now, _name(user)
        if use:  # all of it from the package: no invoice (the money came in when the package was sold)
            ses.minutes, ses.billed_minutes, ses.time_amount, ses.items_amount, ses.total = b["minutes"], b["billed"], 0, 0, 0
        ses.save()
        return ses
    lines = []
    if b["time_amount"]:
        hours = (Decimal(b["billed"] - use) / 60).quantize(Decimal("0.001")) if b["billed"] > use else Decimal(1)
        lines.append(dict(item_code=play_item().code, qty=str(hours), price=str(q2(b["time_amount"] / hours)), discount="0",
                          amount=b["time_amount"]))
    for i in b["items"]:
        lines.append(dict(item_code=i["code"], qty=str(i["qty"]), price=str(i["price"]), discount="0", amount=i["total"]))
    left = ses.discount  # the discount comes off the lines in order (time first), never more than a line is worth
    for ln in lines:
        amt = ln.pop("amount")
        if left > 0:
            d = min(left, amt)
            ln["discount"], left = str(d), left - d
    paid_card, paid_credit = q2(Decimal(str(paid_card or 0))), q2(Decimal(str(paid_credit or 0)))
    if paid_card + paid_credit > b["total"]:
        raise ValidationError("المدفوع بطاقة/آجل أكبر من الحساب")
    d, sh = device(), open_shift()
    seq = (sh.orders.count() + 1) if sh else None
    order, _ = ingest_order(d, dict(uid=f"{d.branch.code}-{d.code}-P{ses.pk}", created_at=now.isoformat(), shift_uid=sh.uid if sh else None,
                                    shift_seq=seq, lines=lines, paid_cash=str(b["total"] - paid_card - paid_credit), paid_card=str(paid_card),
                                    paid_credit=str(paid_credit), customer_id=customer_id, cashier=_name(user),
                                    customer_new=customer_new if paid_credit and not customer_id else None))
    ses.state, ses.closed_at, ses.closed_by, ses.order = "closed", now, _name(user), order
    ses.minutes, ses.billed_minutes, ses.time_amount, ses.items_amount, ses.total = b["minutes"], b["billed"], b["time_amount"], b["items_amount"], b["total"]
    ses.save()
    return ses


def can_cancel(ses):
    """The hall worker alone may cancel a session opened by mistake: in its first 5 minutes and with nothing ordered."""
    return (timezone.now() - ses.opened_at).total_seconds() / 60 <= 5 and not ses.items.exists()


@transaction.atomic
def cancel_session(ses, user, owner=False, approver=""):
    """An open session opened by mistake: the cashier can cancel it in the first 5 minutes if nothing was ordered; the owner always
    (himself, or by typing his name and password on the hall screen → approver)."""
    if ses.state != "open":
        raise ValidationError("هذه الجلسة أُغلقت — إلغاء الفاتورة من «المبيعات» بحساب صاحب المحل")
    if not owner and not can_cancel(ses):
        raise ValidationError("الإلغاء بعد 5 دقائق أو بعد إضافة طلبات يحتاج موافقة صاحب المحل")
    if approver:
        ses.note = f"ألغاها {_name(user)} بموافقة {approver}"[:200]
        from core import audit

        audit.log("gaming.cancel", ses, summary=f"إلغاء {ses} — وافق {approver}، العامل {_name(user)}")
    now = timezone.now()
    ses.segments.filter(end__isnull=True).update(end=now)
    ses.minutes = int(sum(seg_minutes(g) for g in ses.segments.all()))
    ses.state, ses.closed_at, ses.closed_by = "void", now, _name(user)
    ses.save()


def stations_state(now=None):
    now = now or timezone.now()
    live = {}
    for ses in PlaySession.objects.filter(state="open").select_related("hall_customer").prefetch_related("segments__station", "items__item"):
        seg = ses.segments.filter(end__isnull=True).last()
        b = bill(ses, now)
        info = dict(id=ses.pk, no=ses.daily_no or ses.pk, customer=ses.customer, phone=ses.hall_customer.phone if ses.hall_customer_id else "", opened_at=ses.opened_at.isoformat(), discount=str(ses.discount), mode=seg.mode if seg else "",
                    rate=str(seg.rate) if seg else "0", minutes=b["minutes"], billed=b["billed"], time_amount=str(b["time_amount"]),
                    items_amount=str(b["items_amount"]), total=str(b["total"]), planned=ses.planned_minutes,
                    items=[dict(id=i["id"], code=i["code"], name=i["name"], qty=str(i["qty"]), price=str(i["price"]), total=str(i["total"]))
                           for i in b["items"]], segments=[dict(station=g["station"], mode=g["mode"], rate=str(g["rate"]), minutes=g["minutes"], multi=g["multi"])
                                                           for g in b["segments"]])
        live[seg.station_id if seg else f"t{ses.pk}"] = info
    from . import bookings as BK

    nxt = {}
    for r in BK.upcoming(now):
        if r.station_id and r.station_id not in nxt:
            nxt[r.station_id] = dict(id=r.pk, at=BK.t12(r.start), customer=r.customer, start=r.start.isoformat(), minutes=r.minutes)
    out = []
    for st in Station.objects.filter(active=True):
        out.append(dict(id=st.pk, name=st.name, kind=st.kind, kind_name=st.get_kind_display(), rate=str(st.rate),
                        rate_multi=str(st.rate_multi) if st.rate_multi else None, session=live.pop(st.pk, None), booking=nxt.get(st.pk)))
    takeaway = [v for k, v in live.items() if isinstance(k, str)]
    return out, takeaway



# ---------------------------------------------------------------- the owner's numbers: shift report and «تقرير الصالة»
def shift_report(sh):
    """What the hall worker hands over: cash / card / credit, the discounts he gave (and who approved), the cancelled sessions,
    the devices still running, expected and counted cash."""
    from django.db.models import Sum

    orders = sh.orders.filter(cancelled=False)
    agg = orders.aggregate(cash=Sum("paid_cash"), card=Sum("paid_card"), credit=Sum("paid_credit"), total=Sum("total"))
    end = sh.closed_at or timezone.now()
    sessions = PlaySession.objects.filter(order__in=orders).select_related("station")
    disc = [x for x in sessions if x.discount]
    voids = PlaySession.objects.filter(state="void", closed_at__gte=sh.opened_at, closed_at__lte=end).select_related("station")
    running = PlaySession.objects.filter(state="open").select_related("station")
    Z = Decimal(0)
    from .models import MinuteMove

    pk = list(MinuteMove.objects.filter(order__in=orders, minutes__gt=0).select_related("package", "customer"))
    pk_orders = [m.order_id for m in pk]
    play = orders.exclude(pk__in=pk_orders).aggregate(t=Sum("total"))["t"] or Z
    return dict(sh=sh, n=orders.count(), play_total=play, packages=pk, packages_total=sum((m.amount for m in pk), Z),
                prepaid_minutes=sum(x.prepaid_minutes for x in sessions), prepaid_value=sum((x.prepaid_value for x in sessions), Z), cash=agg["cash"] or Z, card=agg["card"] or Z, credit=agg["credit"] or Z, total=agg["total"] or Z,
                discount=sum((x.discount for x in disc), Z), discounts=disc, voids=list(voids), running=list(running),
                expected=sh.expected_cash, counted=sh.counted_cash,
                diff=(sh.counted_cash - sh.expected_cash) if sh.closed_at and sh.expected_cash is not None else None)


def hall_report(d1, d2):
    """Per device: sessions, hours, time revenue, buffet, utilisation (minutes played ÷ the hall's open time those days);
    per worker: bills, total, discounts; the cancelled sessions and the discounts one by one."""
    import datetime as dt

    Z = Decimal(0)
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(dt.datetime.combine(d1, dt.time.min), tz)
    stop = timezone.make_aware(dt.datetime.combine(d2 + dt.timedelta(days=1), dt.time.min), tz)
    closed = list(PlaySession.objects.filter(state="closed", closed_at__gte=start, closed_at__lt=stop).select_related("station"))
    voids = list(PlaySession.objects.filter(state="void", closed_at__gte=start, closed_at__lt=stop).select_related("station"))
    segs = list(PlaySegment.objects.filter(session__state="closed", session__closed_at__gte=start, session__closed_at__lt=stop)
                .select_related("station"))
    open_min = 0.0  # the hall's open time: from the first start to the last close of every day it worked
    days = {}
    for x in closed + voids:
        k = timezone.localtime(x.opened_at).date()
        a, b = days.get(k, (x.opened_at, x.closed_at or x.opened_at))
        days[k] = (min(a, x.opened_at), max(b, x.closed_at or x.opened_at))
    for a, b in days.values():
        open_min += max(0.0, (b - a).total_seconds() / 60)
    dev = {}
    for st in Station.objects.all():
        dev[st.pk] = dict(st=st, n=0, minutes=0.0, billed=0, time=Z, items=Z, discount=Z)
    for g in segs:
        r = dev.setdefault(g.station_id, dict(st=g.station, n=0, minutes=0.0, billed=0, time=Z, items=Z, discount=Z))
        r["minutes"] += seg_minutes(g)
    for x in closed:
        if not x.station_id:
            continue
        r = dev[x.station_id]
        r["n"] += 1
        r["billed"] += x.billed_minutes
        r["time"] += x.time_amount
        r["items"] += x.items_amount
        r["discount"] += x.discount
    rows = []
    for r in dev.values():
        if not (r["n"] or r["st"].active):
            continue
        r["hours"] = round(r["minutes"] / 60, 2)
        r["billed_hours"] = round(r["billed"] / 60, 2)
        r["util"] = round(100 * r["minutes"] / open_min) if open_min else 0
        rows.append(r)
    rows.sort(key=lambda r: -r["time"])
    take = [x for x in closed if not x.station_id]
    workers = {}
    for x in closed:
        w = workers.setdefault(x.closed_by or "—", dict(name=x.closed_by or "—", n=0, total=Z, discount=Z, disc_n=0, voids=0))
        w["n"] += 1
        w["total"] += x.total
        w["discount"] += x.discount
        w["disc_n"] += 1 if x.discount else 0
    for x in voids:
        w = workers.setdefault(x.closed_by or "—", dict(name=x.closed_by or "—", n=0, total=Z, discount=Z, disc_n=0, voids=0))
        w["voids"] += 1
    from .models import MinuteMove

    sold = MinuteMove.objects.filter(minutes__gt=0, at__gte=start, at__lt=stop)
    used = MinuteMove.objects.filter(minutes__lt=0, at__gte=start, at__lt=stop)
    packages = dict(n=sold.count(), total=sum((m.amount for m in sold), Z), hours=round(sum(m.minutes for m in sold) / 60, 1),
                    used_hours=round(-sum(m.minutes for m in used) / 60, 1))
    return dict(devices=rows, workers=sorted(workers.values(), key=lambda w: -w["total"]), voids=voids, packages=packages,
                discounts=[x for x in closed if x.discount], open_hours=round(open_min / 60, 1),
                total=sum((x.total for x in closed), Z), time=sum((x.time_amount for x in closed), Z),
                items=sum((x.items_amount for x in closed), Z), discount=sum((x.discount for x in closed), Z),
                n=len(closed), hours=round(sum(r["minutes"] for r in rows) / 60, 1),
                takeaway=dict(n=len(take), total=sum((x.total for x in take), Z)))
