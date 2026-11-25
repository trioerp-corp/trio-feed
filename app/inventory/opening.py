"""«رصيد البضاعة الموجودة لديك الآن» (1.25): the opening stock step of «تجهيز المحل».

The owner types the quantity and the purchase price of what is on his shelves today — one grid with search and barcode scan,
sizes × colours for clothes, boxes + loose strips for a pharmacy, or an Excel upload — and it posts ONE stock move of kind
«opening» (Dr the category's stock account / Cr 1207010101 «حساب وسيط (أرصدة افتتاحية ومشتريات)», like every opening balance
in the program), so Item.stock_value stays equal to the stock account. Trial samples never get stock by themselves.
The step can be used again later by the owner only: it ADDS what he types on top of the current balance (another opening move).
"""
import datetime as dt
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import Item, ItemUnit, StockMove, StockMoveLine, StockQuant, Warehouse

MAX_QTY = Decimal("1000000")
MAX_COST = Decimal("10000000")
_DIG = str.maketrans("٠١٢٣٤٥٦٧٨٩٫", "0123456789.")


def dec(v, what, item_name):
    txt = str(v if v is not None else "").strip().replace(",", "").translate(_DIG)
    if not txt:
        return None
    try:
        d = Decimal(txt)
    except InvalidOperation:
        raise ValidationError(f"«{item_name}»: {what} ليس رقمًا")
    if not d.is_finite() or d < 0:
        raise ValidationError(f"«{item_name}»: {what} غير صحيح")
    return d


def stock_items():
    """What can hold stock: active stock items, not the price-free «0» item and not a sizes × colours parent (its sizes hold it)."""
    return Item.objects.filter(active=True, item_type="stock").exclude(code="0").select_related("category", "variant_of")


def _last_cost(ids):
    out = {}
    for item_id, uc in (StockMoveLine.objects.filter(item_id__in=ids, move__state="posted", unit_cost__gt=0,
                                                     move__kind__in=("receipt", "opening", "adjust_in"))
                        .order_by("item_id", "-move__date", "-id").values_list("item_id", "unit_cost")):
        out.setdefault(item_id, uc)
    return out


def _n(d):
    return format(d.normalize(), "f") if d is not None else ""


def rows(warehouse):
    """The grid's data (JSON-ready): plain rows, and one row per sizes × colours product with its cells."""
    from .variants import is_parent

    items = list(stock_items())
    have = dict(StockQuant.objects.filter(warehouse=warehouse, item__in=items).values_list("item_id", "qty"))
    last = _last_cost([i.pk for i in items])
    big = {}
    for u in ItemUnit.objects.filter(item__in=items).order_by("-factor"):
        big.setdefault(u.item_id, u)
    ubar = {}
    for u in ItemUnit.objects.filter(item__in=items).exclude(barcode=""):
        ubar.setdefault(u.item_id, []).append(u.barcode)

    def cost(i):
        c = i.std_cost if i.std_cost is not None else last.get(i.pk)
        if c is None and i.qty_on_hand > 0 and i.stock_value > 0:
            c = i.avg_cost
        return c

    out, grids = [], {}
    for i in items:
        p = i.variant_of
        if p is not None and is_parent(p):
            g = grids.get(p.pk)
            if g is None:
                gr = p.extra["grid"]
                g = grids[p.pk] = dict(id=p.pk, grid=1, n=p.name, cat=p.category.name, u=p.unit, sizes=list(gr.get("sizes") or []) or [""],
                                       colors=list(gr.get("colors") or []) or [""], cells={}, b=[], c=_n(p.std_cost) if p.std_cost is not None else "")
                out.append(g)
            x = i.extra or {}
            key = f"{x.get('size', '')}|{x.get('color', '')}"
            g["cells"][key] = dict(id=i.pk, h=_n(have.get(i.pk, Decimal(0))), b=i.barcode)
            if i.barcode:
                g["b"].append(i.barcode)
            if not g["c"] and cost(i) is not None:
                g["c"] = _n(Decimal(cost(i)).quantize(Decimal("0.01")))
            continue
        bu = big.get(i.pk)
        c = cost(i)
        if bu is not None and c is not None:  # the price the owner knows: per box / carton
            c = Decimal(c) * bu.factor
        out.append(dict(id=i.pk, n=i.name, code=i.code, cat=i.category.name, u=i.unit, b=[x for x in [i.barcode] + ubar.get(i.pk, []) if x],
                        h=_n(have.get(i.pk, Decimal(0))), c=_n(Decimal(c).quantize(Decimal("0.01"))) if c is not None else "",
                        bu=dict(n=bu.name, f=_n(bu.factor)) if bu is not None else None))
    out.sort(key=lambda r: (r["cat"], r["n"]))
    return out


def lines_from(data):
    """The posted grid → [(item, base qty, unit cost per base unit, expiry)].

    data: [{"id", "q" (in the big unit when the item has one), "l" (loose base units), "c" (cost per the unit q is in), "x" (expiry)}]
    — rows with no quantity are skipped; a row with a quantity needs a price (typed or the item's known cost)."""
    if not isinstance(data, list):
        raise ValidationError("بيانات غير صحيحة")
    from .variants import is_parent

    want = {}
    for r in data:
        if not isinstance(r, dict):
            raise ValidationError("بيانات غير صحيحة")
        try:
            pk = int(r.get("id"))
        except (TypeError, ValueError):
            raise ValidationError("بيانات غير صحيحة")
        if pk in want:
            raise ValidationError("صنف مكرر في الرصيد")
        want[pk] = r
    items = {i.pk: i for i in Item.objects.filter(pk__in=want).select_related("variant_of")}
    big = {}
    for u in ItemUnit.objects.filter(item_id__in=want).order_by("-factor"):
        big.setdefault(u.item_id, u)
    last = _last_cost(list(want))
    out, errs = [], []
    for pk, r in want.items():
        i = items.get(pk)
        if i is None or not i.active or i.item_type != "stock" or i.code == "0" or is_parent(i):
            errs.append("صنف غير موجود أو ليس صنفًا مخزنيًا")
            continue
        try:
            bu = big.get(pk)
            q = dec(r.get("q"), "الكمية", i.name) or Decimal(0)
            loose = dec(r.get("l"), f"عدد {i.unit}", i.name) or Decimal(0) if bu is not None else Decimal(0)
            base = q * (bu.factor if bu is not None else 1) + loose
            if base == 0:
                continue
            if base > MAX_QTY or base != base.quantize(Decimal("0.001")):
                raise ValidationError(f"«{i.name}»: الكمية غير صحيحة")
            c = dec(r.get("c"), "سعر الشراء", i.name)
            if c is not None and c > MAX_COST:
                raise ValidationError(f"«{i.name}»: سعر الشراء غير صحيح")
            if c is None:
                known = i.std_cost if i.std_cost is not None else last.get(pk)
                if known is None:
                    raise ValidationError(f"«{i.name}»: اكتب سعر الشراء")
                uc = Decimal(known)
            else:
                uc = c / bu.factor if bu is not None else c
            x = None
            if r.get("x"):
                try:
                    x = dt.date.fromisoformat(str(r["x"])[:10])
                except ValueError:
                    raise ValidationError(f"«{i.name}»: تاريخ الصلاحية غير صحيح")
                if not (2000 <= x.year <= 2100):
                    raise ValidationError(f"«{i.name}»: تاريخ الصلاحية غير صحيح")
            out.append((i, base, uc.quantize(Decimal("0.0001")), x))
        except ValidationError as e:
            errs.append(e.messages[0])
    if errs:
        raise ValidationError(errs[:8])
    if not out:
        raise ValidationError("لم تكتب أي كمية — اكتب كمية صنف واحد على الأقل")
    return out


def done():
    """The first opening of the shop's stock, or None."""
    return StockMove.objects.filter(kind="opening", state="posted").order_by("pk").first()


@transaction.atomic
def post(warehouse, lines, user=None, day=None):
    """ONE «opening» move for every line (Dr stock / Cr 1207010101). Returns the posted move."""
    from . import services as INV

    if not isinstance(warehouse, Warehouse) or not warehouse.active:
        raise ValidationError("اختر المخزن")
    again = done() is not None
    day = day or timezone.localdate()
    mv = StockMove.objects.create(kind="opening", date=day, warehouse=warehouse, created_by=user if getattr(user, "pk", None) else None,
                                  description=(f"رصيد افتتاحي إضافي — {warehouse.name}" if again else f"رصيد أول المدة — تجهيز المحل — {warehouse.name}")[:300])
    for item, qty, uc, x in lines:
        StockMoveLine.objects.create(move=mv, item=item, qty=qty, unit_cost=uc, expiry=x)
    return INV.post_move(mv, user=user if getattr(user, "pk", None) else None, allow_negative=True)


def summary(move):
    from django.db.models import Count, Sum

    a = move.lines.aggregate(n=Count("id"), v=Sum("value"))
    return dict(n=a["n"] or 0, v=a["v"] or Decimal(0), date=move.date, number=move.number)
