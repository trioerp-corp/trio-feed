"""Company setup wizard: Excel templates → upload → validation preview → confirmed import.

Every step is idempotent (update by code), so a corrected file can be uploaded again.
"""
import csv
import datetime as dt
import io
from decimal import Decimal, InvalidOperation

import openpyxl
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import redirect, render

from inventory.models import Item, ItemCategory
from ledger import services as L
from ledger.models import Account, Dimension, DimensionValue, Partner
from treasury.models import CashBox

YES = {"yes", "y", "1", "true", "نعم", "اه", "أيوه"}


def D(v):
    try:
        return Decimal(str(v).replace(",", "")).quantize(Decimal("0.01")) if v not in (None, "") else Decimal("0")
    except InvalidOperation:
        raise ValueError(f"رقم غير صالح: {v}")


def _accounts(rows, check_only):
    errs, by = [], {a.code: a for a in Account.objects.all()}
    codes = {str(r["code"]) for r in rows}
    for i, r in enumerate(rows, 2):
        p = str(r.get("parent_code") or "")
        if p and p not in codes and p not in by:
            errs.append(f"سطر {i}: الحساب الرئيسي {p} غير موجود")
        if p and not str(r["code"]).startswith(p):
            errs.append(f"سطر {i}: كود {r['code']} يجب أن يبدأ بكود الرئيسي {p}")
        if r.get("account_type") not in ("asset", "liability", "equity", "income", "expense"):
            errs.append(f"سطر {i}: النوع لازم asset/liability/equity/income/expense")
    if errs or check_only:
        return errs
    for r in sorted(rows, key=lambda r: (int(r.get("level") or 1), str(r["code"]))):
        req = str(r.get("requires_cost_center", "")).lower() in YES
        Account.objects.update_or_create(code=str(r["code"]), defaults=dict(
            name=r["name_ar"], parent=Account.objects.filter(code=str(r.get("parent_code") or "")).first(), level=int(r.get("level") or 1),
            is_postable=str(r.get("postable", "")).lower() in YES, account_type=r["account_type"],
            normal_balance=r.get("normal_balance") or ("debit" if r["account_type"] in ("asset", "expense") else "credit"),
            report=r.get("report") or ("balance_sheet" if r["account_type"] in ("asset", "liability", "equity") else "profit_loss"),
            cash_flow_category=r.get("cash_flow_category") or "", currencies=r.get("currencies") or "EGP",
            is_partner_control=str(r.get("is_partner_control", "")).lower() in YES,
            dim1_policy="required" if req else "none", dim3_policy="optional" if req else "none",
            dim2_policy=r.get("dim2_equipment") if r.get("dim2_equipment") in ("required", "optional") else "none"))
    return []


def _dimension(number, default_name):
    def run(rows, check_only):
        dim, _ = Dimension.objects.get_or_create(number=number, defaults={"name": default_name})
        codes = {str(r["code"]) for r in rows}
        existing = set(DimensionValue.objects.filter(dimension=dim).values_list("code", flat=True))
        errs = [f"سطر {i}: الأب {r['parent_code']} غير موجود" for i, r in enumerate(rows, 2)
                if r.get("parent_code") and str(r["parent_code"]) not in codes | existing]
        if errs or check_only:
            return errs
        for r in sorted(rows, key=lambda r: int(r.get("level") or 1)):
            DimensionValue.objects.update_or_create(dimension=dim, code=str(r["code"]), defaults=dict(
                name=r["name_ar"], level=int(r.get("level") or 1), is_postable=str(r.get("postable", "yes")).lower() in YES,
                parent=DimensionValue.objects.filter(dimension=dim, code=str(r.get("parent_code") or "")).first(),
                is_project=str(r.get("is_project", "")).lower() in YES, project_kind=r.get("project_kind") or "",
                legacy_code=str(r.get("legacy_code") or "")))
        return []
    return run


def _items(rows, check_only):
    cats = {c.name: c for c in ItemCategory.objects.all()}
    errs = [f"سطر {i}: التصنيف «{r.get('category')}» غير موجود (موجود: {', '.join(cats)})" for i, r in enumerate(rows, 2) if r.get("category") not in cats]
    errs += [f"سطر {i}: اكتب حد الطلب (0 = من غير تنبيه)" for i, r in enumerate(rows, 2) if r.get("reorder_level") in (None, "")]
    if errs or check_only:
        return errs
    for r in rows:
        Item.objects.update_or_create(code=str(r["code"]), defaults=dict(
            name=r["name"], category=cats[r["category"]], unit=r.get("unit") or "قطعة", barcode=str(r.get("barcode") or ""),
            sale_price=D(r["sale_price"]) if r.get("sale_price") not in (None, "") else None,
            reorder_level=D(r["reorder_level"]) if r.get("reorder_level") not in (None, "") else None))
    return []


def _cashboxes(rows, check_only):
    errs = []
    for i, r in enumerate(rows, 2):
        a = Account.objects.filter(code=str(r.get("account_code"))).first()
        if not a or not a.is_postable:
            errs.append(f"سطر {i}: الحساب {r.get('account_code')} غير موجود أو غير فرعي")
        if r.get("kind") not in ("cash", "wallet", "bank"):
            errs.append(f"سطر {i}: النوع cash أو wallet أو bank")
    if errs or check_only:
        return errs
    for r in rows:
        CashBox.objects.update_or_create(account=Account.objects.get(code=str(r["account_code"])), defaults=dict(
            name=r["name"], kind=r["kind"], currency=r.get("currency") or "EGP"))
    return []


def _opening(rows, check_only, date=None):
    errs, lines, total = [], [], Decimal(0)
    for i, r in enumerate(rows, 2):
        a = Account.objects.filter(code=str(r.get("account_code"))).first()
        if not a or not a.is_postable:
            errs.append(f"سطر {i}: الحساب {r.get('account_code')} غير موجود أو رئيسي")
            continue
        try:
            dr, cr = D(r.get("debit")), D(r.get("credit"))
        except ValueError as e:
            errs.append(f"سطر {i}: {e}")
            continue
        total += dr - cr
        cc = DimensionValue.objects.filter(dimension__number=1, code=str(r.get("cost_center") or "")).first()
        if a.dim1_policy == "required" and not cc:
            errs.append(f"سطر {i}: الحساب {a.code} يحتاج إلى مركز تكلفة")
        partner = None
        if r.get("partner"):
            partner = Partner.objects.filter(name=r["partner"]).first() or (None if check_only else Partner.objects.create(name=r["partner"]))
        elif a.is_partner_control:
            errs.append(f"سطر {i}: الحساب {a.code} يحتاج إلى اسم الطرف")
        lines.append(dict(account=a, debit=dr, credit=cr, dim1=cc, partner=partner, description="رصيد افتتاحي"))
    if total:
        errs.append(f"الأرصدة غير متوازنة: الفرق {total}")
    if errs or check_only:
        return errs
    L.create_entry("OB", date or dt.date.today(), "أرصدة افتتاحية (معالج التجهيز)", lines, legacy_ref="معالج التجهيز")
    return []


STEPS = {
    "accounts": dict(title="شجرة الحسابات", run=_accounts, cols=["code", "name_ar", "parent_code", "level", "postable", "account_type", "currencies",
                     "is_partner_control", "requires_cost_center", "dim2_equipment", "cash_flow_category"],
                     example=["1201030101", "الخزينة الرئيسية", "12010301", 6, "yes", "asset", "EGP", "no", "no", "none", "cash"],
                     help="كل حساب سطر. الرئيسي قبل الفرعي. postable=yes للحساب الذي يُسجَّل عليه."),
    "cost_centers": dict(title="مراكز التكلفة", run=_dimension(1, "مراكز التكلفة"), cols=["code", "name_ar", "parent_code", "level", "postable", "is_project", "project_kind"],
                         example=["1", "المشروع الأول", "", 1, "no", "yes", "permanent"], help="المشاريع في المستوى الأول (is_project=yes)."),
    "equipment": dict(title="المعدات (البعد الثاني)", run=_dimension(2, "المعدات"), cols=["code", "name_ar", "parent_code", "level", "postable"],
                      example=["E201", "مولد 1", "E2", 2, "yes"], help="مجموعات (postable=no) وتحتها المعدات."),
    "items": dict(title="الأصناف", run=_items, cols=["code", "name", "category", "unit", "barcode", "sale_price", "reorder_level"],
                  example=["P-0001", "سماد يوريا", "أسمدة", "شيكارة", "", "", 20], help="يجب أن يكون التصنيف موجودًا في «تصنيفات الأصناف»."),
    "cashboxes": dict(title="الخزائن والبنوك", run=_cashboxes, cols=["name", "kind", "account_code", "currency"],
                      example=["خزينة الفرع", "cash", "1201030101", "EGP"], help="kind: cash أو wallet أو bank. يجب أن يكون الحساب في الشجرة."),
    "opening": dict(title="الأرصدة الافتتاحية", run=_opening, cols=["account_code", "partner", "cost_center", "debit", "credit"],
                    example=["1203010101", "عميل 1", "", 1000, ""], help="يجب أن يساوي المدين الدائن. والطرف إجباري على حسابات العملاء والموردين والسلف."),
}


def read_rows(f):
    name = f.name.lower()
    if name.endswith(".csv"):
        return list(csv.DictReader(io.StringIO(f.read().decode("utf-8-sig"))))
    ws = openpyxl.load_workbook(f, data_only=True, read_only=True).active
    it = ws.iter_rows(values_only=True)
    head = [str(h).strip() if h is not None else "" for h in next(it)]
    return [{head[i]: v for i, v in enumerate(r) if i < len(head)} for r in it if r and any(v not in (None, "") for v in r)]


@login_required
@permission_required("ledger.add_account", raise_exception=True)
def index(request):
    return render(request, "ui/setup_index.html", {"steps": list(STEPS.items())})


def _can(request, step):
    from django.core.exceptions import PermissionDenied

    if step not in STEPS or not request.user.has_perm("inventory.add_item" if step == "items" else "ledger.add_account"):
        raise PermissionDenied


@login_required
def template(request, step):
    _can(request, step)
    s = STEPS[step]
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = s["title"][:30]
    ws.sheet_view.rightToLeft = True
    ws.append(s["cols"])
    ws.append(s["example"])
    buf = io.BytesIO()
    wb.save(buf)
    resp = HttpResponse(buf.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = f'attachment; filename="template-{step}.xlsx"'
    return resp


@login_required
def upload(request, step):
    _can(request, step)
    s = STEPS[step]
    ctx = {"s": s, "step": step}
    if request.method == "POST" and request.FILES.get("file"):
        try:
            rows = read_rows(request.FILES["file"])
        except Exception as e:  # unreadable file
            messages.error(request, f"الملف غير مقروء: {e}")
            return redirect("setup_upload", step=step)
        missing = [c for c in s["cols"][:2] if rows and c not in rows[0]]
        errs = [f"أعمدة ناقصة: {', '.join(missing)} — استخدم القالب"] if missing else s["run"](rows, check_only=True)
        request.session[f"setup_{step}"] = [{k: (str(v) if isinstance(v, (Decimal, dt.date)) else v) for k, v in r.items()} for r in rows]
        ctx.update(rows=rows[:25], count=len(rows), errs=errs, cols=s["cols"])
    elif request.method == "POST" and "confirm" in request.POST:
        rows = request.session.pop(f"setup_{step}", None)
        if not rows:
            return redirect("setup_upload", step=step)
        with transaction.atomic():
            errs = s["run"](rows, check_only=False)
        if errs:
            for e in errs[:20]:
                messages.error(request, e)
        else:
            messages.success(request, f"تم استيراد {len(rows)} سطر في «{s['title']}»")
        return redirect("shop_items" if step == "items" else "setup_index")
    return render(request, "ui/setup_upload.html", ctx)
