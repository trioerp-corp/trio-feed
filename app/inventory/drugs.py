"""The pharmacy's drug library: ~25,000 medicines registered in Egypt (trade name in English and Arabic, active ingredient,
company, class, route, reference price) — core/data/eg_drugs.csv.gz, from the public-domain (CC0) Egyptian drug database.

Searched offline. A drug becomes an item of the pharmacy only when the pharmacist adds it (or buys it), so the cashier's list
stays the pharmacy's own. Tablets / capsules are added sold by the strip when the pack has more than one strip."""
import csv
import gzip
import re
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from django.core.exceptions import ValidationError
from django.db import transaction

FILE = Path(__file__).resolve().parent.parent / "core" / "data" / "eg_drugs.csv.gz"
ROUTES = {"ORAL.SOLID": "أقراص وكبسولات", "ORAL.LIQUID": "شراب ونقط", "TOPICAL": "كريمات ومراهم", "INJECTION": "حقن", "EFF": "فوار",
          "SPRAY": "بخاخ", "EYE": "قطرة عين", "EAR": "قطرة أذن", "VAGINAL": "لبوس مهبلي", "RECTAL": "لبوس", "MOUTH": "فم وأسنان", "SOAP": "صابون"}
_AR = str.maketrans("أإآٱةىؤئ", "اااايويي")


def norm(t):
    t = re.sub(r"[ً-ْـ]", "", str(t or "").lower()).translate(_AR)
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s%]", " ", t)).strip()


@lru_cache(maxsize=1)
def library():
    if not FILE.exists():
        return []
    with gzip.open(FILE, "rt", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for i, r in enumerate(rows):
        r["id"] = i
        r["_s"] = " " + norm(" ".join([r["en"], r["ar"], r["sci"], r["company"]])) + " "
        r["_k"] = _skel(r["_s"])
    return rows


def _skel(t):
    """Spelling-tolerant form: Arabic long vowels and «ه» dropped (كونجستال = كونجيستال), doubled letters single."""
    t = re.sub(r"[اويه]", "", t)
    return re.sub(r"(.)\1+", r"\1", t)


def count_in_pack(en):
    """How many tablets / capsules the pack holds, from its English name («AUGMENTIN 1 GM 14 F.C. TABS.» → 14)."""
    m = re.search(r"(\d{1,3})\s*(?:(?:F\.?\s*C\.?|S\.?R\.?|E\.?C\.?|M\.?R\.?|X\.?R\.?|CHEW(?:ABLE)?\.?|ORODISP(?:ERSIBLE)?\.?|DISP\.?|"
                  r"E\.?R\.?|P\.?R\.?|COATED|FILM|HARD|SOFT|GELATIN|VAG(?:INAL)?\.?|EFF(?:ERVESCENT)?\.?|SUBLINGUAL)\s*)*(?:TAB|CAP|PILL|SACHET)", en.upper())
    return int(m.group(1)) if m else None


def guess_strips(en, route=""):
    """Strips in a box (pharmacist can change it): 30 → 3 of 10, 14 / 21 / 28 → of 7, 24 / 48 → of 12…; 1 = sold by the box only."""
    if route and route not in ("ORAL.SOLID", "UNKNOWN", "VAGINAL", "RECTAL", "MOUTH"):
        return 1
    n = count_in_pack(en)
    if not n or n < 10:
        return 1
    for size in (10, 7, 12, 14, 8, 6):
        if n % size == 0 and n // size >= 2:
            return n // size
    return 1


def search(q, limit=60):
    q = norm(q)
    if len(q) < 2:
        return []
    words = q.split()
    lib = library()
    out = [r for r in lib if all(w in r["_s"] for w in words)]
    if not out:  # Arabic spelled another way
        sk = [_skel(w) for w in words if _skel(w)]
        out = [r for r in lib if sk and all(w in r["_k"] for w in sk)]
    out.sort(key=lambda r: (not r["_s"].startswith(" " + words[0]), not norm(r["en"]).startswith(words[0]), len(r["en"])))
    return out[:limit]


def get(ids):
    lib = library()
    return [lib[i] for i in ids if 0 <= i < len(lib)]


def view(r):
    """What the page shows for one drug."""
    from inventory.models import Item

    strips = guess_strips(r["en"], r["route"])
    price = Decimal(r["price"]) if r["price"] else None
    return dict(id=r["id"], en=r["en"], ar=r["ar"], sci=r["sci"], company=r["company"], cls=r["cls"], route=ROUTES.get(r["route"], ""),
                price=price, strips=strips, strip_price=(price / strips).quantize(Decimal("0.01")) if price and strips > 1 else None,
                have=Item.objects.filter(name=r["en"]).values_list("pk", flat=True).first())


def _category(r):
    from inventory.models import ItemCategory

    name = "أدوية"
    cat = ItemCategory.objects.filter(name=name).first()
    if cat:
        return cat
    base = ItemCategory.objects.filter(name="أخرى").first() or ItemCategory.objects.first()
    if not base:
        raise ValidationError("لا توجد تصنيفات أصناف — جهّز المحل أولًا")
    return ItemCategory.objects.create(name=name, stock_account=base.stock_account, farm_account=base.farm_account,
                                       admin_account=base.admin_account, needs_equipment=False)


def _next_code():
    from inventory.models import Item

    last = Item.objects.filter(code__regex=r"^D\d+$").order_by("-id").first()
    n = int(last.code[1:]) + 1 if last else 10001
    while Item.objects.filter(code=f"D{n}").exists():
        n += 1
    return f"D{n}"


@transaction.atomic
def add(r, *, box_price=None, strips=None, reorder_boxes=None):
    """The drug as an item of the pharmacy: name = the English trade name (what is on the box), Arabic name as a search alias,
    active ingredient + company in the item's pharmacy fields, then sold by the strip when the box holds several."""
    from inventory.models import Item

    from .units import sell_by_strip

    it = Item.objects.filter(name=r["en"]).first()
    if it:
        return it, False
    price = Decimal(str(box_price)) if box_price not in (None, "") else (Decimal(r["price"]) if r["price"] else None)
    it = Item.objects.create(code=_next_code(), name=r["en"][:150], category=_category(r), unit="علبة", sale_price=price, vat_rate=0,
                             aliases=r["ar"], extra={"generic": r["sci"][:120], "company": r["company"][:80]},
                             reorder_level=Decimal(str(reorder_boxes)) if reorder_boxes not in (None, "") else Decimal(1))  # in boxes; ×strips below
    n = guess_strips(r["en"], r["route"]) if strips in (None, "") else int(strips)
    if n and n > 1:
        it = sell_by_strip(it, n, box_price=price)
    return it, True
