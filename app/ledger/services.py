"""Posting engine. Every financial document in the system ends up here.

Rules enforced (constitution II, III, IV, V, VI, VII):
- at least two lines; each line is debit XOR credit, non-negative, 2 decimals
- total debit == total credit (in EGP)
- account is a leaf, active; partner present on partner-control accounts
- currency allowed on the account; foreign lines carry amount and rate consistent with EGP value
- dimension policies (required) and dimension values postable / active / in the right dimension
- cost center's project not closed
- accounting period open
- number assigned at posting from a gap-free sequence
"""
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core import audit
from core.middleware import current_user

from .models import ZERO, Journal, JournalEntry, JournalLine, Period, Sequence

CENT = Decimal("0.01")


def q2(x):
    return Decimal(x).quantize(CENT, rounding=ROUND_HALF_UP)


def next_number(journal: Journal, date):
    year = date.year if journal.yearly_reset else 0
    seq, _ = Sequence.objects.select_for_update().get_or_create(journal=journal, year=year, defaults={"last": journal.start_after})
    seq.last += 1
    seq.save(update_fields=["last"])
    num = str(seq.last).zfill(journal.padding)
    return f"{journal.prefix}{date.year}-{num}" if journal.yearly_reset else f"{journal.prefix}{num}"


def get_period(date, create=True):
    p = Period.objects.filter(year=date.year, month=date.month).first()
    if p is None and create:
        p = Period.objects.create(year=date.year, month=date.month)
    return p


def check_period_open(date):
    p = get_period(date)
    if p.status != "open":
        raise ValidationError(f"الفترة {date:%Y-%m} مقفولة — لا يمكن التسجيل فيها")


def validate_lines(entry: JournalEntry, lines):
    errors = []
    if len(lines) < 2:
        errors.append("يجب أن يحتوي القيد على سطرين على الأقل")
    total_d = total_c = ZERO
    for i, ln in enumerate(lines, 1):
        acc = ln.account
        tag = f"سطر {i} ({acc.code})"
        if ln.debit < 0 or ln.credit < 0:
            errors.append(f"{tag}: مبالغ سالبة غير مسموحة")
        if (ln.debit > 0) == (ln.credit > 0):
            errors.append(f"{tag}: يجب أن يكون السطر مدينًا أو دائنًا فقط")
        if ln.debit != q2(ln.debit) or ln.credit != q2(ln.credit):
            errors.append(f"{tag}: يجب أن تكون المبالغ برقمين عشريين")
        total_d += ln.debit
        total_c += ln.credit
        if not acc.is_postable:
            errors.append(f"{tag}: الحساب رئيسي ولا يقبل قيود")
        if not acc.active:
            errors.append(f"{tag}: الحساب موقوف")
        if acc.is_partner_control and not ln.partner_id:
            errors.append(f"{tag}: الحساب يتطلب تحديد الطرف")
        if ln.currency not in acc.currency_list:
            errors.append(f"{tag}: العملة {ln.currency} غير مسموحة على الحساب ({acc.currencies})")
        if ln.currency != "EGP":
            if ln.amount_currency is None or not ln.rate:
                errors.append(f"{tag}: السطر بعملة أجنبية يجب أن يتضمن المبلغ بالعملة وسعر الصرف")
            elif abs(q2(abs(ln.amount_currency) * ln.rate) - (ln.debit + ln.credit)) > CENT:
                errors.append(f"{tag}: المبلغ بالجنيه لا يساوي المبلغ بالعملة × السعر")
        for n in (1, 2, 3):
            policy = getattr(acc, f"dim{n}_policy")
            val = getattr(ln, f"dim{n}")
            if policy == "required" and val is None:
                label = {1: "مركز التكلفة", 2: "المعدة", 3: "البعد الثالث"}[n]
                errors.append(f"{tag}: {label} إجباري على هذا الحساب")
            if val is not None:
                if val.dimension.number != n:
                    errors.append(f"{tag}: القيمة {val} ليست من البعد {n}")
                if not val.is_postable:
                    errors.append(f"{tag}: {val} مستوى تجميعي ولا يقبل تسجيل")
                if not val.active:
                    errors.append(f"{tag}: {val} موقوف")
                if n == 1:
                    proj = val.project()
                    if proj and proj.project_status == "closed" and not getattr(entry, "_allow_closed_project", False):
                        errors.append(f"{tag}: المشروع {proj.name} مقفول")
    if q2(total_d) != q2(total_c):
        errors.append(f"القيد غير متوازن: مدين {total_d} / دائن {total_c}")
    if q2(total_d) == ZERO:
        errors.append("القيد بدون مبالغ")
    if errors:
        raise ValidationError(errors)


@transaction.atomic
def post(entry: JournalEntry, user=None):
    entry = JournalEntry.objects.select_for_update().get(pk=entry.pk)
    if entry.state != "draft":
        raise ValidationError("القيد مرحّل بالفعل")
    lines = list(entry.lines.select_related("account", "dim1", "dim2", "dim3", "dim1__dimension", "dim2__dimension", "dim3__dimension"))
    check_period_open(entry.date)
    validate_lines(entry, lines)
    entry.number = entry.number or next_number(entry.journal, entry.date)
    entry.state = "posted"
    entry.posted_at = timezone.now()
    entry.posted_by = user or current_user()
    entry._allow_posted_save = True
    entry.save()
    entry._allow_posted_save = False
    audit.log("post", entry, summary=f"ترحيل {entry.number}: {entry.description}")
    return entry


@transaction.atomic
def create_entry(journal_code, date, description, lines, source="", legacy_ref="", post_now=True, user=None, number=None):
    """lines: list of dicts with account, debit/credit, and optional partner, dim1..3, currency, amount_currency, rate, description, quantity."""
    journal = Journal.objects.get(code=journal_code)
    entry = JournalEntry.objects.create(
        journal=journal, date=date, description=description[:300], source=source, legacy_ref=legacy_ref,
        created_by=user or current_user(), number=number or "",
    )
    for ln in lines:
        d = dict(ln)
        d.setdefault("debit", ZERO)
        d.setdefault("credit", ZERO)
        d["debit"] = q2(d["debit"] or 0)
        d["credit"] = q2(d["credit"] or 0)
        if d["debit"] == ZERO and d["credit"] == ZERO:
            continue
        JournalLine.objects.create(entry=entry, **d)
    if post_now:
        entry = post(entry, user=user)
    return entry


def _post_allowing_closed_project(entry, user):
    entry = JournalEntry.objects.get(pk=entry.pk)
    lines = list(entry.lines.all())
    check_period_open(entry.date)
    entry._allow_closed_project = True
    validate_lines(entry, lines)
    entry.number = next_number(entry.journal, entry.date)
    entry.state = "posted"
    entry.posted_at = timezone.now()
    entry.posted_by = user or current_user()
    entry._allow_posted_save = True
    entry.save()
    entry._allow_posted_save = False
    audit.log("post", entry, summary=f"ترحيل {entry.number}: {entry.description}")
    return entry


@transaction.atomic
def reverse(entry: JournalEntry, date=None, reason="", user=None):
    entry = JournalEntry.objects.select_for_update().get(pk=entry.pk)
    if entry.state != "posted":
        raise ValidationError("لا يمكن عكس إلا قيد مرحّل")
    date = date or entry.date
    rev = JournalEntry.objects.create(
        journal=entry.journal, date=date, description=f"عكس {entry.number}: {reason}"[:300],
        reversal_of=entry, source=entry.source, created_by=user or current_user(),
    )
    for ln in entry.lines.all():
        JournalLine.objects.create(
            entry=rev, account=ln.account, partner=ln.partner, description=ln.description,
            debit=ln.credit, credit=ln.debit, currency=ln.currency,
            amount_currency=(-ln.amount_currency if ln.amount_currency is not None else None), rate=ln.rate,
            dim1=ln.dim1, dim2=ln.dim2, dim3=ln.dim3, quantity=(-ln.quantity if ln.quantity else None),
        )
    rev = _post_allowing_closed_project(rev, user)
    entry.state = "reversed"
    entry._allow_posted_save = True
    entry.save(update_fields=["state"])
    entry._allow_posted_save = False
    audit.log("reverse", entry, summary=f"عكس {entry.number} بالقيد {rev.number}: {reason}")
    return rev


def split_line(base: dict, dim_number: int, shares):
    """Distribution: split one line across several values of a dimension.

    shares: list of (DimensionValue, Decimal percent). Percent must total 100; rounding goes to the last share.
    """
    total_pct = sum((Decimal(p) for _, p in shares), ZERO)
    if abs(total_pct - 100) > Decimal("0.0001"):
        raise ValidationError(f"يجب أن يكون مجموع نسب التوزيع 100% (المجموع {total_pct})")
    amount_field = "debit" if q2(base.get("debit") or 0) > 0 else "credit"
    amount = q2(base[amount_field])
    qty = base.get("quantity")
    out, used, used_q = [], ZERO, Decimal("0")
    for i, (val, pct) in enumerate(shares):
        d = dict(base)
        if i == len(shares) - 1:
            part = amount - used
            part_q = (qty - used_q) if qty is not None else None
        else:
            part = q2(amount * Decimal(pct) / 100)
            part_q = (Decimal(qty) * Decimal(pct) / 100).quantize(Decimal("0.001")) if qty is not None else None
        used += part
        if part_q is not None:
            used_q += part_q
        d[amount_field] = part
        d[f"dim{dim_number}"] = val
        if qty is not None:
            d["quantity"] = part_q
        out.append(d)
    return out


@transaction.atomic
def close_period(year, month, user=None):
    p = get_period(__import__("datetime").date(year, month, 1))
    if JournalEntry.objects.filter(state="draft", date__year=year, date__month=month).exists():
        raise ValidationError("توجد قيود مسودة في الفترة — رحّلها أو احذفها قبل القفل")
    p.status = "closed"
    p.closed_at = timezone.now()
    p.closed_by = user or current_user()
    p.save()
    audit.log("close_period", p, summary=f"قفل الفترة {year}-{month:02d}")
    return p


@transaction.atomic
def reopen_period(year, month, user=None, reason=""):
    p = get_period(__import__("datetime").date(year, month, 1))
    p.status = "open"
    p.save()
    audit.log("reopen_period", p, summary=f"إعادة فتح {year}-{month:02d}: {reason}")
    return p


@transaction.atomic
def close_project(project, date, user=None):
    """Close a project (top-level cost center): blocks new postings. The final cost report is in reports."""
    if not project.is_project:
        raise ValidationError("هذا ليس مشروعًا")
    project.project_status = "closed"
    project.closed_at = date
    project.save()
    audit.log("close_project", project, summary=f"قفل المشروع {project.name}")
    return project
