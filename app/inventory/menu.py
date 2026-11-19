"""The restaurant / café menu (1.22), in its simplest form: a product = name + price (or sizes, each with its price) +
a description the customer sees on the QR menu. Ingredients, stock and cost are optional (the recipes page).

- Sizes: the product is one item on the menu and at the till; every size is its own item (variant_of = the product) so
  sales, recipes and reports stay per size. The product itself has no price and is never sold on its own.
- Add-ons: items with is_addon, sold on the order line they belong to (extra cheese…), each with its price; they show
  with the categories chosen (none = every product).
"""
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction

from .models import Item, ItemCategory

MAX_SIZES = 6
SIZE_HINTS = ("صغير", "وسط", "كبير", "عائلي")


def _price(v, what):
    txt = str(v or "").strip().replace(",", "").translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    try:
        p = Decimal(txt)
    except InvalidOperation:
        raise ValidationError(f"اكتب سعر {what} بالأرقام")
    if p <= 0 or p > Decimal("1000000"):
        raise ValidationError(f"سعر {what} غير صحيح")
    return p.quantize(Decimal("0.01"))


def _code():
    last = Item.objects.filter(code__regex=r"^\d+$").order_by("-id").first()
    code = int(last.code) + 1 if last else 1001
    while Item.objects.filter(code=str(code)).exists():
        code += 1
    return str(code)


def category(cat_id=None, new_name=""):
    """The chosen category, or a new one by name (with the stock account of the first sales category)."""
    new_name = (new_name or "").strip()[:80]
    if new_name:
        c = ItemCategory.objects.filter(name=new_name).first()
        if c:
            return c
        base = ItemCategory.objects.exclude(name__startswith="خامات").order_by("pk").first() or ItemCategory.objects.order_by("pk").first()
        return ItemCategory.objects.create(name=new_name, stock_account=base.stock_account)
    c = ItemCategory.objects.filter(pk=cat_id or 0).first()
    if not c:
        raise ValidationError("اختر التصنيف أو اكتب تصنيفًا جديدًا")
    return c


def clean_sizes(rows):
    """[(size name, price)] from the form rows → checked list (names unique, at most MAX_SIZES)."""
    out, seen = [], set()
    for name, price in rows:
        name = (name or "").strip()[:40]
        if not str(price or "").strip():  # a size without a price is not sold (the form suggests صغير / وسط / كبير)
            continue
        if not name:
            raise ValidationError("اكتب اسم الحجم (صغير، وسط، كبير…)")
        if name in seen:
            raise ValidationError(f"الحجم «{name}» مكرر")
        seen.add(name)
        out.append((name, _price(price, f"الحجم «{name}»")))
    if len(out) > MAX_SIZES:
        raise ValidationError(f"أقصى عدد للأحجام {MAX_SIZES}")
    return out


@transaction.atomic
def save_product(name, cat, price=None, sizes=(), desc="", item=None):
    """Adds or edits a menu product. With sizes: the product keeps no price and each size is an item of its own
    (sizes no longer listed are stopped, never deleted, because they have sales)."""
    name = (name or "").strip()[:120]
    if not name:
        raise ValidationError("اكتب اسم المنتج")
    sizes = clean_sizes(sizes)
    if len(sizes) == 1:
        raise ValidationError("المنتج ذو الحجم الواحد يُكتب له سعر واحد بدون أحجام")
    dup = Item.objects.filter(name=name, active=True, variant_of__isnull=True)
    if item:
        dup = dup.exclude(pk=item.pk)
    if dup.exists():
        raise ValidationError(f"يوجد منتج باسم «{name}» بالفعل")
    if item is None:
        item = Item(code=_code(), unit="واحدة", item_type="recipe")
    elif item.variant_of_id or item.is_addon:
        raise ValidationError("هذا ليس منتجًا من المنيو")
    item.name, item.category, item.active = name, cat, True
    item.extra = dict(item.extra or {}, desc=(desc or "").strip()[:200])
    if item.item_type == "stock" and not sizes:  # a stock item (a can of soda) keeps its stock
        pass
    elif item.item_type == "stock":
        raise ValidationError("الصنف المخزني لا تكون له أحجام — أضفه منتجًا جديدًا")
    item.sale_price = None if sizes else _price(price, "المنتج")
    item.save()
    old = {v.size_name: v for v in item.sizes.all()} if item.pk else {}
    keep = set()
    for n, (size, p) in enumerate(sizes):
        v = old.get(size) or Item(code=_code(), unit="واحدة", item_type="recipe", variant_of=item, size_name=size)
        v.name, v.category, v.sale_price, v.active = f"{name} — {size}"[:150], cat, p, True
        v.extra = dict(v.extra or {}, order=n)
        v.save()
        keep.add(v.pk)
    item.sizes.exclude(pk__in=keep).update(active=False)
    return item


@transaction.atomic
def save_addon(name, price, cats=(), item=None):
    name = (name or "").strip()[:100]
    if not name:
        raise ValidationError("اكتب اسم الإضافة")
    dup = Item.objects.filter(name=name, active=True, is_addon=True)
    if item:
        dup = dup.exclude(pk=item.pk)
    if dup.exists():
        raise ValidationError(f"توجد إضافة باسم «{name}» بالفعل")
    if item is None:
        base = ItemCategory.objects.filter(name="إضافات").first() or category(new_name="إضافات")
        item = Item(code=_code(), unit="واحدة", item_type="recipe", is_addon=True, category=base)
    elif not item.is_addon:
        raise ValidationError("هذه ليست إضافة")
    item.name, item.sale_price, item.active = name, _price(price, "الإضافة"), True
    item.save()
    item.addon_for.set(ItemCategory.objects.filter(pk__in=[c for c in cats if str(c).isdigit()]))
    return item


def stop(item):
    """Off the menu (history stays): the product with all its sizes, or one add-on."""
    item.active = False
    item.save(update_fields=["active"])
    item.sizes.update(active=False)


def sizes_of(item):
    return sorted(item.sizes.filter(active=True), key=lambda v: (v.extra or {}).get("order", 0))


def products():
    """The menu as the owner sees it: products (with their sizes) by category, then the add-ons."""
    from .models import Item as I

    rows = []
    qs = (I.objects.filter(active=True, variant_of__isnull=True, is_addon=False).exclude(code="0")
          .exclude(category__name__startswith="خامات").exclude(category__name__startswith="مستلزمات")
          .filter(item_type__in=("recipe", "stock", "service")).select_related("category").order_by("category__name", "name"))
    sized = {}
    for v in I.objects.filter(active=True, variant_of__isnull=False):
        sized.setdefault(v.variant_of_id, []).append(v)
    for it in qs:
        if it.sale_price is None and it.pk not in sized:
            continue
        sz = sorted(sized.get(it.pk, []), key=lambda v: (v.extra or {}).get("order", 0))
        rows.append(dict(item=it, sizes=sz, desc=(it.extra or {}).get("desc", "")))
    addons = list(I.objects.filter(active=True, is_addon=True).prefetch_related("addon_for").order_by("name"))
    return rows, addons
