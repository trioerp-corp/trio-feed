"""Clinic online booking (1.25): the clinic's public page on Trio's Worker — <Worker>/book/<public id>.

The program publishes what a patient may choose (the doctors with a schedule, the services, each day's open hours and the
times already taken for the next N days); the patient picks a doctor, a service, a day and a free time, writes his name and
mobile, and the request waits in the Worker (D1 cbook) until this loop picks it up — like the QR orders (pos/qr_orders.py).
The program checks it again (the slot may be gone, the doctor may be off), keeps it as «حجز أونلاين — بانتظار التأكيد», and the
reception confirms it (it becomes a normal booking with the note «حجز أونلاين») or refuses it; the patient's page shows the
answer. Limits against abuse: the Worker (per device / per mobile / per clinic per day, a hidden field) and here (one
request per mobile per day, at most 3 waiting per mobile, every request checked against the schedule).
"""
import datetime as dt
import hashlib
import json
import threading
import time
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core import mobile
from core.models import CompanySettings

from .models import Appointment, ClinicSettings, Doctor, OnlineBooking, Patient, Service

FAST, SLOW, IDLE = 15, 45, 120
MAX_WAITING_PER_PHONE = 3
NOTE = "حجز أونلاين"
_state = {"started": False, "last": 0.0}


def public_id(s=None):
    """sha256("acc-book:" + shop secret)[:10 bytes] hex — the same as the Worker's bookId()."""
    return hashlib.sha256(("acc-book:" + mobile.key(s)).encode()).hexdigest()[:20]


def link(s=None):
    base = mobile.api()
    return f"{base}/book/{public_id(s)}" if base else ""


def is_on(s=None, cs=None):
    s = s or CompanySettings.get()
    cs = cs or ClinicSettings.get()
    return s.business == "clinic" and cs.online_on and not s.suspended


def clean_phone(p):
    p = "".join(ch for ch in str(p or "").translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")) if ch.isdigit())
    if p.startswith("0020"):
        p = p[4:]
    elif p.startswith("20") and len(p) == 12:
        p = p[2:]
    if len(p) == 10 and p.startswith("1"):
        p = "0" + p
    return p if len(p) == 11 and p.startswith("01") else ""


def _mins(t):
    return t.hour * 60 + t.minute


def _svc_minutes(svc):
    return int(svc.minutes or 15) if svc else 15


def online_doctors():
    return [d for d in Doctor.objects.filter(active=True).prefetch_related("shifts").order_by("order", "id") if d.shifts.all()]


def online_services(doctors):
    ids = {d.pk for d in doctors}
    out = []
    for sv in Service.objects.filter(item__active=True).select_related("item", "doctor").order_by("item__name"):
        if sv.doctor_id and sv.doctor_id not in ids:
            continue
        out.append(sv)
    return out


def taken(doctor, day):
    """[(start, end)] minutes already booked (bookings + online requests waiting) and how many count toward the day's limit."""
    rows, n = [], 0
    for a in Appointment.objects.filter(doctor=doctor, date=day).exclude(state__in=["cancelled", "noshow"]).select_related("service"):
        n += 1
        if a.time:
            rows.append((_mins(a.time), _mins(a.time) + _svc_minutes(a.service)))
    for ob in OnlineBooking.objects.filter(doctor=doctor, date=day, state="pending").select_related("service"):
        n += 1
        if ob.time:
            rows.append((_mins(ob.time), _mins(ob.time) + _svc_minutes(ob.service)))
    return rows, n


def data(s=None, cs=None, today=None):
    s = s or CompanySettings.get()
    cs = cs or ClinicSettings.get()
    today = today or timezone.localdate()
    days = max(1, min(int(cs.online_days or 14), 60))
    docs = online_doctors()
    svcs = online_services(docs)
    out_docs, open_, busy = [], {}, {}
    for d in docs:
        out_docs.append(dict(i=d.pk, n=str(d)[:80], sp=d.specialty[:80]))
    for k in range(days + 1):
        day = today + dt.timedelta(days=k)
        key = day.isoformat()
        for d in docs:
            win = [[_mins(x.start), _mins(x.end)] for x in d.shifts.all() if x.weekday == day.weekday()]
            if not win:
                continue
            rows, n = taken(d, day)
            if d.max_per_day and n >= d.max_per_day:
                continue  # the day is full
            open_.setdefault(key, {})[str(d.pk)] = win
            if rows:
                busy.setdefault(key, {})[str(d.pk)] = sorted([list(r) for r in rows])
    return dict(name=s.name[:80], phone=s.phone[:40], address=s.address[:120], note=(cs.online_note or "")[:200], days=days,
                today=today.isoformat(), docs=out_docs,
                svcs=[dict(i=sv.pk, n=sv.item.name[:80], p=float(sv.item.sale_price or 0), mi=_svc_minutes(sv), d=sv.doctor_id or 0) for sv in svcs[:60]],
                open=open_, busy=busy)


# ---------------------------------------------------------------- the request from the Worker
def _time(v):
    try:
        return dt.time.fromisoformat(str(v)[:5])
    except (TypeError, ValueError):
        raise ValidationError("الموعد غير صحيح")


def check_slot(doctor, service, day, t, today=None, exclude_rid=None):
    """Raises ValidationError when the patient may not take this time (the reason is shown on his page)."""
    from . import services as C

    today = today or timezone.localdate()
    cs = ClinicSettings.get()
    if day < today or day > today + dt.timedelta(days=int(cs.online_days or 14)):
        raise ValidationError("هذا اليوم غير متاح للحجز أونلاين")
    win = [x for x in doctor.shifts.all() if x.weekday == day.weekday()]
    mins = _svc_minutes(service)
    a0 = _mins(t)
    if not any(_mins(x.start) <= a0 and a0 + mins <= _mins(x.end) for x in win):
        raise ValidationError(f"{doctor} لا يعمل في هذا الموعد — اختر موعدًا آخر")
    if day == today and a0 < _mins(timezone.localtime().time()) + 15:
        raise ValidationError("فات هذا الموعد — اختر موعدًا آخر")
    if C.overlaps(doctor, day, t, mins):
        raise ValidationError("هذا الموعد حُجز للتو — اختر موعدًا آخر")
    for ob in OnlineBooking.objects.filter(doctor=doctor, date=day, state="pending", time__isnull=False).exclude(rid=exclude_rid or "").select_related("service"):
        b0 = _mins(ob.time)
        if a0 < b0 + _svc_minutes(ob.service) and b0 < a0 + mins:
            raise ValidationError("هذا الموعد محجوز لطلب آخر — اختر موعدًا آخر")
    rows, n = taken(doctor, day)
    if exclude_rid:
        n -= 1
    if doctor.max_per_day and n >= doctor.max_per_day:
        raise ValidationError(f"اكتمل حجز {doctor} في هذا اليوم — اختر يومًا آخر")


def ingest(o, today=None):
    """One request from the Worker → the OnlineBooking (never twice for the same id). Refused ones are kept answered «no»."""
    rid = str(o.get("id") or "")[:20]
    if not rid:
        return None
    ex = OnlineBooking.objects.filter(rid=rid).first()
    if ex:
        if ex.answered:  # the Worker did not hear our answer: send it again
            ex.answered = False
            ex.save(update_fields=["answered"])
        return ex
    today = today or timezone.localdate()
    name = " ".join(str(o.get("name") or "").split())[:80]
    phone = clean_phone(o.get("phone"))
    try:
        day = dt.date.fromisoformat(str(o.get("day") or ""))
    except ValueError:
        day = today
    sent = timezone.now()
    ob = OnlineBooking(rid=rid, name=name or "—", phone=phone or str(o.get("phone") or "")[:20], note=str(o.get("note") or "")[:200], date=day,
                       sent_at=sent)
    try:
        with transaction.atomic():
            s, cs = CompanySettings.get(), ClinicSettings.get()
            if not is_on(s, cs):
                raise ValidationError("الحجز أونلاين متوقف الآن — اتصل بالعيادة")
            if len(name) < 2:
                raise ValidationError("اكتب اسمك")
            if not phone:
                raise ValidationError("رقم الموبايل غير صحيح")
            doc = Doctor.objects.filter(pk=o.get("doc") or 0, active=True).prefetch_related("shifts").first()
            if not doc or not doc.shifts.all():
                raise ValidationError("هذا الطبيب غير متاح للحجز أونلاين")
            svc = Service.objects.filter(pk=o.get("svc") or 0, item__active=True).select_related("item").first() if o.get("svc") else None
            if o.get("svc") and (not svc or (svc.doctor_id and svc.doctor_id != doc.pk)):
                raise ValidationError("هذه الخدمة غير متاحة لدى الطبيب")
            t = _time(o.get("time"))
            ob.doctor, ob.service, ob.time = doc, svc, t
            if OnlineBooking.objects.filter(phone=phone, date=day, state__in=["pending", "ok"]).exists() or \
                    Appointment.objects.filter(patient__phone=phone, date=day, state__in=["booked", "waiting", "inside"], online__isnull=False).exists():
                raise ValidationError("لديك حجز في هذا اليوم بالفعل")
            if OnlineBooking.objects.filter(phone=phone, state="pending").count() >= MAX_WAITING_PER_PHONE:
                raise ValidationError("لديك طلبات حجز كثيرة تنتظر التأكيد — اتصل بالعيادة")
            check_slot(doc, svc, day, t, today)
            ob.state, ob.msg = "pending", "وصل طلبك ✔ — سيؤكده الاستقبال قريبًا"
            ob.save()
    except ValidationError as e:
        ob.pk = None
        ob.state, ob.msg = "no", "؛ ".join(e.messages)[:200]
        ob.save()
        return ob
    if cs.online_auto:
        try:
            approve(ob, "الحجز التلقائي")
        except ValidationError:
            pass  # stays for the reception to decide
        ob.refresh_from_db()
    return ob


def match_patient(ob):
    """The patient with the same mobile and name — else a new file."""
    for p in Patient.objects.filter(active=True).exclude(phone="").filter(phone__endswith=ob.phone[-9:]):
        if clean_phone(p.phone) == ob.phone and " ".join(p.name.split()) == ob.name:
            return p
    from . import services as C

    return C.add_patient(ob.name, ob.phone)


def known_patient(ob):
    for p in Patient.objects.filter(active=True).exclude(phone="").filter(phone__endswith=ob.phone[-9:]):
        if clean_phone(p.phone) == ob.phone:
            return p
    return None


def _who(user):
    if not user or isinstance(user, str):
        return str(user or "")[:100]
    return (user.get_full_name() or user.username)[:100]


@transaction.atomic
def approve(ob, user=None, allow_overlap=False):
    from . import followup as FU
    from . import services as C

    ob = OnlineBooking.objects.select_for_update().get(pk=ob.pk)
    if ob.state != "pending":
        raise ValidationError("تم الرد على هذا الطلب من قبل")
    p = match_patient(ob)
    appt = C.book(p, ob.date, doctor=ob.doctor, service=ob.service, time=ob.time, note=(NOTE + (" — " + ob.note if ob.note else ""))[:200],
                  user=user if user and not isinstance(user, str) else None, allow_overlap=allow_overlap)
    if isinstance(user, str):
        appt.created_by = user[:100]
        appt.save(update_fields=["created_by"])
    when = f"{FU.DAY_NAMES[ob.date.weekday()]} {ob.date:%d/%m/%Y}" + (f" الساعة {FU.t12(ob.time)}" if ob.time else "")
    ob.state, ob.appointment, ob.answered = "ok", appt, False
    ob.msg = f"تم تأكيد حجزك ✔ {when}" + (f" مع {ob.doctor}" if ob.doctor_id else "") + f" — رقم الدور {appt.queue_no}"
    ob.decided_by, ob.decided_at = _who(user), timezone.now()
    ob.save()
    return appt


@transaction.atomic
def deny(ob, user=None, reason=""):
    ob = OnlineBooking.objects.select_for_update().get(pk=ob.pk)
    if ob.state != "pending":
        raise ValidationError("تم الرد على هذا الطلب من قبل")
    s = CompanySettings.get()
    reason = (reason or "").strip()
    ob.state, ob.answered = "no", False
    ob.msg = ("نعتذر، لم نتمكن من تأكيد الموعد" + (f": {reason}" if reason else "") + (f" — للتواصل {s.phone}" if s.phone else ""))[:200]
    ob.decided_by, ob.decided_at = _who(user), timezone.now()
    ob.save()
    return ob


def confirm_text(ob, s=None):
    from . import followup as FU

    s = s or CompanySettings.get()
    when = f"يوم {FU.DAY_NAMES[ob.date.weekday()]} {ob.date:%d/%m/%Y}" + (f" الساعة {FU.t12(ob.time)}" if ob.time else "")
    q = f"، ورقم الدور {ob.appointment.queue_no}" if ob.appointment_id else ""
    doc = f" مع {ob.doctor}" if ob.doctor_id else ""
    tail = f" للإلغاء أو التعديل يُرجى الاتصال على {s.phone}." if s.phone else ""
    return f"السيد/ة {ob.name}، تحية طيبة من {s.name}. تم تأكيد حجزكم{doc} {when}{q}.{tail} مع تمنياتنا بدوام الصحة."


def waiting(today=None):
    today = today or timezone.localdate()
    return list(OnlineBooking.objects.filter(state="pending", date__gte=today).select_related("doctor", "service__item").order_by("date", "time", "id"))


def expire_old(today=None):
    """A request whose day passed with no answer is refused (so the patient's page does not wait for ever)."""
    today = today or timezone.localdate()
    n = OnlineBooking.objects.filter(state="pending", date__lt=today).update(state="no", answered=False, msg="انتهى موعد الطلب دون تأكيد — اتصل بالعيادة")
    return n


# ---------------------------------------------------------------- the loop: publish, pick up, answer
def _mark_file():
    return Path(settings.BASE_DIR) / "data" / "book-sent.json"


def push(force=False):
    s, cs = CompanySettings.get(), ClinicSettings.get()
    if not mobile.api():
        return "no-api"
    d = data(s, cs) if is_on(s, cs) else dict(off=True, name=s.name[:80], phone=s.phone[:40])
    h = hashlib.sha256(json.dumps(d, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
    f = _mark_file()
    try:
        mark = json.loads(f.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        mark = {}
    if not force and mark.get("h") == h and time.time() - mark.get("t", 0) < 3600:
        return "same"
    res = mobile.post("/book", dict(mobile.ident(s), d=d))
    if res.get("error"):
        raise ConnectionError(res["error"])
    f.parent.mkdir(exist_ok=True)
    f.write_text(json.dumps(dict(h=h, t=time.time())), encoding="utf-8")
    return "sent"


def answers():
    return [dict(id=ob.rid, st="got" if ob.state == "pending" else ob.state, msg=ob.msg)
            for ob in OnlineBooking.objects.filter(answered=False).order_by("id")[:50]]


def pull():
    s, cs = CompanySettings.get(), ClinicSettings.get()
    if s.business != "clinic" or not mobile.api():
        return IDLE
    if not cs.online_on:
        if _mark_file().exists():  # turned off: tell the page once
            try:
                push()
            except Exception:  # noqa: BLE001
                pass
        return IDLE
    expire_old()
    out = mobile.post("/book/pull", dict(mobile.ident(s)), timeout=20)
    got = out.get("reqs") or []
    for o in got:
        ingest(o)
    res = answers()
    if res:
        r = mobile.post("/book/pull", dict(mobile.ident(s), res=res), timeout=20)
        if r.get("ok"):
            OnlineBooking.objects.filter(rid__in=[x["id"] for x in res]).update(answered=True)
    push()  # the free times follow every booking
    if got or res:
        _state["last"] = time.time()
    return FAST if time.time() - _state["last"] < 900 else SLOW


def _loop():
    from django.db import close_old_connections

    time.sleep(25)
    while True:
        wait = SLOW
        try:
            close_old_connections()
            wait = pull()
        except Exception:  # noqa: BLE001 — offline / the Worker is down: next round
            wait = SLOW
        finally:
            close_old_connections()
        time.sleep(wait)


def start():
    if _state["started"]:
        return
    _state["started"] = True
    threading.Thread(target=_loop, name="trio-online-booking", daemon=True).start()
