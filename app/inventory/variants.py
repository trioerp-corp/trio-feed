"""«صنف بمقاسات وألوان» (1.25, clothes / shoes and any shop that sells sizes × colours).

One product (the parent) with a child stock Item per size × colour — the restaurant sizes' model (variant_of / size_name),
so every variant is a normal stock item: its own barcode, price, stock, cost, sales and GL. The parent is only the till's
picker and the report's row: item_type «service», no price, and never stock (pos.services.ingest_order and
inventory.services.post_move refuse it).

- parent.extra["grid"] = {"sizes": [...], "colors": [...]} — the order the owner typed
- child.extra = {"size": "L", "color": "أزرق", "order": n}; child.name = «قميص — L / أزرق», size_name = «L / أزرق»
"""
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum

from . import names
from .models import Item, ItemUnit, StockMove, StockMoveLine, Warehouse

MAX = 25  # sizes, and colours
MAX_CELLS = 300
SIZE_SETS = {"ملابس": ["S", "M", "L", "XL", "XXL"], "أرقام": ["38", "40", "42", "44", "46"],
             "أحذية": ["38", "39", "40", "41", "42", "43", "44", "45"], "أطفال": ["2", "4", "6", "8", "10", "12"]}
COLORS = ["أسود", "أبيض", "أزرق", "كحلي", "رمادي", "بيج", "أحمر", "أخضر", "بني"]
_DIG = str.maketrans("٠١٢٣٤٥٦٧٨٩٫", "0123456789.")


def enabled(s=None):
    """Shops with shelves that sell sizes / colours: every «retail» shop, and a market whose activity has a size field."""
    from core.activities import current
    from core.models import CompanySettings

    s = s or CompanySettings.get()
    if s.business == "retail":
        return True
    return s.business == "market" and any(f.get("k") in ("size", "color") for f in current(s).get("fields") or [])


def is_parent(item):
    return bool(item and not item.variant_of_id and isinstance((item.extra or {}).get("grid"), dict))


def label(size, color):
    return " / ".join(x for x in (size, color) if x)


def clean_list(values, what):
    out, seen = [], set()
    for v in values or []:
        v = names.clean(v, 30)
        if not v:
            continue
        k = names.norm(v)
        if k in seen:
            continue
        seen.add(k)
        out.append(v)
    if len(out) > MAX:
        raise ValidationError(f"أقصى عدد لـ{what} {MAX}")
    return out


def _money(v, what, required=False):
    txt = str(v if v is not None else "").strip().replace(",", "").translate(_DIG)
    if not txt:
        if required:
            raise ValidationError(f"اكتب {what}")
        return None
    try:
        d = Decimal(txt)
    except InvalidOperation:
        raise ValidationError(f"اكتب {what} بالأرقام")
    if not d.is_finite() or d < 0 or d > Decimal("10000000"):
        raise ValidationError(f"{what} غير صحيح")
    return d


def _code():
    last = Item.objects.filter(code__regex=r"^\d+$").order_by("-id").first()
    code = int(last.code) + 1 if last else 1001
    while Item.objects.filter(code=str(code)).exists():
        code += 1
    return str(code)


def auto_barcode(code):
    """An in-shop EAN-13 «200» + the item code (unique because codes are), with its check digit."""
    body = ("200" + str(code).zfill(9))[-12:] if str(code).isdigit() else None
    if not body:
        return ""
    s = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(body))
    return body + str((10 - s % 10) % 10)


def _barcode_owner(bc, exclude=None):
    it = Item.objects.filter(barcode=bc).exclude(pk=exclude or 0).first()
    if it:
        return it.name
    u = ItemUnit.objects.filter(barcode=bc).select_related("item").first()
    return f"{u.name} {u.item.name}" if u else None


def variants(parent, active=True):
    qs = parent.sizes.all()
    if active:
        qs = qs.filter(active=True)
    return sorted(qs, key=lambda v: (v.extra or {}).get("order", 0))


@transaction.atomic
def save(name, cat, price, cost=None, sizes=(), colors=(), cells=None, reorder="1", item=None, unit="قطعة", user=None,
         merge=False, day=None, warehouse=None, extra=None):
    """Adds / edits a product with sizes × colours.

    cells: {(size, colour): {"p": price or "", "b": barcode or "", "q": opening qty or ""}} — missing = the main price,
    an automatic barcode, no opening stock. Without merge every size × colour exists and combinations no longer listed
    are stopped (only with no stock left); with merge (Excel) only the given cells are touched.
    Returns (parent, made count)."""
    from .reorder import parse as reorder_of

    name = names.clean(name, 120)
    if not name:
        raise ValidationError("اكتب اسم الصنف")
    sizes, colors = clean_list(sizes, "المقاسات"), clean_list(colors, "الألوان")
    cells = cells or {}
    if merge:
        combos = list(cells)
    else:
        if not sizes and not colors:
            raise ValidationError("اكتب مقاسًا واحدًا على الأقل أو لونًا")
        combos = [(s, c) for s in (sizes or [""]) for c in (colors or [""])]
    if len(combos) > MAX_CELLS:
        raise ValidationError(f"أقصى عدد للمقاسات × الألوان {MAX_CELLS}")
    if item is not None and not is_parent(item):
        raise ValidationError("هذا الصنف ليس صنفًا بمقاسات وألوان")
    names.check(name, exclude=item.pk if item else None)
    price = _money(price, "سعر البيع", required=item is None)
    cost = _money(cost, "سعر الشراء")
    level = reorder_of(reorder)
    if item is None:
        item = Item(code=_code(), item_type="service")
    old_name = item.name
    grid = dict((item.extra or {}).get("grid") or {})
    if merge:  # keep the owner's order, then the new ones
        sizes = grid.get("sizes", []) + [s for s, _ in combos if s and s not in grid.get("sizes", [])]
        colors = grid.get("colors", []) + [c for _, c in combos if c and c not in grid.get("colors", [])]
        sizes, colors = list(dict.fromkeys(sizes)), list(dict.fromkeys(colors))
    item.name, item.category, item.unit, item.active, item.sale_price = name, cat, unit or item.unit or "قطعة", True, None
    if cost is not None:
        item.std_cost = cost
    extra = {k: v for k, v in (extra or {}).items() if k not in ("grid", "size", "color", "order") and v}  # brand, season… (activity fields)
    item.extra = {**(item.extra or {}), **extra, "grid": {"sizes": sizes, "colors": colors}}
    item.save()
    old = {((v.extra or {}).get("size", ""), (v.extra or {}).get("color", "")): v for v in item.sizes.all()}
    keep, opening, made, typed = set(), [], 0, {}
    order = {(s, c): i for i, (s, c) in enumerate((s, c) for s in (sizes or [""]) for c in (colors or [""]))}
    for s, c in combos:
        cell = cells.get((s, c)) or {}
        lab = label(s, c)
        p = _money(cell.get("p"), f"سعر «{lab}»")
        q = _money(cell.get("q"), f"كمية «{lab}»")
        v = old.get((s, c))
        if v is None:
            v = Item(code=_code(), item_type="stock", variant_of=item, reorder_level=level)
            made += 1
        p = p if p is not None else (price if price is not None else v.sale_price)
        if p is None or p <= 0:
            raise ValidationError(f"اكتب سعر البيع لـ«{lab}»")
        v.name, v.size_name, v.category, v.unit, v.sale_price, v.active = f"{name} — {lab}"[:150], lab[:40], cat, item.unit, p, True
        if cost is not None:
            v.std_cost = cost
        v.extra = {**(v.extra or {}), **extra, "size": s, "color": c, "order": order.get((s, c), len(order))}
        v.extra = {k: x for k, x in v.extra.items() if x not in ("", None) or k == "order"}
        bc = str(cell.get("b") or "").strip()[:40]
        if bc:
            if bc in typed:
                raise ValidationError(f"الباركود {bc} مكتوب لـ«{typed[bc]}» و«{lab}»")
            typed[bc] = lab
            other = _barcode_owner(bc, exclude=v.pk)
            if other:
                raise ValidationError(f"الباركود {bc} مسجل على «{other}»")
            v.barcode = bc
        v.save()
        if not v.barcode:
            v.barcode = auto_barcode(v.code) if not _barcode_owner(auto_barcode(v.code), v.pk) else ""
            v.save(update_fields=["barcode"])
        keep.add(v.pk)
        if q:
            if StockMoveLine.objects.filter(item=v, move__state="posted").exists():
                raise ValidationError(f"«{lab}» له رصيد وحركات — عدّل رصيده من الجرد")
            uc = cost if cost is not None else v.std_cost
            if uc is None:
                raise ValidationError("كتبت كميات — اكتب سعر الشراء لحساب قيمة البضاعة")
            opening.append((v, q, uc))
    if not merge:
        for v in item.sizes.exclude(pk__in=keep).filter(active=True):
            if v.qty_on_hand != 0:
                raise ValidationError(f"«{v.size_name}» عليه رصيد {v.qty_on_hand.normalize():f} — لا يمكن حذفه قبل تصفير رصيده بالجرد")
            v.active = False
            v.save(update_fields=["active"])
    elif old_name and old_name != name:
        for v in item.sizes.exclude(pk__in=keep):
            v.name = f"{name} — {v.size_name}"[:150]
            v.save(update_fields=["name"])
    if opening:
        from django.utils import timezone

        from . import services as INV

        wh = warehouse or Warehouse.objects.filter(active=True).order_by("pk").first()
        mv = StockMove.objects.create(kind="opening", date=day or timezone.localdate(), warehouse=wh, created_by=user,
                                      description=f"رصيد أول المدة — {name}")
        for v, q, uc in opening:
            StockMoveLine.objects.create(move=mv, item=v, qty=q, unit_cost=uc)
        INV.post_move(mv, user=user, allow_negative=True)
    return item, made


def products(cat=None, q=""):
    qs = Item.objects.filter(variant_of__isnull=True, active=True, extra__has_key="grid").select_related("category").order_by("category__name", "name")
    if cat:
        qs = qs.filter(category_id=cat)
    out = [p for p in qs if is_parent(p)]
    if q:
        k = names.norm(q)
        out = [p for p in out if all(w in names.norm(p.name + " " + " ".join(p.extra["grid"].get("sizes", []) + p.extra["grid"].get("colors", [])))
                                     for w in k.split())]
    return out


def matrix(parent):
    """Size × colour quantities of one product (+ value): rows = colours, columns = sizes."""
    vs = [v for v in parent.sizes.all() if v.active or v.qty_on_hand]
    g = (parent.extra or {}).get("grid") or {}
    sizes = list(g.get("sizes") or []) or [""]
    colors = list(g.get("colors") or []) or [""]
    for v in vs:  # a stopped variant that still holds stock keeps its column / row
        s, c = (v.extra or {}).get("size", ""), (v.extra or {}).get("color", "")
        if s not in sizes:
            sizes.append(s)
        if c not in colors:
            colors.append(c)
    by = {((v.extra or {}).get("size", ""), (v.extra or {}).get("color", "")): v for v in vs}
    rows = []
    for c in colors:
        cells = [by.get((s, c)) for s in sizes]
        rows.append(dict(color=c, cells=cells, total=sum((v.qty_on_hand for v in cells if v), Decimal(0))))
    col_tot = [sum((by[(s, c)].qty_on_hand for c in colors if (s, c) in by), Decimal(0)) for s in sizes]
    return dict(item=parent, sizes=sizes, rows=rows, col_tot=col_tot, qty=sum(col_tot, Decimal(0)),
                value=sum((v.stock_value for v in vs), Decimal(0)))


def sold_by_size(d1, d2, cat=None):
    """Net pieces sold (sales − returns) of the variants between two days: by size, by colour, and the top variants."""
    from pos.models import OrderLine

    qs = OrderLine.objects.filter(item__variant_of__isnull=False, item__variant_of__extra__has_key="grid", order__cancelled=False,
                                  order__created_at_device__date__gte=d1, order__created_at_device__date__lte=d2)
    if cat:
        qs = qs.filter(item__variant_of__category_id=cat)
    by_size, by_color, by_item = {}, {}, {}
    for ln in qs.select_related("order", "item"):
        sign = -1 if ln.order.is_return else 1
        q, amt = sign * ln.qty, sign * (ln.qty * ln.price - ln.discount)
        x = ln.item.extra or {}
        for d, k in ((by_size, x.get("size") or "—"), (by_color, x.get("color") or "—"), (by_item, ln.item.name)):
            r = d.setdefault(k, [Decimal(0), Decimal(0)])
            r[0] += q
            r[1] += amt

    def top(d, n=30):
        return [dict(k=k, q=v[0], amt=v[1]) for k, v in sorted(d.items(), key=lambda kv: -kv[1][0])[:n]]

    return dict(sizes=top(by_size), colors=top(by_color), items=top(by_item, 15))


def stock_total(parent):
    return parent.sizes.aggregate(q=Sum("qty_on_hand"))["q"] or Decimal(0)
