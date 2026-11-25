"""1.25 owner pages: a credit customer's statement (bills, instalments, due dates, FIFO, WhatsApp), «الأقساط والمستحقات»
(everything due this week / late, with WhatsApp reminders), and «منصات التوصيل» (Talabat / Elmenus: settings + report)."""
from core import currency as _CUR  # 1.26: the shop's currency (display only)
import datetime as dt

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from core.models import CompanySettings
from ledger.models import Partner
from pos.credit import platforms as PF
from pos.credit import services as CR
from pos.credit.models import Plan, PlatformPayout
from treasury.models import CashBox

from .shop import _is_manager, _need_cash


def _owner(user):
    from pos.voids import is_owner

    return user.is_superuser or is_owner(user)


def _can_plan(user):
    return _owner(user) or _is_manager(user) or user.has_perm("ledger.change_partner")


def _errs(request, e):
    for m in getattr(e, "messages", [str(e)]):
        messages.error(request, m)


def _date(v, default=None):
    try:
        return dt.date.fromisoformat(str(v))
    except (TypeError, ValueError):
        return default


@login_required
def customer(request, pk):
    _need_cash(request)
    p = get_object_or_404(Partner, pk=pk)
    if p.kind != "customer" and not hasattr(p, "patient") and not hasattr(p, "insurance_company"):
        raise Http404("ليس عميلًا آجلًا")
    s = CompanySettings.get()
    today = timezone.localdate()
    if request.method == "POST":
        op = request.POST.get("op")
        try:
            if op == "remind":  # the WhatsApp link opens in a new tab; the program remembers the reminder
                CR.mark_reminded(p, request.user)
                return redirect(request.POST.get("wa") if str(request.POST.get("wa", "")).startswith("https://wa.me/") else request.path)
            if not _can_plan(request.user):
                raise PermissionDenied
            if op == "plan":
                tgt = request.POST.get("target", "all")
                order = entry = None
                if tgt.startswith("order:"):
                    from pos.models import Order

                    order = Order.objects.filter(pk=tgt[6:] if tgt[6:].isdigit() else 0, customer=p).first()
                    if not order:
                        raise ValidationError("الفاتورة غير موجودة")
                elif tgt.startswith("entry:"):
                    from ledger.models import JournalEntry

                    entry = JournalEntry.objects.filter(pk=tgt[6:] if tgt[6:].isdigit() else 0).first()
                d = CR.dues(p, today, s)
                amount = request.POST.get("amount", "").strip()
                if not amount:
                    if order or entry:
                        row = next((r for r in d["rows"] if (order and r["order"] == order) or (entry and r["entry"] == entry)), None)
                        amount = row["remaining"] if row else 0
                    else:
                        amount = d["owed"]
                CR.make_plan(p, amount, request.POST.get("count"), request.POST.get("first") or CR.add_period(today, request.POST.get("every", "month"), 1),
                             every=request.POST.get("every", "month"), order=order, entry=entry, whole=not (order or entry),
                             note=request.POST.get("note", ""), user=request.user, today=today)
                messages.success(request, "حُفظت خطة التقسيط ✔")
            elif op == "cancel_plan":
                pl = get_object_or_404(Plan, pk=request.POST.get("plan") or 0, partner=p)
                CR.cancel_plan(pl, request.user)
                messages.success(request, "أُلغيت خطة التقسيط — تعود الفاتورة إلى موعد سدادها العادي")
            elif op == "terms":
                CR.set_terms(p, request.POST.get("days"))
                messages.success(request, "حُفظت مدة السداد ✔")
        except ValidationError as e:
            _errs(request, e)
        return redirect(request.path)
    d = CR.dues(p, today, s)
    text = CR.statement_text(p, d, s, today)
    first_late = next((r for r in d["rows"] if r["remaining"] > 0 and r["due"] <= today), None) or d["next"]
    moves = []
    run = 0
    from django.db.models import Sum  # noqa: F401

    for ln in CR._live_lines(p).order_by("entry__date", "entry_id", "id"):
        run += ln.debit - ln.credit
        moves.append(dict(date=ln.entry.date, number=ln.entry.number, desc=ln.description or ln.entry.description, debit=ln.debit,
                          credit=ln.credit, bal=run))
    open_rows = [r for r in d["rows"] if r["remaining"] > 0 and r["kind"] in ("sale", "old")]
    t = getattr(p, "credit_terms", None)
    return render(request, "ui/credit_customer.html", dict(
        p=p, d=d, today=today, moves=moves[-300:], wa=CR.wa_link(p.phone, text), text=text, has_phone=bool(CR.wa_number(p.phone)),
        remind_wa=CR.wa_link(p.phone, CR.reminder_text(p, first_late, d["owed"], s)) if first_late else "",
        can_plan=_can_plan(request.user), open_rows=open_rows, terms=t, default_days=CR.cfg(s)["days"],
        first_default=CR.add_period(today, "month", 1), payments=CR.payments(p, 10), limit=p.credit_limit,
        print_mode=request.GET.get("print") == "1", s=s))


@login_required
def dues(request):
    _need_cash(request)
    s = CompanySettings.get()
    today = timezone.localdate()
    if request.method == "POST":
        op = request.POST.get("op")
        try:
            if op == "remind":
                p = get_object_or_404(Partner, pk=request.POST.get("partner") or 0)
                CR.mark_reminded(p, request.user)
                wa = request.POST.get("wa", "")
                return redirect(wa if wa.startswith("https://wa.me/") else request.path)
            if op == "cfg":
                if not _owner(request.user):
                    raise PermissionDenied
                CR.set_cfg(days=request.POST.get("days"), block_late=request.POST.get("block_late") == "1")
                messages.success(request, "حُفظت مدة السداد العامة ✔")
        except ValidationError as e:
            _errs(request, e)
        return redirect(request.get_full_path())
    show = request.GET.get("show", "week")
    horizon = {"late": -1, "today": 0, "week": 7, "month": 31}.get(show, 7)
    rows = CR.due_list(today, max(horizon, 0))
    if show == "late":
        rows = [r for r in rows if r["due"] < today]
    late = [r for r in rows if r["due"] < today]
    for r in rows:
        r["wa"] = CR.wa_link(r["partner"].phone, CR.reminder_text(r["partner"], r, r["owed"], s))
        r["has_phone"] = bool(CR.wa_number(r["partner"].phone))
        r["terms"] = getattr(r["partner"], "credit_terms", None)
    all_rows = CR.due_list(today, 7) if show != "week" else rows
    return render(request, "ui/credit_dues.html", dict(
        rows=rows, show=show, today=today, cfg=CR.cfg(s), owner=_owner(request.user),
        late_total=sum((r["remaining"] for r in all_rows if r["due"] < today), 0),
        late_people=len({r["partner"].pk for r in all_rows if r["due"] < today}),
        week_total=sum((r["remaining"] for r in all_rows if today <= r["due"] <= today + dt.timedelta(days=7)), 0),
        today_total=sum((r["remaining"] for r in all_rows if r["due"] == today), 0), n_late=len(late)))


@login_required
def platforms(request):
    s = CompanySettings.get()
    if not PF.available(s):
        raise Http404("منصات التوصيل غير متاحة لهذا النشاط")
    _need_cash(request)
    owner = _owner(request.user)
    if request.method == "POST":
        op = request.POST.get("op")
        try:
            if op == "cfg":
                if not owner:
                    raise PermissionDenied
                PF.set_cfg({k: dict(on=request.POST.get(f"{k}_on") == "1", link=request.POST.get(f"{k}_link", ""), pct=request.POST.get(f"{k}_pct", "0"))
                            for k in PF.available(s)})
                messages.success(request, "تم الحفظ ✔ — تظهر المنصات المفعّلة في شاشة الكاشير ضمن طرق الدفع بعد تحديث الصفحة")
            elif op == "payout":
                box = get_object_or_404(CashBox, pk=request.POST.get("box") or 0, active=True)
                pay = PF.settle(request.POST.get("platform", ""), request.POST.get("amount"), box, other=request.POST.get("other") or 0,
                                date=_date(request.POST.get("date"), timezone.localdate()), note=request.POST.get("note", ""), user=request.user)
                messages.success(request, f"سُجّل تحويل {PF.PLATFORMS[pay.platform]['name']} {_CUR.fmt(pay.amount)} إلى {box.name} ✔")
            elif op == "cancel_payout":
                if not owner:
                    raise PermissionDenied
                PF.cancel_payout(get_object_or_404(PlatformPayout, pk=request.POST.get("pk") or 0), request.POST.get("reason", ""), request.user)
                messages.success(request, "أُلغي التحويل بقيد عكسي")
        except ValidationError as e:
            _errs(request, e)
        return redirect(request.get_full_path())
    today = timezone.localdate()
    start = _date(request.GET.get("from"), today.replace(day=1))
    end = _date(request.GET.get("to"), today)
    return render(request, "ui/platforms.html", dict(
        cfg=PF.cfg(s), rows=PF.report(start, end, s), start=start, end=end, owner=owner, today=today,
        boxes=CashBox.objects.filter(active=True).order_by("order", "id"),
        payouts=PlatformPayout.objects.select_related("box").order_by("-date", "-id")[:50], names={k: v["name"] for k, v in PF.PLATFORMS.items()}))


def statement_url(partner):
    return reverse("credit_customer", args=[partner.pk])
