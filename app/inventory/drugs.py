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
# The library has the trade name in Arabic but the active ingredient in English only: the common ingredients in Arabic, as the
# pharmacist and the customer say them, so «باراسيتامول» / «ديكلوفيناك» find the drugs (saved on the item too).
GENERIC_AR = {
    "PARACETAMOL": "باراسيتامول", "ACETAMINOPHEN": "باراسيتامول", "IBUPROFEN": "ايبوبروفين", "DICLOFENAC": "ديكلوفيناك", "KETOPROFEN": "كيتوبروفين",
    "NAPROXEN": "نابروكسين", "MELOXICAM": "ميلوكسيكام", "PIROXICAM": "بيروكسيكام", "CELECOXIB": "سيليكوكسيب", "ETORICOXIB": "ايتوريكوكسيب",
    "ACETYLSALICYLIC": "اسبرين", "ASPIRIN": "اسبرين", "TRAMADOL": "ترامادول", "ORPHENADRINE": "اورفينادرين", "CAFFEINE": "كافيين",
    "AMOXICILLIN": "اموكسيسيلين", "CLAVULANIC": "كلافيولانيك", "AMPICILLIN": "امبيسيلين", "AZITHROMYCIN": "ازيثرومايسين",
    "CLARITHROMYCIN": "كلاريثرومايسين", "CIPROFLOXACIN": "سيبروفلوكساسين", "LEVOFLOXACIN": "ليفوفلوكساسين", "CEFTRIAXONE": "سيفترياكسون",
    "CEFIXIME": "سيفيكسيم", "CEFUROXIME": "سيفوروكسيم", "CEPHALEXIN": "سيفالكسين", "CEFADROXIL": "سيفادروكسيل", "DOXYCYCLINE": "دوكسيسيكلين",
    "METRONIDAZOLE": "ميترونيدازول", "NITAZOXANIDE": "نيتازوكسانيد", "FLUCONAZOLE": "فلوكونازول", "MICONAZOLE": "ميكونازول",
    "CLOTRIMAZOLE": "كلوتريمازول", "FUSIDIC": "فيوسيديك", "MUPIROCIN": "ميوبيروسين", "GENTAMICIN": "جنتاميسين", "ACYCLOVIR": "اسيكلوفير",
    "OMEPRAZOLE": "اوميبرازول", "ESOMEPRAZOLE": "ايسوميبرازول", "PANTOPRAZOLE": "بانتوبرازول", "LANSOPRAZOLE": "لانسوبرازول",
    "RABEPRAZOLE": "رابيبرازول", "FAMOTIDINE": "فاموتيدين", "RANITIDINE": "رانيتيدين", "DOMPERIDONE": "دومبيريدون",
    "METOCLOPRAMIDE": "ميتوكلوبراميد", "HYOSCINE": "هيوسين", "MEBEVERINE": "ميبيفرين", "DROTAVERINE": "دروتافيرين", "SIMETHICONE": "سيميثيكون",
    "LOPERAMIDE": "لوبيراميد", "LACTULOSE": "لاكتيولوز", "BISACODYL": "بيساكوديل", "ONDANSETRON": "اوندانسيترون",
    "CHLORPHENIRAMINE": "كلورفينيرامين", "PSEUDOEPHEDRINE": "سودوافيدرين", "LORATADINE": "لوراتادين", "DESLORATADINE": "ديسلوراتادين",
    "CETIRIZINE": "سيتريزين", "LEVOCETIRIZINE": "ليفوسيتريزين", "FEXOFENADINE": "فيكسوفينادين", "MONTELUKAST": "مونتيلوكاست",
    "SALBUTAMOL": "سالبيوتامول", "AMBROXOL": "امبروكسول", "BROMHEXINE": "برومهيكسين", "GUAIFENESIN": "جوايفينيسين",
    "DEXTROMETHORPHAN": "ديكستروميثورفان", "BUDESONIDE": "بوديزونيد", "PREDNISOLONE": "بريدنيزولون", "DEXAMETHASONE": "ديكساميثازون",
    "BETAMETHASONE": "بيتاميثازون", "HYDROCORTISONE": "هيدروكورتيزون", "METFORMIN": "ميتفورمين", "GLIMEPIRIDE": "جليميبيريد",
    "GLICLAZIDE": "جليكلازيد", "SITAGLIPTIN": "سيتاجليبتين", "VILDAGLIPTIN": "فيلداجليبتين", "EMPAGLIFLOZIN": "امباجليفلوزين",
    "DAPAGLIFLOZIN": "داباجليفلوزين", "INSULIN": "انسولين", "AMLODIPINE": "املوديبين", "BISOPROLOL": "بيسوبرولول", "ATENOLOL": "اتينولول",
    "PROPRANOLOL": "بروبرانولول", "CARVEDILOL": "كارفيديلول", "VALSARTAN": "فالسارتان", "LOSARTAN": "لوسارتان", "CANDESARTAN": "كانديسارتان",
    "TELMISARTAN": "تلميسارتان", "CAPTOPRIL": "كابتوبريل", "ENALAPRIL": "اينالابريل", "LISINOPRIL": "ليزينوبريل", "RAMIPRIL": "راميبريل",
    "HYDROCHLOROTHIAZIDE": "هيدروكلوروثيازيد", "FUROSEMIDE": "فوروسيميد", "SPIRONOLACTONE": "سبيرونولاكتون", "INDAPAMIDE": "انداباميد",
    "ATORVASTATIN": "اتورفاستاتين", "ROSUVASTATIN": "روزوفاستاتين", "SIMVASTATIN": "سيمفاستاتين", "EZETIMIBE": "ايزيتيميب",
    "CLOPIDOGREL": "كلوبيدوجريل", "WARFARIN": "وارفارين", "RIVAROXABAN": "ريفاروكسابان", "APIXABAN": "ابيكسابان",
    "LEVOTHYROXINE": "ليفوثيروكسين", "CARBIMAZOLE": "كاربيمازول", "PREGABALIN": "بريجابالين", "GABAPENTIN": "جابابنتين",
    "CARBAMAZEPINE": "كاربامازيبين", "VALPROATE": "فالبروات", "LEVETIRACETAM": "ليفيتيراسيتام", "SERTRALINE": "سيرترالين",
    "ESCITALOPRAM": "اسيتالوبرام", "FLUOXETINE": "فلوكستين", "PAROXETINE": "باروكستين", "OLANZAPINE": "اولانزابين",
    "SILDENAFIL": "سيلدينافيل", "TADALAFIL": "تادالافيل", "TAMSULOSIN": "تامسولوسين", "FINASTERIDE": "فيناسترايد", "ALFUZOSIN": "الفوزوسين",
    "FOLIC ACID": "حمض الفوليك", "VITAMIN C": "فيتامين سي", "ASCORBIC": "فيتامين سي", "VITAMIN D": "فيتامين د", "CHOLECALCIFEROL": "فيتامين د",
    "VITAMIN B12": "فيتامين ب12", "CYANOCOBALAMIN": "فيتامين ب12", "VITAMIN E": "فيتامين ه", "IRON": "حديد", "FERROUS": "حديد", "CALCIUM": "كالسيوم",
    "MAGNESIUM": "ماغنسيوم", "ZINC": "زنك", "OMEGA": "اوميجا", "LIDOCAINE": "ليدوكايين", "CHLORHEXIDINE": "كلورهيكسيدين",
    "XYLOMETAZOLINE": "زيلوميتازولين", "OXYMETAZOLINE": "اوكسيميتازولين", "TOBRAMYCIN": "توبرامايسين", "NAPHAZOLINE": "نافازولين",
    "ORLISTAT": "اورليستات", "ALLOPURINOL": "الوبيورينول", "COLCHICINE": "كولشيسين", "FEBUXOSTAT": "فيبوكسوستات",
}


def generic_ar(sci):
    """The active ingredient(s) in Arabic, from GENERIC_AR («PARACETAMOL+CAFFEINE» → «باراسيتامول + كافيين»), '' when unknown."""
    s, out = str(sci or "").upper(), []
    for k, v in GENERIC_AR.items():
        if re.search(r"(?<![A-Z])" + re.escape(k), s) and v not in out:
            out.append(v)
    return " + ".join(out)


def norm(t):
    t = re.sub(r"[ً-ْـ]", "", str(t or "").lower()).translate(_AR)
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s%]", " ", t)).strip()


_UNIT_AR = {"MG": "مجم", "MGM": "مجم", "GM": "جم", "G": "جم", "GRAM": "جم", "MCG": "ميكروجرام", "ML": "مل", "IU": "وحدة", "%": "%"}
_FORM_AR = [("F.C.TAB", "أقراص"), ("TAB", "أقراص"), ("CAP", "كبسولات"), ("SUSP", "معلق"), ("SYRUP", "شراب"), ("SYR", "شراب"), ("VIAL", "فيال"),
            ("AMP", "أمبولات"), ("CREAM", "كريم"), ("OINT", "مرهم"), ("GEL", "جل"), ("DROP", "نقط"), ("SACHET", "أكياس"), ("SUPP", "لبوس"),
            ("SPRAY", "بخاخ"), ("INHAL", "بخاخ"), ("EFF", "فوار"), ("LOTION", "لوشن"), ("SHAMPOO", "شامبو")]
_STRENGTH = re.compile(r"(\d+(?:\.\d+)?)\s*(MCG|MGM|MG|GM|GRAM|G|ML|IU|%)(?![A-Z])(?:\s*/\s*(\d+(?:\.\d+)?)?\s*(MCG|MG|GM|G|ML)(?![A-Z]))?")


def ar_name(en, ar):
    """The Arabic trade name with the strength and form of the English one («أوجمينتين /» + «AUGMENTIN 625 MG 10 F.C.TABS.» →
    «أوجمينتين 625 مجم أقراص») — the library's Arabic names lost their numbers, so 375 / 625 / 1 جم looked the same."""
    ar = " ".join(t for t in (ar or "").split() if not re.fullmatch(r"[/.\-]+\S*|\d*", t)).strip(" ./-")
    if not ar:
        return ""
    up = (en or "").upper()
    m = _STRENGTH.search(up)
    bits = []
    if m:
        st = f"{m.group(1)} {_UNIT_AR.get(m.group(2), m.group(2))}"
        if m.group(4):
            st += "/" + (f"{m.group(3)} " if m.group(3) else "") + _UNIT_AR.get(m.group(4), m.group(4))
        bits.append(st.replace(" %", "%"))
    form = next((f for k, f in _FORM_AR if k in up), "")
    if form and form not in ar:
        bits.append(form)
    return " ".join([ar] + bits)


@lru_cache(maxsize=1)
def library():
    if not FILE.exists():
        return []
    with gzip.open(FILE, "rt", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    rows = [r for r in rows if "N/A" not in r["en"].upper()]  # not on the market (yet): never offered
    for i, r in enumerate(rows):
        r["id"] = i
        r["sci_ar"] = generic_ar(r["sci"])
        r["ar"] = ar_name(r["en"], r["ar"])
        r["_s"] = " " + norm(" ".join([r["en"], r["ar"], r["sci"], r["sci_ar"], r["company"]])) + " "
        r["_k"] = _skel(r["_s"])
    return rows


@lru_cache(maxsize=1)
def count():
    """How many drugs the library holds — for the pharmacy page's heading without building the whole search index
    (library() normalises every row: seconds on a slow PC, so the page opens before anything is searched)."""
    if library.cache_info().currsize:
        return len(library())
    if not FILE.exists():
        return 0
    with gzip.open(FILE, "rt", encoding="utf-8") as f:
        return sum(1 for r in csv.DictReader(f) if "N/A" not in r["en"].upper())


def _skel(t):
    """Spelling-tolerant form: Arabic long vowels and «ه» dropped (كونجستال = كونجيستال), doubled letters single."""
    t = re.sub(r"[اويه]", "", t)
    return re.sub(r"(.)\1+", r"\1", t)


def count_in_pack(en):
    """How many tablets / capsules the pack holds, from its English name («AUGMENTIN 1 GM 14 F.C. TABS.» → 14)."""
    # the count, then up to four describing words («F.C.», «SUGAR C.», «DISCMELT ORALLY DIS.»…), then the dosage form; a strength
    # («25 MG») is never the count because a describing word cannot start with a unit or hold a digit
    m = re.search(r"(?<![\d,/])(?<!\d\.)(\d{1,3})\s*(?!(?:MG|MCG|GM|G|ML|IU|MIU|KG|MMOL)\b|%)(?:[A-Z][A-Z.\-]*\s*){0,4}?(?<![A-Z])(?:TAB(?:LET|LE)?S?|TAPS|CAP(?:SULE?)?S?|PILLS?|SACHETS?|"
                  r"CAPLETS?|LOZENGES?)(?![A-Z])", en.upper())
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
    out.sort(key=lambda r: (_rank(r, q, words[0]), len(r["en"]), r["en"]))
    return out[:limit]


def _rank(r, q, w):
    """0 = the trade name is what was typed («بروفين» → BRUFEN), 1 = it starts with it, 2 = a word starts with it, 3 = anywhere
    (MEPABRUFEN), 4 / 5 = found by the spelling-tolerant form only (the name starting with it first)."""
    ar, en = norm(r["ar"]), norm(r["en"])
    first = {(ar.split() or [""])[0], (en.split() or [""])[0]}
    if q in first or w in first:
        return 0
    if ar.startswith(q) or en.startswith(q):
        return 1
    if (" " + w) in r["_s"]:
        return 2
    if all(x in r["_s"] for x in q.split()):
        return 3
    return 4 if _skel(ar).startswith(_skel(w)) or _skel(en).startswith(_skel(w)) else 5


def get(ids):
    lib = library()
    return [lib[i] for i in ids if 0 <= i < len(lib)]


def view(r):
    """What the page shows for one drug."""
    from inventory.models import Item

    strips = guess_strips(r["en"], r["route"])
    price = Decimal(r["price"]) if r["price"] else None
    return dict(id=r["id"], en=r["en"], ar=r["ar"], sci=r["sci"], sci_ar=r.get("sci_ar", ""), company=r["company"], cls=r["cls"], route=ROUTES.get(r["route"], ""),
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
    if price is None or price <= 0:  # never a drug at no price on the till: the pharmacist writes it
        raise ValidationError(f"اكتب سعر بيع العلبة لـ «{r['en']}» — لا يوجد له سعر في المكتبة")
    sci_ar = r.get("sci_ar") or generic_ar(r["sci"])
    it = Item.objects.create(code=_next_code(), name=r["en"][:150], category=_category(r), unit="علبة", sale_price=price, vat_rate=0,
                             aliases=" ".join(x for x in (r["ar"], sci_ar) if x),  # Arabic trade name + Arabic generic: the till finds both
                             extra=dict({"generic": r["sci"][:120], "company": r["company"][:80]}, **({"generic_ar": sci_ar[:120]} if sci_ar else {})),
                             reorder_level=Decimal(str(reorder_boxes)) if reorder_boxes not in (None, "") else Decimal(1))  # in boxes; ×strips below
    n = guess_strips(r["en"], r["route"]) if strips in (None, "") else int(strips)
    if n and n > 1:
        it = sell_by_strip(it, n, box_price=price)
    return it, True
