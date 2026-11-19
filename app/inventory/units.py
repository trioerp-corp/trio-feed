"""Selling a pharmacy item by the strip (شريط) as well as by the box (علبة).

The stock of such an item is kept in strips (the smallest unit sold), and the box becomes a bigger unit (ItemUnit, factor = strips
in a box) with the box's price and barcode — so scanning the box sells a box, choosing «شريط» sells one strip, and the shelf
count never shows fractions. An item that already has stock / history in boxes is converted: every quantity × strips and every
unit price ÷ strips, so values and the ledger do not change."""
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F

from .models import Item, ItemUnit, ProductionLine, RecipeLine, StockMoveLine, StockQuant

BOX, STRIP = "علبة", "شريط"


def strip_info(item):
    """(strips per box, box unit) when the item already sells by the strip, else (None, None)."""
    if item.unit != STRIP:
        return None, None
    u = item.units.filter(name=BOX).first()
    return (int(u.factor), u) if u else (None, None)


def _q2(x):
    return Decimal(x).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@transaction.atomic
def sell_by_strip(item: Item, strips, *, box_price=None, strip_price=None):
    """Turn a box item into strip-based stock + a «علبة» unit. Calling it again only updates the counts / prices."""
    try:
        n = int(strips)
    except (TypeError, ValueError):
        raise ValidationError("يجب أن يكون عدد الشرائط في العلبة رقمًا")
    if not 2 <= n <= 100:
        raise ValidationError("عدد الشرائط من 2 إلى 100")
    item = Item.objects.select_for_update().get(pk=item.pk)
    have, box = strip_info(item)
    if have:  # already by the strip: a different count means the box holds another number of strips
        if have != n:
            box.factor = n
            box.save(update_fields=["factor"])
        box_price = Decimal(str(box_price)) if box_price not in (None, "") else box.sale_price
        if box_price is not None:
            box.sale_price = _q2(box_price)
            box.save(update_fields=["sale_price"])
        item.sale_price = _q2(Decimal(str(strip_price))) if strip_price not in (None, "") else (_q2(box_price / n) if box_price else item.sale_price)
        item.save(update_fields=["sale_price"])
        return item
    box_price = Decimal(str(box_price)) if box_price not in (None, "") else item.sale_price
    N = Decimal(n)
    # the history in boxes becomes strips: quantities × n, unit prices ÷ n (values stay the same)
    StockMoveLine.objects.filter(item=item).update(qty=F("qty") * N)
    for ln in StockMoveLine.objects.filter(item=item, unit_cost__isnull=False):
        StockMoveLine.objects.filter(pk=ln.pk).update(unit_cost=(ln.unit_cost / N).quantize(Decimal("0.0001")))
    StockQuant.objects.filter(item=item).update(qty=F("qty") * N)
    RecipeLine.objects.filter(ingredient=item).update(qty=F("qty") * N)
    ProductionLine.objects.filter(item=item).update(qty=F("qty") * N)
    from pos.models import OrderLine

    for ol in OrderLine.objects.filter(item=item):
        OrderLine.objects.filter(pk=ol.pk).update(qty=ol.qty * N, price=_q2(ol.price / N))
    ItemUnit.objects.filter(item=item).update(factor=F("factor") * N)
    item.qty_on_hand = item.qty_on_hand * N
    if item.reorder_level is not None:
        item.reorder_level = item.reorder_level * N
    if item.std_cost is not None:
        item.std_cost = (item.std_cost / N).quantize(Decimal("0.0001"))
    ItemUnit.objects.create(item=item, name=BOX, factor=N, barcode=item.barcode, sale_price=_q2(box_price) if box_price is not None else None)
    item.barcode = ""  # the box barcode now sells a box
    item.unit = STRIP
    item.sale_price = _q2(Decimal(str(strip_price))) if strip_price not in (None, "") else (_q2(box_price / N) if box_price is not None else None)
    item.save(update_fields=["qty_on_hand", "reorder_level", "std_cost", "barcode", "unit", "sale_price"])
    return item


@transaction.atomic
def sell_by_box(item: Item):
    """Back to boxes only (the reverse conversion)."""
    item = Item.objects.select_for_update().get(pk=item.pk)
    n, box = strip_info(item)
    if not n:
        return item
    N = Decimal(n)
    StockMoveLine.objects.filter(item=item).update(qty=F("qty") / N)
    for ln in StockMoveLine.objects.filter(item=item, unit_cost__isnull=False):
        StockMoveLine.objects.filter(pk=ln.pk).update(unit_cost=(ln.unit_cost * N).quantize(Decimal("0.0001")))
    StockQuant.objects.filter(item=item).update(qty=F("qty") / N)
    RecipeLine.objects.filter(ingredient=item).update(qty=F("qty") / N)
    ProductionLine.objects.filter(item=item).update(qty=F("qty") / N)
    from pos.models import OrderLine

    for ol in OrderLine.objects.filter(item=item):
        OrderLine.objects.filter(pk=ol.pk).update(qty=(ol.qty / N).quantize(Decimal("0.001")), price=_q2(ol.price * N))
    item.qty_on_hand = (item.qty_on_hand / N).quantize(Decimal("0.001"))
    if item.reorder_level is not None:
        item.reorder_level = (item.reorder_level / N).quantize(Decimal("0.001"))
    if item.std_cost is not None:
        item.std_cost = item.std_cost * N
    item.barcode, item.unit, item.sale_price = box.barcode, BOX, box.sale_price
    box.delete()
    ItemUnit.objects.filter(item=item).update(factor=F("factor") / N)
    item.save(update_fields=["qty_on_hand", "reorder_level", "std_cost", "barcode", "unit", "sale_price"])
    return item
