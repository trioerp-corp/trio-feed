"""Recipe costing: what one drink / dish costs from its ingredients, and the suggested selling price."""
import math
from decimal import ROUND_HALF_UP, Decimal

from .models import Item, StockMoveLine

Z = Decimal(0)


def last_purchase_cost(item):
    """The unit price of the newest purchase invoice (or opening balance) of an ingredient — None if never bought."""
    last = (StockMoveLine.objects.filter(item=item, move__kind__in=["receipt", "opening"], move__state="posted", unit_cost__isnull=False)
            .order_by("-move__date", "-move_id", "-id").values_list("unit_cost", flat=True).first())
    return Decimal(last) if last is not None else None


def unit_cost(item):
    """What one unit of an ingredient costs on a menu item: the last purchase invoice → the moving average → the estimate typed by hand."""
    last = last_purchase_cost(item)
    if last:
        return last
    if item.qty_on_hand > 0 and item.stock_value > 0:
        return (item.stock_value / item.qty_on_hand).quantize(Decimal("0.0001"))
    return item.std_cost or Z


def nice_price(x):
    """Round up to a price people use: to 0.5 under 10, to 1 under 50, to 5 above."""
    x = float(x)
    if x <= 0:
        return Z
    step = 0.5 if x < 10 else 1 if x < 50 else 5
    return Decimal(str(math.ceil(x / step - 1e-9) * step)).quantize(Decimal("0.01"))


def costing(recipe, vat_rate=Z, prices_include_vat=True):
    lines, total, missing = [], Z, []
    for ln in recipe.lines.select_related("ingredient"):
        c = unit_cost(ln.ingredient)
        q = ln.qty * (1 + ln.waste_pct / 100)
        v = (q * c).quantize(Decimal("0.01"), ROUND_HALF_UP)
        if not c:
            missing.append(ln.ingredient.name)
        lines.append(dict(line=ln, unit_cost=c, value=v))
        total += v
    cost = total + recipe.extra_cost
    margin = recipe.target_margin if recipe.target_margin < 100 else Decimal("99")
    net = cost / (1 - margin / 100) if cost else Z
    gross = net * (1 + vat_rate / 100) if prices_include_vat else net
    price = recipe.item.sale_price
    price_net = (price / (1 + vat_rate / 100) if prices_include_vat else price) if price else None
    real = ((price_net - cost) / price_net * 100).quantize(Decimal("0.1")) if price_net else None
    return dict(lines=lines, ingredients=total, cost=cost, suggested=nice_price(gross), suggested_exact=gross.quantize(Decimal("0.01")),
                price=price, real_margin=real, food_cost_pct=(cost / price_net * 100).quantize(Decimal("0.1")) if price_net else None,
                low=real is not None and real < margin, missing=missing)


def ingredients():
    return Item.objects.filter(active=True, item_type="stock").exclude(code="0").select_related("category").order_by("category__name", "name")
