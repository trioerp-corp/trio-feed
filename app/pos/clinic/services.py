"""Clinic workflow. A visit is paid like any sale (ingest_order on the reception device «CL»): cash / card / on the patient's
account, consumables of each service leave the store, the doctor's percentage is booked as an expense owed to him and paid
later from the cash box (pos.money kind «doctor»). Cancel / edit go through reversing entries, never deletes."""
import datetime as dt
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max, Sum
from django.utils import timezone

from inventory.models import Item, ItemCategory, Recipe, RecipeLine
from ledger import services as L
from ledger.models import Account, JournalLine, Partner

from .models import Appointment, ClinicSettings, Doctor, Patient, Service, Visit, VisitLine, VisitPayment  # noqa: F401

Z = Decimal("0")
SERVICES_CAT = "خدمات طبية"
CLINIC_CATEGORIES = [SERVICES_CAT, "مستهلكات طبية", "أدوية ومستحضرات", "أدوات ومستلزمات", "أخرى"]
SHARE_EXP, SHARE_PAY = "3201010110", "2301050103"


def _name(user):
    if not user or user == "system":
        return ""
    return user.first_name or user.username


# ---------------------------------------------------------------- accounts / setup
def share_account():
    acc = Account.objects.filter(code=SHARE_EXP).first()
    if acc:
        return acc
    return Account.objects.create(code=SHARE_EXP, name="نسب وأتعاب الأطباء", parent=Account.objects.get(code="32010101"), level=6,
                                  is_postable=True, account_type="expense", normal_balance="debit", report="profit_loss",
                                  cash_flow_category="operating", currencies="EGP", dim1_policy="optional")


def payable_account():
    acc = Account.objects.filter(code=SHARE_PAY).first()
    if acc:
        return acc
    return Account.objects.create(code=SHARE_PAY, name="مستحقات الأطباء", parent=Account.objects.get(code="23010501"), level=6,
                                  is_postable=True, account_type="liability", normal_balance="credit", report="balance_sheet",
                                  cash_flow_category="operating", currencies="EGP", is_partner_control=True)


def services_category():
    cat = ItemCategory.objects.filter(name=SERVICES_CAT).first()
    if cat:
        return cat
    stock = Account.objects.filter(code="1202010101").first()
    cogs = Account.objects.filter(code="3101010101").first()
    return ItemCategory.objects.create(name=SERVICES_CAT, stock_account=stock, farm_account=cogs, admin_account=cogs, needs_equipment=False)


def device():
    from pos.models import Branch, Device

    d = Device.objects.filter(code="CL").select_related("branch").first()
    if d:
        return d
    br = Branch.objects.filter(active=True).first()
    if not br:
        raise ValidationError("لا يوجد فرع مسجّل")
    return Device.objects.create(branch=br, code="CL", name="الاستقبال")


# ---------------------------------------------------------------- the reception drawer (a shift on device «CL»)
def open_shift():
    from pos.models import Shift

    return Shift.objects.filter(device=device(), closed_at__isnull=True).order_by("-opened_at").first()


@transaction.atomic
def start_shift(user, opening_float=0):
    """The reception's drawer: opened by her first collection of the day (or by hand with the change in it), in her name."""
    from pos.services import ingest_shift

    sh = open_shift()
    if sh:
        return sh
    d, now = device(), timezone.now()
    return ingest_shift(d, dict(uid=f"{d.branch.code}-{d.code}-SH{now:%Y%m%d%H%M%S%f}", cashier=_name(user) or "الاستقبال", verified=True,
                                opened_at=now.isoformat(), opening_float=str(L.q2(Decimal(str(opening_float or 0))))))


@transaction.atomic
def end_shift(user, counted):
    """«قفّل درجي»: she counts the drawer without seeing what is expected; the difference is booked and the owner sees it at the
    day close."""
    from pos.services import ingest_shift

    sh = open_shift()
    if not sh:
        raise ValidationError("لا يوجد درج مفتوح")
    try:
        counted = L.q2(Decimal(str(counted).replace("٫", ".").replace(",", ".").strip()))
    except Exception:  # noqa: BLE001 — empty or not a number
        counted = Decimal("-1")
    if counted < 0:
        raise ValidationError("اكتب المبلغ الموجود في الدرج بعد عدّه")
    return ingest_shift(sh.device, dict(uid=sh.uid, closed_at=timezone.now().isoformat(), counted_cash=str(counted)))


def doctor_partner(doc: Doctor):
    if doc.partner_id:
        return doc.partner
    doc.partner = Partner.objects.create(name=f"د. {doc.name}", kind="contractor", phone=doc.phone)
    doc.save(update_fields=["partner"])
    return doc.partner


def patient_customer(p: Patient):
    if p.partner_id:
        return p.partner
    p.partner = Partner.objects.create(name=f"{p.name} (ملف {p.number})", kind="customer", phone=p.phone)
    p.save(update_fields=["partner"])
    return p.partner


# ---------------------------------------------------------------- setup: doctors / services
@transaction.atomic
def save_service(name, price, *, doctor=None, share_pct=None, minutes=15, consumables=(), service=None):
    """consumables: [(item, qty), …] — what one visit of this kind uses from the store."""
    name = (name or "").strip()
    if not name:
        raise ValidationError("اكتب اسم الخدمة")
    price = Decimal(str(price or 0))
    if price < 0:
        raise ValidationError("السعر بالسالب")
    if service:
        it = service.item
        it.name, it.sale_price, it.active = name, price, True
        it.save(update_fields=["name", "sale_price", "active"])
    else:
        n = Item.objects.filter(code__startswith="S").count() + 1
        while Item.objects.filter(code=f"S{n:03d}").exists():
            n += 1
        it = Item.objects.create(code=f"S{n:03d}", name=name, category=services_category(), unit="خدمة", sale_price=price,
                                 item_type="service", vat_rate=0)
        service = Service(item=it)
    service.doctor, service.minutes = doctor, int(minutes or 15)
    service.share_pct = Decimal(str(share_pct)) if share_pct not in (None, "") else None
    service.save()
    rows = [(i, Decimal(str(q))) for i, q in consumables if i and Decimal(str(q or 0)) > 0]
    r = getattr(it, "recipe", None) if hasattr(it, "recipe") else None
    if rows:
        r = r or Recipe.objects.create(item=it, target_margin=0)
        r.lines.all().delete()
        for i, q in rows:
            if i.item_type != "stock":
                raise ValidationError(f"«{i.name}» ليس صنفًا مخزنيًا")
            RecipeLine.objects.create(recipe=r, ingredient=i, qty=q)
    elif r:
        r.delete()
    return service


# ---------------------------------------------------------------- patients / bookings
def next_number():
    return (Patient.objects.aggregate(m=Max("number"))["m"] or 1000) + 1


def add_patient(name, phone="", **extra):
    name = (name or "").strip()
    if not name:
        raise ValidationError("اكتب اسم المريض")
    return Patient.objects.create(number=next_number(), name=name[:120], phone=(phone or "").strip()[:40], **extra)


def _mins(t):
    return t.hour * 60 + t.minute


def _as_time(t):
    if not t or isinstance(t, dt.time):
        return t or None
    try:
        return dt.time.fromisoformat(str(t)[:5])
    except ValueError:
        raise ValidationError("الساعة غير صحيحة")


def overlaps(doctor, date, time, minutes=15, exclude=None):
    """The doctor's bookings that day whose slot (its time + its service's minutes) meets [time, time + minutes)."""
    time = _as_time(time)
    if not doctor or not time:
        return []
    a0, a1 = _mins(time), _mins(time) + int(minutes or 15)
    out = []
    for a in Appointment.objects.filter(doctor=doctor, date=date, time__isnull=False).exclude(state__in=["cancelled", "noshow"]).select_related(
            "patient", "service"):
        if exclude and a.pk == exclude:
            continue
        b0 = _mins(a.time)
        b1 = b0 + (a.service.minutes if a.service_id and a.service.minutes else 15)
        if a0 < b1 and b0 < a1:
            out.append(a)
    return out


def first_free(doctor, date, time, minutes=15):
    """The first time from `time` on (5-minute steps) the doctor has no booking — «HH:MM», or "" when the day is full."""
    t = _mins(_as_time(time))
    while t + int(minutes or 15) <= 24 * 60:
        cand = dt.time(t // 60, t % 60)
        if not overlaps(doctor, date, cand, minutes):
            return cand.strftime("%H:%M")
        t += 5
    return ""


def _queue(date, doctor):
    return (Appointment.objects.filter(date=date, doctor=doctor).aggregate(m=Max("queue_no"))["m"] or 0) + 1


@transaction.atomic
def book(patient, date, *, doctor=None, service=None, time=None, note="", user=None, pay=None, paid_card=0, paid_credit=0, discount=0,
         allow_overlap=False, followup=None, approver=None):
    """A booking. With `pay` («cash» / «card» / «credit» / «split») the visit is opened now and its service paid: the booking receipt.
    Two bookings of the same doctor at the same time are refused unless the reception confirms it (allow_overlap)."""
    if Appointment.objects.filter(patient=patient, date=date, state__in=["booked", "waiting", "inside"]).exists():
        raise ValidationError(f"{patient.name} محجوز في هذا اليوم بالفعل")
    doctor = doctor or (service.doctor if service else None)
    time = _as_time(time)
    if time and doctor and not allow_overlap:
        mins = service.minutes if service else 15
        clash = overlaps(doctor, date, time, mins)
        if clash:
            a = clash[0]
            free = first_free(doctor, date, time, mins)
            raise ValidationError(f"{doctor} لديه حجز {a.patient.name} الساعة {a.time:%H:%M} — "
                                  + (f"أول موعد متاح {free}" if free else "لا يوجد موعد متاح بعدها في هذا اليوم")
                                  + " (أو أكّد الحجز في الوقت نفسه)")
    if doctor and not allow_overlap:  # outside his days / hours, or his day is full: the reception confirms
        from . import followup as FU

        w = FU.schedule_warnings(doctor, date, time)
        if w:
            raise ValidationError(" · ".join(w) + " (أو أكّد الحجز)")
    fu = None
    if followup is not False:  # a free / reduced «إعادة» of a paid «كشف» within N days: offered automatically
        from . import followup as FU

        fu = FU.followup_for(patient, doctor, service or _default_service(doctor), date)
    appt = Appointment.objects.create(patient=patient, date=date, doctor=doctor, service=service, time=time, note=(note or "")[:200],
                                      queue_no=_queue(date, doctor), created_by=_name(user), followup_of=fu["base"] if fu else None)
    if pay:
        v = open_visit(patient, doctor=doctor, service=service, user=user, followup=fu)
        if not v.lines.exists():
            raise ValidationError("اختر الخدمة التي ستُدفع")
        total = v.unbilled - L.q2(Decimal(str(discount or 0)))
        pay = resolve_pay(patient, pay)
        if pay in ("insurance", "insurance_card"):  # the company's share on its account, the patient's part now
            checkout(v, user, discount=discount, kind="booking", insured=True, patient_by_card=pay == "insurance_card", approver=approver)
        else:
            card, credit = (paid_card, paid_credit) if pay == "split" else (total if pay == "card" else 0, total if pay == "credit" else 0)
            checkout(v, user, paid_card=card, paid_credit=credit, discount=discount, kind="booking", approver=approver)
        appt.visit = v
        appt.state = "booked"
        appt.save(update_fields=["visit", "state"])
    return appt


@transaction.atomic
def arrive(appt: Appointment, user=None):
    """The patient reached the clinic: he joins the waiting queue and his visit opens (the service of the booking prefilled)."""
    if appt.state not in ("booked", "noshow"):
        raise ValidationError("هذا الحجز ليس في انتظار الوصول")
    if appt.date != timezone.localdate():
        appt.date, appt.queue_no = timezone.localdate(), _queue(timezone.localdate(), appt.doctor)
    fu = None
    if not appt.visit_id and appt.followup_of_id:
        from . import followup as FU

        fu = FU.followup_for(appt.patient, appt.doctor, appt.service or _default_service(appt.doctor), timezone.localdate(),
                            exclude_appt=appt.pk)
        if fu and fu["base"].pk != appt.followup_of_id:
            fu = None
    appt.visit = appt.visit or open_visit(appt.patient, doctor=appt.doctor, service=appt.service, user=user, followup=fu)
    appt.state = "waiting"
    appt.save()
    return appt


def walk_in(patient, *, doctor=None, service=None, user=None, **pay):
    """No booking: straight into today's queue (paid at the reception when `pay` is given)."""
    pay.setdefault("allow_overlap", True)  # the patient is here: the schedule warnings are shown, never a refusal
    appt = book(patient, timezone.localdate(), doctor=doctor, service=service, user=user, **pay)
    return arrive(appt, user)


def set_state(appt: Appointment, state, user=None, reason=""):
    if state not in dict(Appointment.STATES):
        raise ValidationError("حالة غير صحيحة")
    if appt.state == "done":
        raise ValidationError("تمت محاسبة الزيارة — الإلغاء من صفحة الزيارة")
    v = appt.visit if appt.visit_id else None
    if state == "cancelled" and v and v.state != "cancelled":
        if v.live_payments:  # a paid booking: the money goes back with a reversing entry
            cancel_visit(v, reason or "إلغاء الحجز", user)
        else:
            v.state = "cancelled"
            v.save(update_fields=["state"])
    appt.state = state
    appt.save(update_fields=["state"])
    return appt


# ---------------------------------------------------------------- visits
@transaction.atomic
def open_visit(patient, *, doctor=None, service=None, user=None, followup=None):
    if service is None:  # no service picked: the doctor's first service, else the clinic's first («كشف»)
        service = _default_service(doctor)
    v = Visit.objects.create(patient=patient, doctor=doctor or (service.doctor if service else None), opened_at=timezone.now(), by=_name(user),
                             followup_of=followup["base"] if followup else None, followup_note=followup["note"] if followup else "")
    if service:
        VisitLine.objects.create(visit=v, item=service.item, price=followup["price"] if followup else (service.item.sale_price or Z))
    return v


def _default_service(doctor):
    live = Service.objects.filter(item__active=True).order_by("id")
    return (live.filter(doctor=doctor).first() if doctor else None) or live.filter(doctor__isnull=True).first()


def _need_live(v):
    if v.state == "cancelled":
        raise ValidationError("هذه الزيارة ملغاة")


@transaction.atomic
def add_line(v: Visit, item, qty=1, price=None):
    """Lines can be added after a payment too (the doctor adds an X-ray…): they wait for an extra bill."""
    _need_live(v)
    qty = Decimal(str(qty or 1))
    if qty <= 0:
        raise ValidationError("يجب أن تكون الكمية أكبر من صفر")
    price = Decimal(str(price)) if price not in (None, "") else item.sale_price
    if price is None:
        raise ValidationError(f"{item.name} ملوش سعر")
    ln = VisitLine.objects.create(visit=v, item=item, qty=qty, price=price)
    if v.state == "paid":
        v.state = "open"
        v.save(update_fields=["state"])
    return ln


def share_of(v: Visit, lines=None, discount=Z):
    """The doctor's money from these lines: his % (or the service's own %) of each service line after the discount."""
    if not v.doctor_id:
        return Z
    disc_left = discount
    out = Z
    for ln in lines if lines is not None else v.lines.select_related("item"):
        amt = ln.amount
        if disc_left:  # the bill discount comes off the lines in order, like on the invoice
            d = min(disc_left, amt)
            amt, disc_left = amt - d, disc_left - d
        svc = getattr(ln.item, "clinic_service", None) if ln.item.item_type == "service" else None
        if svc is None:
            continue
        pct = svc.share_pct if svc.share_pct is not None else v.doctor.share_pct
        out += amt * pct / 100
    return L.q2(out)


def _totals(v):
    live = [p for p in v.payments.all() if not p.cancelled]
    v.total = sum((p.total for p in live), Z)
    v.doctor_share = sum((p.share for p in live), Z)
    v.discount = sum((p.discount for p in live), Z)
    v.order = live[-1].order if live else None
    v.share_entry = live[-1].share_entry if live else None


@transaction.atomic
def insurer_customer(ins):
    if ins.partner_id:
        return ins.partner
    ins.partner = Partner.objects.create(name=f"تأمين: {ins.name}"[:150], kind="customer", phone=ins.phone, tax_id=ins.tax_id)
    ins.save(update_fields=["partner"])
    return ins.partner


def insurance_of(p: Patient, day=None):
    """The patient's insurance if it can be used today (company active, card not expired) — else None."""
    ins = p.insurance
    day = day or timezone.localdate()
    if not ins or not ins.active or (p.insurance_expiry and p.insurance_expiry < day):
        return None
    return ins


def insurance_share(ins, total):
    amt = L.q2(Decimal(str(total)) * ins.coverage_pct / 100)
    if ins.max_per_visit is not None:
        amt = min(amt, ins.max_per_visit)
    return max(min(amt, Decimal(str(total))), Z)


def _trusted(user):
    """Internal calls (no user / «system») and the owner may discount freely; the reception is capped."""
    from pos.voids import is_owner

    return user in (None, "system") or getattr(user, "is_superuser", False) or is_owner(user)


def resolve_pay(patient, pay):
    """The reception's payment choice → what the server does: an insured patient (valid card) pays only his share by default —
    «cash» / «card» become «insurance» / «insurance_card»; «cash_full» / «card_full» = without the insurance."""
    pay = pay or "cash"
    if pay in ("cash_full", "card_full"):
        return pay[:-5]
    if pay in ("cash", "card") and insurance_of(patient):
        return "insurance" if pay == "cash" else "insurance_card"
    return pay


def checkout(v: Visit, user=None, *, paid_card=0, paid_credit=0, discount=0, kind=None, insured=False, patient_by_card=False,
             approver=None):
    """Bill what is not paid yet: one sales invoice (drawer / card / patient's account), the consumables leave the store,
    the doctor's share is booked as owed to him. insured: the patient's insurance company takes its share on its account
    (the patient pays the rest now, cash or card)."""
    from pos.services import ingest_order

    v = Visit.objects.select_for_update().get(pk=v.pk)
    _need_live(v)
    lines = list(v.lines.filter(payment__isnull=True).select_related("item"))
    if not lines:
        raise ValidationError("لا يوجد ما لم تتم محاسبته — أضف الكشف / الخدمة أولًا")
    disc = L.q2(Decimal(str(discount or 0)))
    total = sum((ln.amount for ln in lines), Z) - disc
    if total < 0:
        raise ValidationError("الخصم أكبر من الحساب")
    # a free «إعادة» (total 0) is still a normal invoice at 0.00: its consumables leave the store and cost goes to COGS
    gross = sum((ln.amount for ln in lines), Z)
    if disc and not _trusted(user) and not approver:
        cap = L.q2(gross * ClinicSettings.get().max_discount_pct / 100)
        if disc > cap:
            raise ValidationError(f"الخصم أكبر من المسموح للاستقبال ({cap:,.2f} ج) — يكتب صاحب العيادة اسمه وكلمة سره للموافقة")
    paid_card, paid_credit = L.q2(Decimal(str(paid_card or 0))), L.q2(Decimal(str(paid_credit or 0)))
    ins, ins_amt = None, Z
    if insured:
        ins = insurance_of(v.patient)
        if not ins:
            raise ValidationError("ليس للمريض تأمين ساري (الشركة موقوفة أو البطاقة منتهية)")
        if paid_credit:
            raise ValidationError("مع التأمين: تُدفع حصة المريض نقدًا أو بالبطاقة — والآجل على شركة التأمين فقط")
        ins_amt = insurance_share(ins, total)
        if patient_by_card:
            paid_card = total - ins_amt
        if paid_card > total - ins_amt:
            raise ValidationError("المدفوع بالبطاقة أكبر من حصة المريض")
        paid_credit = ins_amt
    if paid_card + paid_credit > total:
        raise ValidationError("المدفوع بطاقة/آجل أكبر من الحساب")
    customer = (insurer_customer(ins) if ins else patient_customer(v.patient)) if paid_credit else None
    data_lines, disc_left = [], disc
    for ln in lines:
        d = ln.discount
        if disc_left:
            extra = min(disc_left, ln.amount)
            d, disc_left = d + extra, disc_left - extra
        data_lines.append(dict(item_code=ln.item.code, qty=str(ln.qty), price=str(ln.price), discount=str(d)))
    d = device()
    now = timezone.now()
    seq = v.payments.count() + 1
    sh = open_shift() or start_shift(user)  # the money goes into the reception's drawer (her shift), closed by «قفّل درجي»
    order, _ = ingest_order(d, dict(uid=f"{d.branch.code}-{d.code}-V{v.pk}-{seq}", created_at=now.isoformat(), lines=data_lines,
                                    shift_uid=sh.uid, shift_seq=sh.orders.count() + 1,
                                    paid_cash=str(total - paid_card - paid_credit), paid_card=str(paid_card), paid_credit=str(paid_credit),
                                    customer_id=customer.pk if customer else None, cashier=_name(user), zero_ok=total == 0))
    share = share_of(v, lines, disc)
    entry = None
    if share:
        cc = d.branch.cost_center
        entry = L.create_entry("JV", now.date(), f"نسبة {v.doctor} — زيارة {v.patient.name}", [
            dict(account=share_account(), debit=share, dim1=cc, description=f"نسبة {v.doctor} زيارة {v.pk}"),
            dict(account=payable_account(), credit=share, partner=doctor_partner(v.doctor), description=f"نسبة زيارة {v.pk}")],
            source=f"clinic.visit:{v.pk}", user=user if user and user != "system" else None)
    pay = VisitPayment.objects.create(visit=v, order=order, share_entry=entry, total=order.total, discount=disc, share=share, by=_name(user),
                                      kind=kind or ("extra" if v.payments.filter(cancelled=False).exists() else "visit"),
                                      insurance=ins, insurance_amount=ins_amt,
                                      discount_by=(approver or _name(user)) if disc else "")
    if disc and approver and approver != _name(user):
        from core import audit

        audit.log("clinic.discount", v, summary=f"خصم {disc} على زيارة {v.patient.name} — وافق {approver}، الاستقبال {_name(user)}")
    VisitLine.objects.filter(pk__in=[ln.pk for ln in lines]).update(payment=pay)
    v.state, v.paid_by = "paid", _name(user)
    _totals(v)
    v.save()
    if kind != "booking":
        Appointment.objects.filter(visit=v).update(state="done")
    return v


def discounts(d1, d2):
    """The bills with a discount in these days (who gave it, who approved): the owner board and the phone."""
    return list(VisitPayment.objects.filter(cancelled=False, discount__gt=0, created_at__date__gte=d1, created_at__date__lte=d2)
                .select_related("visit__patient", "visit__doctor").order_by("-id")[:200])


def _undo_money(v: Visit, reason, user):
    from pos import voids as V

    for p in v.payments.filter(cancelled=False).select_related("order", "share_entry"):
        if not p.order.cancelled:
            V.void_order(p.order, reason, user)
        if p.share_entry_id and p.share_entry.state == "posted":
            L.reverse(p.share_entry, date=timezone.localdate(), reason=reason, user=None if user == "system" else user)
        p.cancelled = True
        p.save(update_fields=["cancelled"])
    v.lines.update(payment=None)


@transaction.atomic
def cancel_visit(v: Visit, reason, user=None):
    from pos import voids as V

    V._need_editor(user)
    v = Visit.objects.select_for_update().get(pk=v.pk)
    if v.state == "cancelled":
        raise ValidationError("الزيارة ملغية بالفعل")
    if not (reason or "").strip():
        raise ValidationError("اكتب السبب")
    _undo_money(v, f"إلغاء زيارة {v.patient.name}: {reason}", user)
    v.state, v.note = "cancelled", reason[:300]
    _totals(v)
    v.save()
    Appointment.objects.filter(visit=v).update(state="cancelled")
    return v


@transaction.atomic
def reopen_visit(v: Visit, reason, user=None):
    """Edit the bill of a visit: every payment and the doctor's share are reversed, the visit opens again to change and pay again."""
    from pos import voids as V

    V._need_editor(user)
    v = Visit.objects.select_for_update().get(pk=v.pk)
    if not v.payments.filter(cancelled=False).exists():
        raise ValidationError("لم تتم محاسبة الزيارة")
    if not (reason or "").strip():
        raise ValidationError("اكتب السبب")
    _undo_money(v, f"تعديل زيارة {v.patient.name}: {reason}", user)
    v.state, v.note, v.edits = "open", reason[:300], v.edits + 1
    _totals(v)
    v.save()
    Appointment.objects.filter(visit=v, state="done").update(state="inside")
    return v


# ---------------------------------------------------------------- doctors' money
def doctor_balance(doc: Doctor):
    if not doc.partner_id:
        doc.refresh_from_db(fields=["partner"])
    if not doc.partner_id:
        return Z
    acc = Account.objects.filter(code=SHARE_PAY).first()
    if not acc:
        return Z
    t = JournalLine.objects.filter(account=acc, partner=doc.partner, entry__state__in=["posted", "reversed"]).aggregate(c=Sum("credit"), d=Sum("debit"))
    return (t["c"] or Z) - (t["d"] or Z)


def pay_doctor(doc: Doctor, amount, *, cash_account, user=None, note="", date=None):
    from pos import money

    if not doc.partner_id:
        doc.refresh_from_db(fields=["partner"])
    if not doc.partner_id:
        raise ValidationError(f"{doc} ملوش مستحقات")
    stamp = timezone.now().strftime("%Y%m%d%H%M%S%f")
    return money.post("doctor", amount, date or timezone.localdate(), cash_account=cash_account, partner=doc.partner, note=note,
                      uid=f"CL-DR{doc.pk}-{stamp}", by=_name(user), user=user)


def shares_report(d1, d2):
    rows = []
    for doc in Doctor.objects.all():
        vs = Visit.objects.filter(doctor=doc, state="paid", opened_at__date__gte=d1, opened_at__date__lte=d2)
        agg = vs.aggregate(n=Sum("total"), s=Sum("doctor_share"))
        rows.append(dict(doctor=doc, visits=vs.count(), revenue=agg["n"] or Z, share=agg["s"] or Z, balance=doctor_balance(doc)))
    return [r for r in rows if r["visits"] or r["balance"] or r["doctor"].active]


def today_summary(day=None):
    day = day or timezone.localdate()
    paid = Visit.objects.filter(state="paid", opened_at__date=day)
    agg = paid.aggregate(t=Sum("total"), s=Sum("doctor_share"))
    appts = Appointment.objects.filter(date=day)
    return dict(visits=paid.count(), revenue=agg["t"] or Z, shares=agg["s"] or Z,
                waiting=appts.filter(state="waiting").count(), inside=appts.filter(state="inside").count(),
                booked=appts.filter(state="booked").count(), done=appts.filter(state="done").count())


def parse_date(s, default=None):
    try:
        return dt.date.fromisoformat(s)
    except (TypeError, ValueError):
        return default or timezone.localdate()


# ---------------------------------------------------------------- statements (1.21): every bill — paid, partly paid or still owed
def _balance(partner):
    from django.db.models import Sum

    from ledger.models import JournalLine

    if not partner:
        return Z
    r = JournalLine.objects.filter(account__code="1203010101", partner=partner, entry__state__in=["posted", "reversed"]).aggregate(
        d=Sum("debit"), c=Sum("credit"))
    return (r["d"] or Z) - (r["c"] or Z)


def _fifo(rows, owed_now):
    """rows: bills oldest first with r["credit"] (what went on account). What is still owed sits on the newest bills;
    the older ones are counted as collected first."""
    left = max(owed_now, Z)
    for r in reversed(rows):
        r["remaining"] = min(r["credit"], left)
        left -= r["remaining"]
    for r in rows:
        r["collected"] = r["credit"] - r["remaining"]
        if r["remaining"] <= 0:
            r["status"] = "مدفوعة"
        elif r["remaining"] < r["credit"] or r["paid_now"] > 0:
            r["status"] = "جزئي"
        else:
            r["status"] = "آجل"
    return rows


def patient_statement(p: Patient):
    """→ dict(rows, total, paid, insurance, owed): one row per bill of the patient."""
    p.refresh_from_db(fields=["partner"])
    pays = (VisitPayment.objects.filter(visit__patient=p, cancelled=False).select_related("order", "order__customer", "visit__doctor", "insurance")
            .order_by("created_at", "id"))
    rows = []
    for pay in pays:
        o = pay.order
        on_patient = o.paid_credit if (p.partner_id and o.customer_id == p.partner_id) else Z
        rows.append(dict(pay=pay, date=pay.created_at, visit=pay.visit, total=pay.total, paid_now=o.paid_cash + o.paid_card,
                         insurance=pay.insurance_amount, credit=on_patient))
    owed = _balance(p.partner)
    _fifo(rows, owed)
    for r in rows:
        if r["credit"] == 0:
            r["status"] = "مدفوعة" + (" (والباقي على التأمين)" if r["insurance"] else "")
    return dict(rows=rows, total=sum((r["total"] for r in rows), Z), paid=sum((r["paid_now"] + r["collected"] for r in rows), Z),
                insurance=sum((r["insurance"] for r in rows), Z), owed=owed)


def insurance_statement(ins, d1=None, d2=None):
    """The company's bills (its share of each visit), what it paid, and what it still owes — FIFO like the patient's."""
    ins.refresh_from_db(fields=["partner"])
    pays = (VisitPayment.objects.filter(insurance=ins, cancelled=False).select_related("visit__patient", "visit__doctor")
            .order_by("created_at", "id"))
    rows = [dict(pay=p, date=p.created_at, patient=p.visit.patient, visit=p.visit, total=p.total, paid_now=Z, credit=p.insurance_amount)
            for p in pays]
    owed = _balance(ins.partner)
    _fifo(rows, owed)
    if d1:
        rows = [r for r in rows if r["date"].date() >= d1]
    if d2:
        rows = [r for r in rows if r["date"].date() <= d2]
    return dict(rows=rows, billed=sum((r["credit"] for r in rows), Z), owed=owed,
                collected=sum((r["collected"] for r in rows), Z))


def collect(partner, amount, box, user=None, note=""):
    """Money in from a patient / insurance company to a cash box or wallet."""
    from pos import money

    d = device()
    sh = None
    if box.account_id == d.branch.cashbox.account_id:  # cash into the reception drawer: on her shift
        sh = open_shift() or start_shift(user)
    return money.post("collect", amount, timezone.localdate(), cash_account=box.account, partner=partner, note=note, shift=sh,
                      uid=f"CL-COL-{partner.pk}-{timezone.now():%Y%m%d%H%M%S%f}", by=_name(user), user=user if user and user != "system" else None)
