"""Clinic: the follow-up visit («إعادة») within N days of a paid «كشف», doctor schedules (days, hours, visits per day),
reminders for the reception (tomorrow's bookings + due follow-ups) with a ready WhatsApp message."""
import datetime as dt
from decimal import Decimal
from urllib.parse import quote

from django.utils import timezone

from .models import Appointment, ClinicSettings, Doctor, DoctorShift, Visit, VisitLine

DAY_NAMES = {5: "السبت", 6: "الأحد", 0: "الاثنين", 1: "الثلاثاء", 2: "الأربعاء", 3: "الخميس", 4: "الجمعة"}


def days_ar(n):
    n = int(n)
    if n == 1:
        return "يوم واحد"
    if n == 2:
        return "يومين"
    return f"{n} أيام" if 3 <= n <= 10 else f"{n} يومًا"


def t12(t):
    h = t.hour % 12 or 12
    return f"{h}:{t.minute:02d} {'م' if t.hour >= 12 else 'ص'}"


# ---------------------------------------------------------------- «إعادة»
def rule(service):
    """(days, price) of the follow-up for this service: its own setting, else the clinic's."""
    cs = ClinicSettings.get()
    days = service.followup_days if service and service.followup_days is not None else cs.followup_days
    price = service.followup_price if service and service.followup_price is not None else cs.followup_price
    return int(days or 0), Decimal(price or 0)


def followup_for(patient, doctor, service, day=None, exclude_appt=None):
    """The paid full-price «كشف» this booking is a free / reduced follow-up of → dict(base, price, days, note) or None.
    Same patient + same doctor + same service, within N days, and only one «إعادة» per «كشف»."""
    if not patient or not service:
        return None
    days, price = rule(service)
    if not days:
        return None
    day = day or timezone.localdate()
    doctor = doctor or service.doctor
    lines = (VisitLine.objects.filter(visit__patient=patient, item=service.item, payment__isnull=False, payment__cancelled=False,
                                      price__gt=price, visit__followup_of__isnull=True,
                                      visit__opened_at__date__gte=day - dt.timedelta(days=days), visit__opened_at__date__lte=day)
             .exclude(visit__state="cancelled").select_related("visit").order_by("-visit__opened_at"))
    if doctor:
        lines = lines.filter(visit__doctor=doctor)
    for ln in lines:
        base = ln.visit
        if base.followups.exclude(state="cancelled").exists() or Appointment.objects.filter(
                followup_of=base, state__in=["booked", "waiting", "inside"]).exclude(pk=exclude_appt or 0).exists():
            continue
        d0 = timezone.localtime(base.opened_at).date()
        return dict(base=base, price=price, days=days, date=d0,
                    note=f"إعادة — خلال {days_ar(days)} من كشف {d0:%d/%m/%Y}")
    return None


# ---------------------------------------------------------------- doctor schedules
def shifts_on(doctor, day):
    return [s for s in doctor.shifts.all() if s.weekday == day.weekday()]


def schedule_text(doctor):
    """«السبت، الاثنين 5:00 م – 10:00 م» grouped by the same hours."""
    by = {}
    for s in doctor.shifts.all():
        by.setdefault((s.start, s.end), []).append(s.weekday)
    order = [5, 6, 0, 1, 2, 3, 4]
    return " · ".join("، ".join(DAY_NAMES[d] for d in sorted(ds, key=order.index)) + f" {t12(a)} – {t12(b)}" for (a, b), ds in by.items())


def schedule_warnings(doctor, day, time=None, exclude=None):
    """What the reception must know before booking: the doctor does not work that day / at that time, or his day is full."""
    out = []
    if not doctor:
        return out
    sh = list(doctor.shifts.all())
    if sh:
        today = shifts_on(doctor, day)
        if not today:
            out.append(f"{doctor} لا يعمل يوم {DAY_NAMES[day.weekday()]} — مواعيده: {schedule_text(doctor)}")
        elif time and not any(s.start <= time < s.end for s in today):
            out.append(f"الساعة {t12(time)} خارج مواعيد {doctor} يوم {DAY_NAMES[day.weekday()]} ("
                       + "، ".join(f"{t12(s.start)} – {t12(s.end)}" for s in today) + ")")
    if doctor.max_per_day:
        n = Appointment.objects.filter(doctor=doctor, date=day).exclude(state__in=["cancelled", "noshow"]).exclude(pk=exclude or 0).count()
        if n >= doctor.max_per_day:
            out.append(f"اكتمل عدد كشوفات {doctor} يوم {day:%d/%m} ({n} من {doctor.max_per_day})")
    return out


def save_shifts(doctor, rows):
    """rows = [(weekday, start, end)] — replaces the doctor's schedule."""
    from django.core.exceptions import ValidationError

    clean = []
    for wd, a, b in rows:
        if not a or not b:
            continue
        try:
            a, b = dt.time.fromisoformat(str(a)[:5]), dt.time.fromisoformat(str(b)[:5])
        except ValueError:
            raise ValidationError("ساعة غير صحيحة في مواعيد الطبيب")
        if b <= a:
            raise ValidationError(f"يوم {DAY_NAMES[int(wd)]}: ساعة النهاية قبل ساعة البداية")
        clean.append(DoctorShift(doctor=doctor, weekday=int(wd), start=a, end=b))
    DoctorShift.objects.filter(doctor=doctor).delete()
    DoctorShift.objects.bulk_create(clean)


def doctors_today(day=None):
    """The reception board: who works today, his hours and how many are booked."""
    day = day or timezone.localdate()
    out = []
    for d in Doctor.objects.filter(active=True).prefetch_related("shifts"):
        sh = shifts_on(d, day)
        n = Appointment.objects.filter(doctor=d, date=day).exclude(state__in=["cancelled", "noshow"]).count()
        if sh or (not d.shifts.all() and n):
            out.append(dict(d=d, hours="، ".join(f"{t12(s.start)} – {t12(s.end)}" for s in sh), n=n, max=d.max_per_day))
    return out


# ---------------------------------------------------------------- reminders
def wa_number(phone):
    p = "".join(ch for ch in str(phone or "").translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")) if "0" <= ch <= "9")
    if p.startswith("0020"):
        p = p[4:]
    elif p.startswith("20") and len(p) == 12:
        p = p[2:]
    if p.startswith("0"):
        p = p[1:]
    return "20" + p if len(p) == 10 and p.startswith("1") else ""


def _title(p):
    return {"m": "الأستاذ", "f": "الأستاذة"}.get(p.gender, "الأستاذ/ة")


def booking_text(a, s):
    when = f"يوم {DAY_NAMES[a.date.weekday()]} {a.date:%d/%m/%Y}" + (f" الساعة {t12(a.time)}" if a.time else "")
    doc = f" مع {a.doctor}" if a.doctor_id else ""
    tail = f" للإلغاء أو التعديل يُرجى الاتصال على {s.phone}." if s.phone else ""
    return (f"{_title(a.patient)} {a.patient.name}، تحية طيبة من {s.name}. نذكّركم بموعد حجزكم{doc} {when}، ورقم الدور {a.queue_no}."
            f"{tail} مع تمنياتنا بدوام الصحة.")


def followup_text(v, s):
    doc = f" مع {v.doctor}" if v.doctor_id else ""
    tail = f" يُرجى الاتصال على {s.phone} لتحديد الموعد المناسب." if s.phone else " يُرجى التواصل معنا لتحديد الموعد المناسب."
    return (f"{_title(v.patient)} {v.patient.name}، تحية طيبة من {s.name}. نذكّركم بموعد المتابعة{doc} يوم "
            f"{DAY_NAMES[v.next_visit.weekday()]} {v.next_visit:%d/%m/%Y}.{tail} مع تمنياتنا بدوام الصحة.")


def wa_link(phone, text):
    n = wa_number(phone)
    return f"https://wa.me/{n}?text={quote(text)}" if n else ""


def reminders(day=None):
    """Tomorrow's bookings and the follow-ups due (from yesterday to tomorrow, without a booking yet)."""
    from core.models import CompanySettings

    s = CompanySettings.get()
    day = day or timezone.localdate()
    tom = day + dt.timedelta(days=1)
    books = []
    for a in Appointment.objects.filter(date=tom, state="booked").select_related("patient", "doctor").order_by("queue_no", "time"):
        txt = booking_text(a, s)
        books.append(dict(a=a, text=txt, wa=wa_link(a.patient.phone, txt)))
    fol = []
    booked = set(Appointment.objects.filter(date__gte=day - dt.timedelta(days=1), date__lte=tom + dt.timedelta(days=7))
                 .exclude(state__in=["cancelled", "noshow"]).values_list("patient_id", flat=True))
    for v in (Visit.objects.filter(next_visit__gte=day - dt.timedelta(days=1), next_visit__lte=tom).exclude(state="cancelled")
              .select_related("patient", "doctor").order_by("next_visit")):
        if v.patient_id in booked:
            continue
        txt = followup_text(v, s)
        fol.append(dict(v=v, text=txt, wa=wa_link(v.patient.phone, txt)))
    return dict(day=day, tomorrow=tom, bookings=books, followups=fol)


# ---------------------------------------------------------------- the TV queue screen
def queue_state(day=None):
    day = day or timezone.localdate()
    rows = []
    appts = list(Appointment.objects.filter(date=day, state__in=["waiting", "inside"]).select_related("doctor", "patient")
                 .order_by("queue_no", "id"))
    docs = {a.doctor_id: a.doctor for a in appts if a.doctor_id}
    last_done = {}
    for a in Appointment.objects.filter(date=day, state="done", doctor__isnull=False).order_by("queue_no"):
        last_done[a.doctor_id] = a.queue_no
    for did, d in sorted(docs.items(), key=lambda x: (x[1].order, x[0])):
        mine = [a for a in appts if a.doctor_id == did]
        cur = next((a for a in mine if a.state == "inside"), None)
        rows.append(dict(doctor=str(d), specialty=d.specialty, current=cur.queue_no if cur else None, last=last_done.get(did),
                         next=[a.queue_no for a in mine if a.state == "waiting"][:5]))
    free = [a.queue_no for a in appts if not a.doctor_id and a.state == "waiting"][:5]
    if free:
        rows.append(dict(doctor="الانتظار", specialty="", current=None, last=None, next=free))
    return rows
