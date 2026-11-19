"""Stock count (جرد) → adjustment: read counted quantities from Excel / CSV / PDF, compare with the book, post the differences.

Columns are found by their header (any order): item code and/or name, and the counted quantity
(«الكمية الفعلية», «الجرد», «الفعلي», «counted» … or plain «الكمية» when nothing more specific exists).
"""
import csv
import io
import re
from decimal import Decimal, InvalidOperation

import openpyxl
from django.core.exceptions import ValidationError
from django.db import transaction

from . import services as INV
from .models import Item, StockMove, StockMoveLine, StockQuant

CODE = ("الكود", "كود", "كود الصنف", "رقم الصنف", "code", "item code", "sku")
NAME = ("الصنف", "اسم الصنف", "الاسم", "البيان", "item", "name", "description")
QTY_STRONG = ("الكمية الفعلية", "الرصيد الفعلي", "الجرد", "الكمية المجرودة", "الفعلي", "فعلي", "counted", "physical", "count")
COST = ("سعر التكلفة", "التكلفة", "سعر الشراء", "تكلفة الوحدة", "cost", "unit cost", "purchase price")
QTY_WEAK = ("الكمية", "الرصيد", "qty", "quantity", "balance")


def norm(s):
    s = re.sub(r"[إأآ]", "ا", str(s or "")).replace("ة", "ه").replace("ى", "ي").replace("ـ", "")
    return re.sub(r"\s+", " ", s).strip().lower()


def num(v):
    if v in (None, ""):
        return None
    s = str(v).strip().replace(",", "").translate(str.maketrans("٠١٢٣٤٥٦٧٨٩٫", "0123456789."))
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _rows_from_file(f):
    name = f.name.lower()
    data = f.read()
    if name.endswith((".xlsx", ".xlsm")):
        wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
        return [list(r) for ws in wb.worksheets for r in ws.iter_rows(values_only=True)]
    if name.endswith(".csv"):
        text = data.decode("utf-8-sig", errors="replace")
        return [r for r in csv.reader(io.StringIO(text))]
    if name.endswith(".pdf"):
        import pdfplumber

        rows = []
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for page in pdf.pages:
                for t in page.extract_tables():
                    rows.extend(t)
        if not rows:
            raise ValidationError("تعذّرت قراءة جدول من ملف الـPDF (إن كان صورة ممسوحة فلن يصلح) — صدّره إلى إكسل وارفعه")
        return rows
    raise ValidationError("نوع الملف غير مدعوم — ارفع إكسل (xlsx) أو CSV أو PDF")


def _find(header, keys):
    h = [norm(c) for c in header]
    for k in keys:
        for i, c in enumerate(h):
            if c == norm(k):
                return i
    for k in keys:
        for i, c in enumerate(h):
            if c and norm(k) in c:
                return i
    return None


def _header(rows):
    for variant in (lambda c: c, lambda c: str(c or "")[::-1]):  # some PDFs return Arabic reversed
        for idx, r in enumerate(rows[:40]):
            r2 = [variant(c) for c in r]
            q = _find(r2, QTY_STRONG)
            if q is None:
                q = _find(r2, QTY_WEAK)
            code, name = _find(r2, CODE), _find(r2, NAME)
            if q is not None and (code is not None or name is not None) and q not in (code, name):
                return idx, code, name, q, variant
    raise ValidationError("لم أجد عناوين الأعمدة: يلزم عمود «الكود» أو «الصنف» وعمود «الكمية الفعلية» (أو «الجرد»)")


def parse(f, warehouse):
    rows = _rows_from_file(f)
    hi, ci, ni, qi, variant = _header(rows)
    ki = _find([variant(c) for c in rows[hi]], COST)
    by_code = {i.code.strip().lower(): i for i in Item.objects.all()}
    by_name = {}
    for i in Item.objects.all():
        by_name.setdefault(norm(i.name), i)
        for a in (i.aliases or "").split("/"):
            if a.strip():
                by_name.setdefault(norm(a), i)
    book = {q.item_id: q.qty for q in StockQuant.objects.filter(warehouse=warehouse)}
    out, missing = [], []
    for r in rows[hi + 1:]:
        r = [variant(c) for c in r] + [None] * 5
        counted = num(r[qi])
        code = str(r[ci]).strip() if ci is not None and r[ci] not in (None, "") else ""
        code = code[:-2] if code.endswith(".0") else code
        nm = str(r[ni]).strip() if ni is not None and r[ni] not in (None, "") else ""
        if counted is None or not (code or nm):
            continue
        item = by_code.get(code.lower()) or by_name.get(norm(nm))
        if not item:
            missing.append(f"{code} {nm}".strip())
            continue
        b = book.get(item.pk, Decimal(0))
        cost = num(r[ki]) if ki is not None else None
        out.append(dict(item=item.pk, code=item.code, name=item.name, unit=item.unit, book=b, counted=counted, diff=counted - b,
                        cost=cost))
    return out, missing


@transaction.atomic
def post_adjustment(warehouse, date, rows, note="", user=None, opening=False):
    """opening=True: the first stock of a new shop — increases are booked as «رصيد أول المدة» at the cost in the file."""
    ins = [r for r in rows if Decimal(str(r["diff"])) > 0]
    outs = [r for r in rows if Decimal(str(r["diff"])) < 0]
    made = []
    proj = warehouse.project
    cc = proj if proj.is_postable else (proj.children.filter(is_postable=True, name__startswith="عام").order_by("code").first()
                                        or proj.children.filter(is_postable=True).order_by("code").first())
    for kind, rs in (("opening" if opening else "adjust_in", ins), ("adjust_out", outs)):
        if not rs:
            continue
        mv = StockMove.objects.create(kind=kind, date=date, warehouse=warehouse, created_by=user,
                                      description=(f"رصيد أول المدة {warehouse.name}" if kind == "opening" else f"تسوية جرد {warehouse.name} {date:%d/%m/%Y} {note}")[:300])
        for r in rs:
            item = Item.objects.get(pk=r["item"])
            StockMoveLine.objects.create(move=mv, item=item, qty=abs(Decimal(str(r["diff"]))), dim1=cc,
                                         unit_cost=(Decimal(str(r["cost"])) if r.get("cost") not in (None, "", "None") else
                                                    item.avg_cost.quantize(Decimal("0.0001"))) if kind != "adjust_out" else None)
        made.append(INV.post_move(mv, user=user, allow_negative=True))
    return made


def template_workbook(warehouse):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "جرد"
    ws.sheet_view.rightToLeft = True
    ws.append(["الكود", "الصنف", "الوحدة", "الرصيد الدفتري", "الكمية الفعلية", "سعر التكلفة"])
    qty = {q.item_id: q.qty for q in StockQuant.objects.filter(warehouse=warehouse)}
    for i in Item.objects.filter(active=True).order_by("category__name", "name"):
        ws.append([i.code, i.name, i.unit, float(qty.get(i.pk, 0)), None, float(i.avg_cost) if i.qty_on_hand else None])
    for col, w in zip("ABCDEF", (14, 42, 10, 16, 16, 14)):
        ws.column_dimensions[col].width = w
    return wb
