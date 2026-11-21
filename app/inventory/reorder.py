"""«حد الطلب»: every stock item carries the quantity at which it should be bought again. When a sale (till, gaming hall,
clinic visit) takes an item — or a recipe's ingredient — down to it, the screen that sold it shows a pop-up."""
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError

DEFAULT = Decimal("5")  # what items made by the program itself start with (samples, drug library, owner's phone) — editable


def needs_level(item_type):
    """Recipes (made to order) and services (playing time, delivery) hold no stock, so they have no order limit."""
    return (item_type or "stock") == "stock"


def parse(value, item_type="stock"):
    """The limit typed in a form / sheet → Decimal; a stock item without it is refused (None for recipes / services)."""
    if not needs_level(item_type):
        return None
    txt = str(value if value is not None else "").strip().replace(",", ".").translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    if not txt:
        raise ValidationError("اكتب حد الطلب للصنف (أقل كمية تريد إعادة الشراء عندها) — اكتب 0 إن لم ترد تنبيهًا")
    try:
        d = Decimal(txt)
    except InvalidOperation:
        raise ValidationError("يجب أن يكون حد الطلب رقمًا")
    if d < 0:
        raise ValidationError("لا يمكن أن يكون حد الطلب سالبًا")
    return d


def _row(i):
    return dict(code=i.code, name=i.name, q=f"{i.qty_on_hand.normalize():f}", r=f"{i.reorder_level.normalize():f}", unit=i.unit)


IN_KINDS = ("receipt", "opening", "adjust_in", "harvest", "return", "transfer")


def never_stocked(ids):
    """Of these item ids: the ones with nothing on hand that never received any stock (a sample / menu item the shop has
    not bought yet). The «حد الطلب» pop-up skips them — warning on the very first sale of an item never bought is noise;
    the stock pages still list it."""
    from .models import Item, StockMoveLine

    ids = set(ids)
    if not ids:
        return set()
    empty = set(Item.objects.filter(pk__in=ids, qty_on_hand__lte=0).values_list("pk", flat=True))
    if not empty:
        return set()
    got = set(StockMoveLine.objects.filter(item_id__in=empty, move__state="posted", move__kind__in=IN_KINDS).values_list("item_id", flat=True))
    return empty - got


def low(item_ids=None):
    """Stock items at or under their order limit (0 = no alert). With item_ids: only those items and the ingredients of
    the recipes among them — what one sale could have touched."""
    from .models import Item, RecipeLine

    qs = Item.objects.filter(active=True, item_type="stock", reorder_level__gt=0)
    if item_ids is not None:
        ids = set(item_ids)
        if not ids:
            return []
        ids |= set(RecipeLine.objects.filter(recipe__item_id__in=ids).values_list("ingredient_id", flat=True))
        qs = qs.filter(pk__in=ids)
    rows = [i for i in qs.order_by("name") if i.qty_on_hand <= i.reorder_level]
    skip = never_stocked([i.pk for i in rows])
    return [_row(i) for i in rows if i.pk not in skip]


def popup(request, item_ids):
    """For the pages that sell from the office (clinic visit, purchase-side screens): the same warning as a pop-up message."""
    from django.contrib import messages

    rows = low(item_ids)
    if rows:
        txt = "⚠ أصناف وصلت إلى حد الطلب: " + "، ".join(f"{r['name']} (الباقي {r['q']} {r['unit']} — الحد {r['r']})" for r in rows[:12])
        messages.warning(request, txt, extra_tags="popup")
    return rows
