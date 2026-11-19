from datetime import timedelta as _td
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from core.models import CompanySettings
from inventory.models import Item
from pos import voids as V
from treasury.models import CashBox

from . import services as C
from . import files as F
from .models import Appointment, Doctor, InsuranceCompany, Patient, PatientFile, Service, Visit, VisitLine, VisitPayment

STAFF = ["مدير المحل", "كاشير", "دكتور", "استقبال"]


def _ok(u):
    return u.is_superuser or u.groups.filter(name__in=STAFF).exists()


def guard(view):
    @login_required
    def wrap(request, *a, **k):
        if not _ok(request.user):
            raise PermissionDenied("هذه الصفحة لفريق العيادة")
        return view(request, *a, **k)

    wrap.__name__ = view.__name__
    return wrap


def _can_files(u):
    """Results and x-rays: the doctors, the reception and the owner (not a plain cashier)."""
    return u.is_superuser or V.is_owner(u) or u.groups.filter(name__in=["دكتور", "استقبال"]).exists()


def _owner(request):
    if not V.is_owner(request.user):
        raise PermissionDenied("هذه الصفحة لصاحب العيادة")


def _errs(request, e):
    for m in getattr(e, "messages", [str(e)]):
        messages.error(request, m)


def _dec(x, default=None):
    try:
        return Decimal(str(x).replace("٫", ".").replace(",", ".").strip()) if str(x or "").strip() else default
    except InvalidOperation:
        raise ValidationError(f"رقم غير صحيح: {x}")


def _doctor(pk):
    return Doctor.objects.filter(pk=pk or 0).first()


def _service(pk):
    return Service.objects.filter(pk=pk or 0).select_related("item", "doctor").first()


def _patient_from(request):
    """The patient picked in a form (id) or a new one typed in (name + phone)."""
    p = Patient.objects.filter(pk=request.POST.get("patient") or 0).first()
    if p:
        return p
    name = request.POST.get("new_name", "").strip()
    if not name:
        raise ValidationError("اختر المريض أو اكتب اسم مريض جديد")
    phone = request.POST.get("new_phone", "").strip()
    same = Patient.objects.filter(name=name, phone=phone).first() if phone else None
    return same or C.add_patient(name, phone)


def _pay_from(request):
    """The «دُفع الآن» part of a booking / walk-in form: {} when it is paid later at the visit."""
    if not request.POST.get("pay_now"):
        return {}
    pay = request.POST.get("pay", "cash")
    out = dict(pay=pay, discount=_dec(request.POST.get("discount"), Decimal(0)))
    if pay == "split":
        out.update(paid_card=_dec(request.POST.get("card"), Decimal(0)), paid_credit=_dec(request.POST.get("credit"), Decimal(0)))
    return out


def _paid_redirect(appt):
    """After a paid booking: straight to its receipt (it prints itself)."""
    if appt.visit_id and appt.visit.live_payments:
        return redirect(f"/ui/clinic/visits/{appt.visit_id}/receipt/?auto=1")
    return None


def _ctx(**kw):
    return dict(doctors=Doctor.objects.filter(active=True), services=Service.objects.filter(item__active=True).select_related("item", "doctor"),
                today=timezone.localdate(), **kw)


# ---------------------------------------------------------------- today
def _my_doctor(u):
    return Doctor.objects.filter(user=u, active=True).first() if u.is_authenticated else None


def _is_doctor(u):
    return u.groups.filter(name="دكتور").exists() and not V.is_owner(u)


@guard
def home(request, reception=False):
    if request.method == "GET" and not reception:
        if _is_doctor(request.user):  # each screen opens on the right place
            return redirect("clinic_doctor_screen")
        if request.user.groups.filter(name="استقبال").exists() and not V.is_owner(request.user):
            return redirect("clinic_reception")
    if request.method == "POST":
        a = request.POST.get("action")
        try:
            if a == "walkin":
                appt = C.walk_in(_patient_from(request), doctor=_doctor(request.POST.get("doctor")),
                                 service=_service(request.POST.get("service")), user=request.user, **_pay_from(request))
                messages.success(request, f"{appt.patient.name} دخل الدور — رقم {appt.queue_no}")
                r = _paid_redirect(appt)
                if r:
                    return r
            elif a == "arrive":
                appt = C.arrive(get_object_or_404(Appointment, pk=request.POST["appt"]), request.user)
                messages.success(request, f"{appt.patient.name} وصل — دوره {appt.queue_no}")
            elif a in ("inside", "noshow", "cancelled", "waiting"):
                appt = get_object_or_404(Appointment, pk=request.POST["appt"])
                C.set_state(appt, a, request.user, request.POST.get("reason", ""))
                if a == "inside" and appt.visit_id:
                    return redirect("clinic_visit", appt.visit_id)
        except ValidationError as e:
            _errs(request, e)
        return redirect("clinic_reception" if reception else "clinic_home")
    day = timezone.localdate()
    appts = list(Appointment.objects.filter(date=day).select_related("patient", "doctor", "service__item", "visit", "patient__insurance"))
    lanes = {k: [a for a in appts if a.state == k] for k in ("booked", "waiting", "inside", "done")}
    owner = V.is_owner(request.user) and not reception
    return render(request, "clinic/home.html", _ctx(lanes=lanes, sum=C.today_summary(day) if owner else None, owner=owner,
                                                    others=[a for a in appts if a.state in ("cancelled", "noshow")],
                                                    upcoming=Appointment.objects.filter(date__gt=day, state="booked").count(),
                                                    follow=Visit.objects.filter(next_visit=day).exclude(state="cancelled").select_related("patient")[:20],
                                                    reception=reception,
                                                    unpaid=[v for v in Visit.objects.filter(state="open", opened_at__date=day).select_related("patient", "doctor")
                                                            .prefetch_related("lines") if v.unbilled > 0]))


@guard
def reception(request):
    """The reception desk: the day's queue, walk-ins, bookings and the bills to collect (no owner numbers)."""
    return home(request, reception=True)


@guard
def doctor_screen(request):
    """«شاشة الدكتور»: his queue today — who is inside, who is next — the patient's file at a glance, one button for the next."""
    u = request.user
    doc = _my_doctor(u)
    if V.is_owner(u) or u.is_superuser:
        doc = Doctor.objects.filter(pk=request.GET.get("d") or 0).first() or doc or Doctor.objects.filter(active=True).first()
    if not doc:
        return render(request, "clinic/doctor_screen.html", _ctx(doc=None, all_docs=[]))
    day = timezone.localdate()
    mine = Appointment.objects.filter(date=day).filter(Q(doctor=doc) | Q(doctor__isnull=True)).select_related(
        "patient", "patient__insurance", "service__item", "visit")
    if request.method == "POST":
        try:
            a = request.POST.get("action")
            if a == "next":
                cur = mine.filter(state="inside", doctor=doc).first()
                if cur:  # done with him; anything not paid yet shows at the reception
                    Appointment.objects.filter(pk=cur.pk).update(state="done")
                nxt = mine.filter(state="waiting").order_by("queue_no", "id").first()
                if not nxt:
                    raise ValidationError("لا أحد ينتظر الآن")
                if not nxt.visit_id:
                    nxt = C.arrive(nxt, u)
                if not nxt.doctor_id:
                    Appointment.objects.filter(pk=nxt.pk).update(doctor=doc)
                    Visit.objects.filter(pk=nxt.visit_id, doctor__isnull=True).update(doctor=doc)
                C.set_state(nxt, "inside", u)
                messages.success(request, f"تفضّل {nxt.patient.name} — دور {nxt.queue_no}")
                return redirect("clinic_visit", nxt.visit_id)
        except ValidationError as e:
            _errs(request, e)
        return redirect(request.get_full_path())
    lanes = list(mine)
    inside = next((a for a in lanes if a.state == "inside" and a.doctor_id == doc.pk), None)
    waiting = [a for a in lanes if a.state == "waiting"]
    paid = Visit.objects.filter(doctor=doc, state="paid", opened_at__date=day)
    ctx = dict(doc=doc, inside=inside, waiting=waiting, done=[a for a in lanes if a.state == "done" and a.doctor_id == doc.pk],
               booked=[a for a in lanes if a.state == "booked"], seen=paid.count(),
               share=paid.aggregate(s=Sum("doctor_share"))["s"] or 0,
               all_docs=Doctor.objects.filter(active=True) if (V.is_owner(u) or u.is_superuser) else [])
    if inside:
        p = inside.patient
        ctx.update(history=p.visits.exclude(pk=inside.visit_id).exclude(state="cancelled").select_related("doctor")[:5],
                   files=p.files.defer("data")[:8], can_files=True)
    return render(request, "clinic/doctor_screen.html", _ctx(**ctx))


@guard
def owner_board(request):
    """«لوحة صاحب العيادة»: the money of the clinic in one page — today, this month, by doctor, what is owed."""
    _owner(request)
    from pos.services import customer_balance  # noqa: F401

    day = timezone.localdate()
    m1 = day.replace(day=1)
    d1 = C.parse_date(request.GET.get("d1"), m1) if request.GET.get("d1") else m1
    d2 = C.parse_date(request.GET.get("d2")) if request.GET.get("d2") else day
    pays = VisitPayment.objects.filter(cancelled=False, created_at__date__gte=d1, created_at__date__lte=d2).select_related("order")
    cash = card = credit = ins = Decimal(0)
    for p in pays:
        o = p.order
        cash += o.paid_cash
        card += o.paid_card
        if p.insurance_id:
            ins += p.insurance_amount
        else:
            credit += o.paid_credit
    total = cash + card + credit + ins
    daily = {}
    for p in VisitPayment.objects.filter(cancelled=False, created_at__date__gte=day - _td(days=29)):
        k = timezone.localtime(p.created_at).date()
        daily[k] = daily.get(k, Decimal(0)) + p.total
    days = [(day - _td(days=i)) for i in range(29, -1, -1)]
    peak = max([daily.get(d, 0) for d in days] + [1])
    bars = [dict(d=d, v=daily.get(d, 0), h=int(100 * float(daily.get(d, 0)) / float(peak))) for d in days]
    from django.db.models import Count, DecimalField, F

    top = (VisitLine.objects.filter(payment__cancelled=False, payment__created_at__date__gte=d1, payment__created_at__date__lte=d2)
           .values("item__name").annotate(n=Count("id"), v=Sum(F("qty") * F("price") - F("discount"), output_field=DecimalField())).order_by("-v")[:8])
    patients_owed = sum((C._balance(p.partner) for p in Patient.objects.filter(partner__isnull=False).select_related("partner")), Decimal(0))
    ins_owed = sum((C._balance(i.partner) for i in InsuranceCompany.objects.filter(partner__isnull=False).select_related("partner")), Decimal(0))
    return render(request, "clinic/owner.html", _ctx(
        d1=d1, d2=d2, tday=C.today_summary(day), total=total, cash=cash, card=card, credit=credit, ins=ins, bars=bars,
        doc_rows=C.shares_report(d1, d2), top=top, patients_owed=patients_owed, ins_owed=ins_owed,
        visits=Visit.objects.filter(state="paid", opened_at__date__gte=d1, opened_at__date__lte=d2).count(),
        new_patients=Patient.objects.filter(created_at__date__gte=d1, created_at__date__lte=d2).count()))


# ---------------------------------------------------------------- bookings
@guard
def appointments(request):
    day = C.parse_date(request.GET.get("d"))
    if request.method == "POST" and request.POST.get("appt"):  # cancel a booking from the list (a paid one is refunded)
        try:
            appt = get_object_or_404(Appointment, pk=request.POST["appt"])
            C.set_state(appt, "cancelled", request.user, request.POST.get("reason", ""))
            messages.success(request, f"أُلغي حجز {appt.patient.name}")
        except ValidationError as e:
            _errs(request, e)
        return redirect(f"{request.path}?d={day.isoformat()}")
    if request.method == "POST":
        try:
            day = C.parse_date(request.POST.get("date"), day)
            t = request.POST.get("time") or None
            appt = C.book(_patient_from(request), day, doctor=_doctor(request.POST.get("doctor")), service=_service(request.POST.get("service")),
                          time=t, note=request.POST.get("note", ""), user=request.user, **_pay_from(request))
            messages.success(request, f"تم الحجز {appt.patient.name} يوم {day:%d/%m} — رقم {appt.queue_no}")
            r = _paid_redirect(appt)
            if r:
                return r
        except ValidationError as e:
            _errs(request, e)
        return redirect(f"{request.path}?d={day.isoformat()}")
    import datetime as dt

    days = [day + dt.timedelta(days=i) for i in range(-1, 6)]
    return render(request, "clinic/appointments.html", _ctx(
        day=day, days=days, appts=Appointment.objects.filter(date=day).select_related("patient", "doctor", "service__item", "visit"),
        counts={d: Appointment.objects.filter(date=d).exclude(state__in=["cancelled"]).count() for d in days},
        pick=Patient.objects.filter(pk=request.GET.get("p") or 0).first()))


# ---------------------------------------------------------------- patients
@guard
def patient_find(request):
    q = (request.GET.get("q") or "").strip()
    qs = Patient.objects.filter(active=True)
    if q:
        f = Q(name__icontains=q) | Q(phone__contains=q)
        if q.isdigit():
            f |= Q(number=int(q))
        qs = qs.filter(f)
    return JsonResponse({"items": [dict(id=p.pk, label=f"{p.name} — {p.phone or 'بدون رقم'} (ملف {p.number})") for p in qs[:20]]})


@guard
def patients(request):
    if request.method == "POST":
        try:
            p = C.add_patient(request.POST.get("name"), request.POST.get("phone"), gender=request.POST.get("gender", ""),
                              birth_year=int(request.POST["birth_year"]) if request.POST.get("birth_year", "").isdigit() else None,
                              address=request.POST.get("address", "")[:200], medical_notes=request.POST.get("medical_notes", ""))
            messages.success(request, f"فُتح ملف {p.name} — رقم {p.number}")
            return redirect("clinic_patient", p.pk)
        except ValidationError as e:
            _errs(request, e)
        return redirect("clinic_patients")
    q = (request.GET.get("q") or "").strip()
    qs = Patient.objects.filter(active=True)
    if q:
        f = Q(name__icontains=q) | Q(phone__contains=q)
        if q.isdigit():
            f |= Q(number=int(q))
        qs = qs.filter(f)
    return render(request, "clinic/patients.html", _ctx(q=q, patients=qs[:200], total=Patient.objects.filter(active=True).count()))


@guard
def patient(request, pk):
    p = get_object_or_404(Patient, pk=pk)
    if request.method == "POST":
        a = request.POST.get("action")
        try:
            if a == "save":
                p.name = request.POST.get("name", p.name).strip()[:120] or p.name
                p.phone = request.POST.get("phone", "").strip()[:40]
                p.gender = request.POST.get("gender", "")
                p.birth_year = int(request.POST["birth_year"]) if request.POST.get("birth_year", "").isdigit() else None
                p.address = request.POST.get("address", "")[:200]
                p.medical_notes = request.POST.get("medical_notes", "")
                if "insurance" in request.POST:
                    p.insurance = InsuranceCompany.objects.filter(pk=request.POST.get("insurance") or 0).first()
                    p.insurance_no = request.POST.get("insurance_no", "").strip()[:60]
                    p.insurance_holder = request.POST.get("insurance_holder", "").strip()[:120]
                    p.insurance_expiry = C.parse_date(request.POST.get("insurance_expiry")) if request.POST.get("insurance_expiry") else None
                    if p.insurance and not p.insurance_no:
                        raise ValidationError("اكتب الرقم التأميني للمريض")
                p.save()
                messages.success(request, "تم الحفظ ✔")
            elif a == "file":
                F.attach(p, request.FILES.get("file"), kind=request.POST.get("kind", "lab"), title=request.POST.get("title", ""),
                         taken_on=C.parse_date(request.POST.get("taken_on")) if request.POST.get("taken_on") else None,
                         visit=Visit.objects.filter(pk=request.POST.get("visit") or 0, patient=p).first(), user=request.user)
                messages.success(request, "تم رفع الملف ✔ — في ملف المريض")
            elif a == "file_del":
                f = get_object_or_404(PatientFile, pk=request.POST.get("file"), patient=p)
                f.delete()
                messages.success(request, "حُذف الملف")
            elif a == "collect":
                if not p.partner_id:
                    raise ValidationError("ليس لهذا المريض حساب آجل")
                box = get_object_or_404(CashBox, pk=request.POST.get("box"), active=True)
                C.collect(p.partner, _dec(request.POST.get("amount"), Decimal(0)), box, request.user, note=f"من {p.name}")
                messages.success(request, f"حُصّل {request.POST.get('amount')} ج من {p.name} ✔")
            elif a == "visit":
                appt = C.walk_in(p, doctor=_doctor(request.POST.get("doctor")), service=_service(request.POST.get("service")), user=request.user)
                return redirect("clinic_visit", appt.visit_id)
        except ValidationError as e:
            _errs(request, e)
        return redirect("clinic_patient", p.pk)
    from pos.services import customer_balance

    return render(request, "clinic/patient.html", _ctx(
        st=C.patient_statement(p), files=p.files.defer("data"), kinds=PatientFile.KINDS, insurers=InsuranceCompany.objects.filter(active=True),
        boxes=CashBox.objects.filter(active=True).order_by("order", "pk"), can_files=_can_files(request.user),
        p=p, visits=p.visits.select_related("doctor").prefetch_related("lines__item"),
        appts=p.appointments.filter(date__gte=timezone.localdate()).exclude(state__in=["cancelled", "done"]).select_related("doctor"),
        balance=customer_balance(p.partner) if p.partner_id else 0))


# ---------------------------------------------------------------- visit
@guard
def visit(request, pk):
    v = get_object_or_404(Visit.objects.select_related("patient", "doctor", "order"), pk=pk)
    if request.method == "POST":
        a = request.POST.get("action")
        try:
            if a in ("save", "pay", "add"):
                for f in ("complaint", "diagnosis", "prescription", "notes"):
                    if f in request.POST:
                        setattr(v, f, request.POST[f])
                v.next_visit = C.parse_date(request.POST["next_visit"]) if request.POST.get("next_visit") else None
                if not v.live_payments and "doctor" in request.POST:
                    v.doctor = _doctor(request.POST.get("doctor"))
                v.save()
                if v.state != "cancelled":
                    for ln in v.lines.filter(payment__isnull=True):
                        if request.POST.get(f"drop_{ln.pk}"):
                            ln.delete()
                            continue
                        if f"qty_{ln.pk}" in request.POST:
                            ln.qty = _dec(request.POST[f"qty_{ln.pk}"], ln.qty)
                            ln.price = _dec(request.POST.get(f"price_{ln.pk}"), ln.price)
                            if ln.qty <= 0:
                                ln.delete()
                                continue
                            ln.save()
                    code = request.POST.get("add_code", "").strip()
                    if code:
                        it = Item.objects.filter(Q(code=code) | Q(name=code) | Q(barcode=code), active=True).first()
                        if not it:
                            raise ValidationError(f"لا توجد خدمة / صنف «{code}»")
                        C.add_line(v, it, request.POST.get("add_qty") or 1)
                if v.state == "open" and v.live_payments and not v.lines.filter(payment__isnull=True).exists():
                    v.state = "paid"
                    v.save(update_fields=["state"])
                if a == "pay":
                    pay = request.POST.get("pay", "cash")
                    v.refresh_from_db()
                    total = v.unbilled - (_dec(request.POST.get("discount"), Decimal(0)))
                    if pay in ("insurance", "insurance_card"):
                        C.checkout(v, request.user, discount=request.POST.get("discount") or 0, insured=True, patient_by_card=pay == "insurance_card")
                    else:
                        if pay == "split":
                            card, credit = _dec(request.POST.get("card"), 0), _dec(request.POST.get("credit"), 0)
                        else:
                            card, credit = (total if pay == "card" else 0), (total if pay == "credit" else 0)
                        C.checkout(v, request.user, paid_card=card or 0, paid_credit=credit or 0, discount=request.POST.get("discount") or 0)
                    messages.success(request, f"تمت محاسبة الزيارة ✔ — {v.patient.name}")
                    from inventory.reorder import popup

                    popup(request, v.lines.values_list("item_id", flat=True))
                    if request.POST.get("print"):
                        return redirect(f"/ui/clinic/visits/{v.pk}/receipt/?auto=1")
                else:
                    messages.success(request, "تم الحفظ ✔")
            elif a == "finish":  # the doctor is done: the patient leaves the queue (what is not paid yet waits at the reception)
                Appointment.objects.filter(visit=v).update(state="done")
                messages.success(request, f"انتهى كشف {v.patient.name} ✔" + (f" — عليه {v.unbilled:,.2f} ج يدفعها في الاستقبال" if v.unbilled else ""))
                return redirect("clinic_doctor_screen" if _is_doctor(request.user) else "clinic_home")
            elif a == "cancel":
                C.cancel_visit(v, request.POST.get("reason", ""), request.user)
                messages.success(request, "أُلغيت الزيارة — أُعيد المبلغ بقيد عكسي وعادت المستهلكات إلى المخزن")
            elif a == "reopen":
                C.reopen_visit(v, request.POST.get("reason", ""), request.user)
                messages.success(request, "أُعيد فتح الحساب — عدّل وحاسب من جديد")
        except ValidationError as e:
            _errs(request, e)
        return redirect("clinic_visit", v.pk)
    items = Item.objects.filter(active=True, sale_price__isnull=False).exclude(code="0").order_by("item_type", "name")
    ins = C.insurance_of(v.patient)
    ins_share = C.insurance_share(ins, v.unbilled) if ins else None
    return render(request, "clinic/visit.html", _ctx(
        insurance=ins, ins_share=ins_share, ins_rest=(v.unbilled - ins_share) if ins else None, files=v.patient.files.defer("data")[:12],
        v=v, lines=v.lines.select_related("item", "payment"), items=items, can_edit=V.can_edit(request.user),
        payments=v.payments.select_related("order"), unbilled=v.unbilled,
        in_queue=Appointment.objects.filter(visit=v, state__in=["waiting", "inside"]).exists(),
        history=v.patient.visits.exclude(pk=v.pk).exclude(state="cancelled").select_related("doctor")[:10]))


@guard
def receipt(request, pk):
    """The thermal receipt of a visit's payment (the booking receipt or a later bill): with the queue number."""
    v = get_object_or_404(Visit.objects.select_related("patient", "doctor"), pk=pk)
    pays = list(v.payments.filter(cancelled=False).select_related("order"))
    pay = next((p for p in pays if str(p.pk) == request.GET.get("p")), pays[-1] if pays else None)
    appt = Appointment.objects.filter(visit=v).first()
    return render(request, "clinic/receipt.html", dict(v=v, s=CompanySettings.get(), hide_nav=True, pay=pay, appt=appt,
                                                       lines=pay.lines.select_related("item") if pay else [], auto=request.GET.get("auto") == "1"))


@guard
def print_rx(request, pk):
    v = get_object_or_404(Visit.objects.select_related("patient", "doctor"), pk=pk)
    return render(request, "clinic/print.html", dict(v=v, s=CompanySettings.get(), hide_nav=True, lines=v.lines.select_related("item"),
                                                     receipt=request.GET.get("bill") == "1"))


# ---------------------------------------------------------------- setup (owner)
@guard
def setup(request):
    _owner(request)
    if request.method == "POST":
        a = request.POST.get("action")
        try:
            if a == "doctor":
                doc = Doctor.objects.filter(pk=request.POST.get("pk") or 0).first() or Doctor()
                doc.name = request.POST.get("name", "").strip()[:100]
                if not doc.name:
                    raise ValidationError("اكتب اسم الطبيب")
                doc.specialty = request.POST.get("specialty", "")[:100]
                doc.phone = request.POST.get("phone", "")[:40]
                doc.share_pct = _dec(request.POST.get("share_pct"), Decimal(0))
                if not (0 <= doc.share_pct <= 100):
                    raise ValidationError("النسبة من 0 إلى 100")
                doc.active = request.POST.get("active", "1") == "1"
                doc.save()
                if doc.partner_id:
                    doc.partner.name = f"د. {doc.name}"
                    doc.partner.save(update_fields=["name"])
                messages.success(request, f"تم الحفظ {doc}")
            elif a == "service":
                svc = _service(request.POST.get("pk"))
                cons = []
                for code, q in zip(request.POST.getlist("c_item"), request.POST.getlist("c_qty")):
                    if code.strip():
                        it = Item.objects.filter(Q(code=code.strip()) | Q(name=code.strip()), active=True, item_type="stock").first()
                        if not it:
                            raise ValidationError(f"لا يوجد صنف مخزني «{code}»")
                        cons.append((it, _dec(q, Decimal(1))))
                svc = C.save_service(request.POST.get("name"), _dec(request.POST.get("price"), Decimal(0)), doctor=_doctor(request.POST.get("doctor")),
                                     share_pct=request.POST.get("share_pct", "").strip() or None, minutes=request.POST.get("minutes") or 15,
                                     consumables=cons, service=svc)
                messages.success(request, f"تم حفظ «{svc.item.name}»")
            elif a == "service_off":
                svc = _service(request.POST.get("pk"))
                if svc:
                    svc.item.active = False
                    svc.item.save(update_fields=["active"])
                    messages.success(request, f"أُزيلت «{svc.item.name}» من القائمة")
        except ValidationError as e:
            _errs(request, e)
        return redirect("clinic_setup")
    edit_doc = Doctor.objects.filter(pk=request.GET.get("doc") or 0).first()
    edit_svc = _service(request.GET.get("svc"))
    return render(request, "clinic/setup.html", _ctx(
        all_doctors=Doctor.objects.all(), edit_doc=edit_doc, edit_svc=edit_svc,
        edit_cons=list(edit_svc.item.recipe.lines.select_related("ingredient")) if edit_svc and hasattr(edit_svc.item, "recipe") else [],
        stock_items=Item.objects.filter(active=True, item_type="stock").exclude(code="0").order_by("name")))


@guard
def doctors(request):
    _owner(request)
    import datetime as dt

    today = timezone.localdate()
    d1 = C.parse_date(request.GET.get("from"), today.replace(day=1))
    d2 = C.parse_date(request.GET.get("to"), today)
    if request.method == "POST":
        try:
            doc = get_object_or_404(Doctor, pk=request.POST["doctor"])
            box = get_object_or_404(CashBox, pk=request.POST["box"])
            amt = _dec(request.POST.get("amount"), Decimal(0))
            if amt > C.doctor_balance(doc):
                raise ValidationError(f"المبلغ أكبر من مستحقات {doc} ({C.doctor_balance(doc)})")
            C.pay_doctor(doc, amt, cash_account=box.account, user=request.user, note=request.POST.get("note", ""))
            messages.success(request, f"صُرف {amt} لـ {doc} من {box.name}")
        except (ValidationError, KeyError) as e:
            _errs(request, e)
        return redirect(f"{request.path}?from={d1}&to={d2}")
    rows = C.shares_report(d1, d2)
    return render(request, "clinic/doctors.html", _ctx(rows=rows, d1=d1, d2=d2, boxes=CashBox.objects.filter(active=True),
                                                       visits=Visit.objects.filter(state="paid", opened_at__date__gte=d1, opened_at__date__lte=d2)
                                                       .select_related("patient", "doctor").order_by("-opened_at")[:300],
                                                       last_month=(today.replace(day=1) - dt.timedelta(days=1))))


@guard
def patient_file(request, pk):
    """Shows an attached result / x-ray (inline for images and PDF, as a download otherwise)."""
    from django.http import HttpResponse

    if not _can_files(request.user):
        raise PermissionDenied
    f = get_object_or_404(PatientFile, pk=pk)
    r = HttpResponse(bytes(f.data), content_type=f.content_type)
    from urllib.parse import quote

    r["Content-Disposition"] = f"{'inline' if F.inline(f.content_type) else 'attachment'}; filename*=UTF-8''{quote(f.filename)}"
    r["X-Content-Type-Options"] = "nosniff"
    r["Content-Security-Policy"] = "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; sandbox"
    r["Cache-Control"] = "private, max-age=600"
    return r


# ---------------------------------------------------------------- medical insurance (1.21)
def _ins_from_post(request, ins):
    ins.name = request.POST.get("name", "").strip()[:120]
    if not ins.name:
        raise ValidationError("اكتب اسم الشركة")
    pct = _dec(request.POST.get("coverage_pct"), Decimal(0))
    if not (0 <= pct <= 100):
        raise ValidationError("نسبة التحمل من 0 إلى 100")
    ins.coverage_pct, ins.max_per_visit = pct, _dec(request.POST.get("max_per_visit"))
    for f, n in (("phone", 40), ("contact", 100), ("address", 200), ("tax_id", 40), ("contract_no", 60), ("notes", 300)):
        setattr(ins, f, request.POST.get(f, "").strip()[:n])
    ins.active = request.POST.get("active", "1") == "1"
    ins.save()
    if ins.partner_id:
        ins.partner.name = f"تأمين: {ins.name}"[:150]
        ins.partner.save(update_fields=["name"])
    return ins


@guard
def insurance(request):
    if not (V.is_owner(request.user) or request.user.has_perm("treasury.view_voucher")):
        raise PermissionDenied
    if request.method == "POST":
        _owner(request)
        try:
            ins = _ins_from_post(request, InsuranceCompany())
            messages.success(request, f"تمت إضافة «{ins.name}» ✔")
        except ValidationError as e:
            _errs(request, e)
        return redirect("clinic_insurance")
    rows = []
    for ins in InsuranceCompany.objects.all():
        st = C.insurance_statement(ins)
        rows.append(dict(ins=ins, billed=st["billed"], owed=st["owed"], patients=ins.patients.count()))
    return render(request, "clinic/insurance.html", _ctx(rows=rows, owner=V.is_owner(request.user)))


@guard
def insurance_detail(request, pk):
    if not (V.is_owner(request.user) or request.user.has_perm("treasury.view_voucher")):
        raise PermissionDenied
    ins = get_object_or_404(InsuranceCompany, pk=pk)
    if request.method == "POST":
        _owner(request)
        a = request.POST.get("action")
        try:
            if a == "save":
                _ins_from_post(request, ins)
                messages.success(request, "تم الحفظ ✔")
            elif a == "collect":
                box = get_object_or_404(CashBox, pk=request.POST.get("box"), active=True)
                C.collect(C.insurer_customer(ins), _dec(request.POST.get("amount"), Decimal(0)), box, request.user,
                          note=f"من {ins.name} {request.POST.get('ref', '')}".strip())
                messages.success(request, f"حُصّل {request.POST.get('amount')} ج من {ins.name} ✔")
        except ValidationError as e:
            _errs(request, e)
        return redirect("clinic_insurance_detail", ins.pk)
    d1, d2 = C.parse_date(request.GET.get("d1")), C.parse_date(request.GET.get("d2"))
    if not request.GET.get("d1"):
        d1 = timezone.localdate().replace(day=1)
    st = C.insurance_statement(ins, d1, d2)
    return render(request, "clinic/insurance_detail.html", _ctx(
        ins=ins, st=st, d1=d1, d2=d2, boxes=CashBox.objects.filter(active=True).order_by("order", "pk"), owner=V.is_owner(request.user),
        printing=bool(request.GET.get("print"))))
