"""Credit customers (1.25): what each customer owes, bill by bill, when each part falls due, what is late.

Every credit sale (any posted entry that raised the customer's balance — the till, the clinic, an opening balance) is a
«charge». A charge is due once, `days` after its date (the customer's own period or the shop's), unless it has an instalment
plan: then its instalments are the dues. A plan over the customer's whole balance (no invoice) replaces every charge up to
its day. What the customer still owes sits on the latest dues; everything older counts as paid (FIFO), so payments,
returns and zeroing never need to be matched by hand. A cancelled sale (reversed entry) is no charge.
"""
from core import currency as _CUR  # 1.26: the shop's currency (display only)
import datetime as dt
from decimal import Decimal
from urllib.parse import quote

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from core.models import CompanySettings
from ledger import services as L
from ledger.models import JournalLine, Partner

from .models import CustomerTerms, Instalment, Plan

CUSTOMERS = "1203010101"
Z = Decimal(0)
DEFAULT_DAYS = 30
MAX_COUNT = 36
DAY_NAMES = {5: "السبت", 6: "الأحد", 0: "الاثنين", 1: "الثلاثاء", 2: "الأربعاء", 3: "الخميس", 4: "الجمعة"}


# ---------------------------------------------------------------- settings
def cfg(s=None):
    s = s or CompanySettings.get()
    c = dict((s.menu_cfg or {}).get("credit") or {})
    try:
        days = int(c.get("days", DEFAULT_DAYS))
    except (TypeError, ValueError):
        days = DEFAULT_DAYS
    return dict(days=max(0, min(days, 365)), block_late=bool(c.get("block_late")))


def set_cfg(days=None, block_late=None):
    s = CompanySettings.get()
    m = dict(s.menu_cfg or {})
    c = dict(m.get("credit") or {})
    if days is not None:
        d = str(days).strip()
        if not d.isdigit() or int(d) > 365:
            raise ValidationError("مدة السداد من 0 إلى 365 يومًا")
        c["days"] = int(d)
    if block_late is not None:
        c["block_late"] = bool(block_late)
    m["credit"] = c
    s.menu_cfg = m
    s.save(update_fields=["menu_cfg"])
    return cfg(s)


def terms_days(partner, s=None):
    t = CustomerTerms.objects.filter(partner=partner).first()
    return t.days if t and t.days is not None else cfg(s)["days"]


def set_terms(partner, days):
    t, _ = CustomerTerms.objects.get_or_create(partner=partner)
    d = str(days if days is not None else "").strip()
    if d and (not d.isdigit() or int(d) > 365):
        raise ValidationError("مدة السداد من 0 إلى 365 يومًا")
    t.days = int(d) if d else None
    t.save(update_fields=["days"])
    return t


# ---------------------------------------------------------------- dates
def add_period(day, every, n):
    if every == "week":
        return day + dt.timedelta(days=7 * n)
    if every == "2weeks":
        return day + dt.timedelta(days=14 * n)
    m = day.month - 1 + n
    y, m = day.year + m // 12, m % 12 + 1
    import calendar

    return dt.date(y, m, min(day.day, calendar.monthrange(y, m)[1]))


def split(amount, count):
    """Equal instalments in whole pounds; the last one takes the rest (1000 / 3 → 333, 333, 334)."""
    amount = L.q2(Decimal(str(amount)))
    if count <= 1:
        return [amount]
    each = (amount / count).quantize(Decimal("1"), rounding="ROUND_DOWN")
    if each <= 0:
        each = L.q2(amount / count)
    rows = [each] * (count - 1)
    rows.append(amount - sum(rows, Z))
    return rows


# ---------------------------------------------------------------- balance and charges
def balance(partner):
    r = JournalLine.objects.filter(account__code=CUSTOMERS, partner=partner, entry__state__in=["posted", "reversed"]).aggregate(
        d=Sum("debit"), c=Sum("credit"))
    return L.q2((r["d"] or Z) - (r["c"] or Z))


def _live_lines(partner):
    """The customer's lines without cancelled pairs (a reversed entry and its reversal)."""
    return (JournalLine.objects.filter(account__code=CUSTOMERS, partner=partner, entry__state="posted", entry__reversal_of__isnull=True)
            .select_related("entry"))


def _order_of(entry):
    from pos.models import Order

    src = entry.source or ""
    if src.startswith("pos.order:") and src[10:].isdigit():
        return Order.objects.filter(pk=int(src[10:])).first()
    return None


def charges(partner):
    """Entries that raised the customer's balance, oldest first: [dict(entry, date, amount, order, ref)]."""
    by = {}
    for ln in _live_lines(partner):
        by.setdefault(ln.entry_id, [ln.entry, Z])[1] += ln.debit - ln.credit
    out = []
    for e, net in sorted(by.values(), key=lambda x: (x[0].date, x[0].pk)):
        if net > 0:
            o = _order_of(e)
            ref = (f"فاتورة {o.shift_seq or ''}".strip() + f" ({o.uid})") if o else (e.description or e.number)
            out.append(dict(entry=e, date=e.date, amount=L.q2(net), order=o, ref=ref[:120]))
    return out


def payments(partner, limit=None):
    """Money in / returns that lowered the balance, newest first: [dict(date, amount, desc)]."""
    by = {}
    for ln in _live_lines(partner):
        by.setdefault(ln.entry_id, [ln.entry, Z])[1] += ln.credit - ln.debit
    rows = [dict(date=e.date, amount=L.q2(v), desc=e.description or e.number, entry=e) for e, v in by.values() if v > 0]
    rows.sort(key=lambda r: (r["date"], r["entry"].pk), reverse=True)
    return rows[:limit] if limit else rows


def live_plans(partner):
    return list(Plan.objects.filter(partner=partner, cancelled=False).prefetch_related("rows").order_by("created_at", "id"))


def dues(partner, today=None, s=None):
    """Every due of the customer with what is still owed on it (FIFO), sorted by due date.
    → dict(rows, owed, late, late_days, next, plans) — rows: dict(due, amount, remaining, paid, ref, kind, no, count, plan, date,
    status, late_days, order, entry)."""
    today = today or timezone.localdate()
    days = terms_days(partner, s)
    plans = live_plans(partner)
    by_order = {p.order_id: p for p in plans if p.order_id}
    by_entry = {p.entry_id: p for p in plans if p.entry_id and not p.order_id}
    whole = [p for p in plans if not p.order_id and not p.entry_id]
    rows = []
    used = set()
    chs = charges(partner)
    for c in chs:
        p = by_order.get(c["order"].pk if c["order"] else None) or by_entry.get(c["entry"].pk)
        if p is None:  # a plan over the whole balance covers every charge up to its day
            p = next((w for w in whole if w.covers_until and c["date"] <= w.covers_until), None)
            if p is not None:
                continue
        if p is not None:
            continue
        rows.append(dict(due=c["date"] + dt.timedelta(days=days), date=c["date"], amount=c["amount"], ref=c["ref"], kind="sale", no=0, count=0,
                         plan=None, order=c["order"], entry=c["entry"]))
    live_entries = {c["entry"].pk for c in chs}
    live_orders = {c["order"].pk for c in chs if c["order"]}
    for p in plans:
        if p.order_id and p.order_id not in live_orders:
            continue  # its sale was cancelled
        if p.entry_id and not p.order_id and p.entry_id not in live_entries:
            continue
        used.add(p.pk)
        for r in p.rows.all():
            start = p.order.created_at_device.date() if p.order_id else (p.entry.date if p.entry_id else (p.created_at.date() if p.created_at else r.due))
            rows.append(dict(due=r.due, date=start, amount=r.amount, ref=p.ref or "تقسيط",
                             kind="inst", no=r.no, count=p.count, plan=p, order=p.order, entry=p.entry, row=r))
    owed = balance(partner)
    explained = sum((r["amount"] for r in rows), Z)
    if owed > explained:  # an old balance the books do not explain bill by bill (an import, a manual entry)
        first = min([r["date"] for r in rows] + [today])
        rows.append(dict(due=first, date=first, amount=L.q2(owed - explained), ref="رصيد سابق", kind="old", no=0, count=0, plan=None,
                         order=None, entry=None))
    rows.sort(key=lambda r: (r["due"], r["date"], r["no"]))
    left = max(owed, Z)
    for r in reversed(rows):
        r["remaining"] = min(r["amount"], left)
        left -= r["remaining"]
    late = Z
    worst = 0
    nxt = None
    for r in rows:
        r["paid"] = r["amount"] - r["remaining"]
        r["late_days"] = (today - r["due"]).days if r["remaining"] > 0 and r["due"] < today else 0
        if r["remaining"] <= 0:
            r["status"], r["cls"] = "مسدَّد", "ok"
        elif r["due"] < today:
            r["status"], r["cls"] = f"متأخر {r['late_days']} يوم", "late"
            late += r["remaining"]
            worst = max(worst, r["late_days"])
        elif r["due"] == today:
            r["status"], r["cls"] = "مستحق اليوم", "today"
        else:
            r["status"], r["cls"] = ("مسدَّد جزئيًا — " if r["paid"] > 0 else "") + "لم يحن موعده", "soon"
        if r["remaining"] > 0 and r["due"] >= today and nxt is None:
            nxt = r
    return dict(rows=rows, owed=owed, late=late, late_days=worst, next=nxt, plans=[p for p in plans if p.pk in used], days=days)


def late_amount(partner, today=None):
    if balance(partner) <= 0:
        return Z
    return dues(partner, today)["late"]


def customers_with_balance():
    bal = {r["partner"]: (r["d"] or Z) - (r["c"] or Z) for r in JournalLine.objects.filter(
        account__code=CUSTOMERS, entry__state__in=["posted", "reversed"], partner__isnull=False).values("partner").annotate(d=Sum("debit"), c=Sum("credit"))}
    ids = [pk for pk, b in bal.items() if b > 0]
    return [(p, bal[p.pk]) for p in Partner.objects.filter(pk__in=ids).order_by("name")]


def due_list(today=None, horizon=7):
    """«الأقساط المستحقة»: every due still owed up to `horizon` days ahead, the latest first in danger (most late first)."""
    today = today or timezone.localdate()
    s = CompanySettings.get()
    out = []
    for p, b in customers_with_balance():
        d = dues(p, today, s)
        for r in d["rows"]:
            if r["remaining"] > 0 and r["due"] <= today + dt.timedelta(days=horizon):
                out.append(dict(r, partner=p, owed=d["owed"], late_total=d["late"]))
    out.sort(key=lambda r: (r["due"], r["partner"].name))
    return out


# ---------------------------------------------------------------- plans
def _who(user):
    if not user or isinstance(user, str):
        return str(user or "")[:100]
    return (user.get_full_name() or user.username)[:100]


@transaction.atomic
def make_plan(partner, amount, count, first_due, *, every="month", order=None, entry=None, whole=False, note="", user=None, today=None):
    today = today or timezone.localdate()
    try:
        count = int(count)
    except (TypeError, ValueError):
        raise ValidationError("عدد الأقساط غير صحيح")
    if not 2 <= count <= MAX_COUNT:
        raise ValidationError(f"عدد الأقساط من 2 إلى {MAX_COUNT}")
    if every not in dict(Plan.EVERY):
        raise ValidationError("الدورية غير صحيحة")
    amount = L.q2(Decimal(str(amount or 0)))
    if amount <= 0:
        raise ValidationError("لا يوجد مبلغ للتقسيط")
    if not isinstance(first_due, dt.date):
        try:
            first_due = dt.date.fromisoformat(str(first_due))
        except ValueError:
            raise ValidationError("تاريخ أول قسط غير صحيح")
    if order is not None:
        Plan.objects.filter(order=order, cancelled=False).update(cancelled=True)  # a new plan for the same bill replaces the old
        ref = f"تقسيط فاتورة {order.shift_seq or ''} ({order.uid})".replace("  ", " ")
    elif entry is not None:
        Plan.objects.filter(entry=entry, order__isnull=True, cancelled=False).update(cancelled=True)
        ref = f"تقسيط {entry.description or entry.number}"
    else:
        ref = f"تقسيط الرصيد حتى {today:%d/%m/%Y}"
    p = Plan.objects.create(partner=partner, order=order, entry=entry if order is None else None, amount=amount, count=count, every=every,
                            first_due=first_due, covers_until=today if whole else None, note=(note or "")[:200], by=_who(user), ref=ref[:80])
    Instalment.objects.bulk_create([Instalment(plan=p, no=i + 1, due=add_period(first_due, every, i), amount=a)
                                    for i, a in enumerate(split(amount, count))])
    return p


def plan_from_till(order, info):
    """A credit sale rung with «تقسيط على N أقساط» at the till: monthly, the first one month after the sale."""
    if not order.customer_id or order.paid_credit <= 0 or order.is_return or not isinstance(info, dict):
        return None
    try:
        n = int(info.get("n") or 0)
    except (TypeError, ValueError):
        return None
    if n < 2:
        return None
    every = info.get("every") if info.get("every") in dict(Plan.EVERY) else "month"
    day = order.created_at_device.date()
    return make_plan(order.customer, order.paid_credit, min(n, MAX_COUNT), add_period(day, every, 1), every=every, order=order,
                     user=str(info.get("by") or "الكاشير"), today=day)


def cancel_plan(plan, user=None):
    plan.cancelled = True
    plan.note = (f"أُلغيت بواسطة {_who(user)}؛ " + plan.note)[:200]
    plan.save(update_fields=["cancelled", "note"])


# ---------------------------------------------------------------- WhatsApp texts
def wa_number(phone):
    from pos.clinic.followup import wa_number as w

    return w(phone)


def wa_link(phone, text):
    n = wa_number(phone)
    return f"https://wa.me/{n}?text={quote(text)}" if n else f"https://wa.me/?text={quote(text)}"


def money(x):
    return f"{L.q2(Decimal(str(x))):,.2f}"


def statement_text(partner, d=None, s=None, today=None):
    s = s or CompanySettings.get()
    today = today or timezone.localdate()
    d = d or dues(partner, today, s)
    lines = [f"السيد/ة {partner.name}، تحية طيبة من {s.name}.", f"كشف حسابكم حتى {today:%d/%m/%Y}:"]
    if d["owed"] <= 0:
        lines.append("لا توجد مستحقات عليكم — شكرًا لتعاملكم معنا." if d["owed"] == 0 else f"رصيد لكم لدينا: {_CUR.fmt(money(-d['owed']))}")
    else:
        lines.append(f"إجمالي المستحق: {_CUR.fmt(money(d['owed']))}")
        if d["late"] > 0:
            lines.append(f"منها متأخر السداد: {_CUR.fmt(money(d['late']))}")
        open_rows = [r for r in d["rows"] if r["remaining"] > 0]
        for r in open_rows[:8]:
            what = f"القسط {r['no']} من {r['count']}" if r["kind"] == "inst" else r["ref"]
            lines.append(f"• {what}: {_CUR.fmt(money(r['remaining']))} — يستحق {r['due']:%d/%m/%Y}" + (f" (متأخر {r['late_days']} يوم)" if r["late_days"] else ""))
        if len(open_rows) > 8:
            lines.append(f"• و{len(open_rows) - 8} مستحقات أخرى")
    last = payments(partner, 1)
    if last:
        lines.append(f"آخر سداد: {_CUR.fmt(money(last[0]['amount']))} بتاريخ {last[0]['date']:%d/%m/%Y}")
    if s.phone:
        lines.append(f"للاستفسار: {s.phone}")
    lines.append("مع خالص الشكر.")
    return "\n".join(lines)


def reminder_text(partner, row, owed, s=None):
    s = s or CompanySettings.get()
    what = f"القسط رقم {row['no']} من {row['count']}" if row["kind"] == "inst" else ("الرصيد السابق" if row["kind"] == "old" else row["ref"])
    when = f"المستحق يوم {DAY_NAMES[row['due'].weekday()]} {row['due']:%d/%m/%Y}"
    late = f"، وقد تأخر سداده {row['late_days']} يوم" if row["late_days"] else ""
    tail = f" للاستفسار: {s.phone}." if s.phone else ""
    return (f"السيد/ة {partner.name}، تحية طيبة من {s.name}. نذكّركم بسداد {what} بمبلغ {_CUR.fmt(money(row['remaining']))} {when}{late}. "
            f"إجمالي المستحق عليكم {_CUR.fmt(money(owed))}.{tail} مع خالص الشكر.")


def mark_reminded(partner, user=None):
    t, _ = CustomerTerms.objects.get_or_create(partner=partner)
    t.reminded_at, t.reminded_by = timezone.now(), _who(user)
    t.save(update_fields=["reminded_at", "reminded_by"])
    return t
