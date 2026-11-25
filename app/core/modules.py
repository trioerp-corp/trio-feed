"""Trio: which parts of the program a client bought (pricing plan + extra modules), chosen by the developer at setup."""

# key: (Arabic name, short description, URL prefixes it opens)
MODULES = {
    "pos": ("نقطة البيع والكاشير", "شاشة البيع، الباركود، الطابعة الحرارية، الورديات وتقفيل اليوم", ("/pos/", "/ui/shop/close")),
    "items": ("الأصناف والمخزن", "الأصناف والأسعار والرصيد، جرد المخزن والتسوية، التالف والهالك", ("/ui/shop/items",)),
    "purchases": ("المشتريات والموردين", "فواتير شراء نقدًا أو آجلًا أو جزئيًا، وحسابات الموردين", ("/ui/shop/purchases",)),
    "partners": ("العملاء الآجل", "البيع الآجل وحد المديونية وكشف حساب العميل", ("/ui/shop/partners",)),
    "money": ("المصروفات والمرتبات وصاحب المحل", "مصاريف تشغيل، أجور وسلف، مسحوبات وإيداعات، سداد موردين", ("/ui/shop/money",)),
    "treasury": ("الخزينة والمحافظ والبنك", "سندات القبض والصرف وأرصدة الخزائن", ("/ui/vouchers",)),
    "stores": ("أكثر من مخزن وتحويلات", "المخزن الرئيسي والتشغيلي والتحويل بينهما", ("=/ui/moves/", "/ui/moves/new")),  # «=» exact page; opening stock/count and viewing an invoice stay open
    "returns": ("مرتجع المشتريات للمورد", "أرجِع بضاعة للمورد وتُخصم من حسابه", ("/ui/shop/returns",)),
    "promos": ("العروض والخصومات", "خصم نسبة/مبلغ، اشترِ واحصل، سعر جملة", ("/ui/shop/promos",)),
    "units": ("الوحدات (كرتونة/علبة)", "أكثر من وحدة للصنف بباركود وسعر", ("/ui/shop/units",)),
    "labels": ("ملصقات الباركود والميزان", "طباعة ملصقات وقراءة باركود الميزان", ("/ui/shop/labels",)),
    "expiry": ("الصلاحية (الإنتاج والانتهاء)", "تنبيه بالأصناف التي اقتربت صلاحيتها من الانتهاء لتُباع أولًا", ("/ui/shop/expiry",)),
    "valuation": ("تقييم المخزون", "قيمة المخزن بالتكلفة وبسعر البيع", ("/ui/shop/valuation",)),
    "production": ("التصنيع", "تحويل خامات إلى أصناف أخرى بالتكلفة", ("/ui/shop/production",)),
    "recipes": ("المنيو والمكونات", "أصناف المنيو ومكونات كل صنف، والتكلفة بسعر آخر فاتورة شراء، وخصم الخامات من المخزن مع كل طلب", ("/ui/shop/recipes",)),
    "gaming": ("صالة الألعاب بالساعة", "بلايستيشن/بلياردو: فتح جهاز بالساعة وطلبات على نفس الفاتورة", ("/ui/play",)),
    "clinic": ("العيادة", "ملف المريض والزيارات، والحجز والدور، والروشتة، وأكثر من طبيب بنسبة، وتُخصم المستهلكات من المخزن مع كل زيارة",
               ("/ui/clinic",)),
    "branches": ("أكثر من فرع", "كل فرع بمخزنه وخزينته ومديره، ويرى صاحب النشاط الفروع كلها وتقاريرها", ("/ui/shop/branches",)),
    "profit_monthly": ("تقرير الأرباح الشهري والشركاء", "ربح الشهر، ونصيب كل شريك، وأين ذهب الربح: مسحوبات، نقدية، بضاعة",
                       ("=/ui/shop/profit/", "/ui/shop/profit/partners")),
    "profit_yearly": ("تقرير الأرباح السنوي", "السنة كاملة شهرًا بشهر ونصيب كل شريك في السنة", ("/ui/shop/profit/year",)),
    "report_whatsapp": ("ملخص اليوم على واتساب", "زر في تقفيل اليوم يرسل الملخص إلى واتساب صاحب المحل", ()),
    "report_telegram": ("ملخص اليوم على تليجرام", "يُرسل تلقائيًا آخر اليوم إلى بوت تليجرام", ()),
    "report_email": ("ملخص اليوم على البريد", "يُرسل تلقائيًا آخر اليوم إلى بريد صاحب المحل", ()),
    "drive_backup": ("نسخة احتياطية على جوجل درايف", "نسخة أونلاين بجانب النسخة المحلية", ()),
    "mobile": ("تابع محلك من الهاتف", "رابط على هاتفك بأيقونة Trio: مبيعات اليوم والخزينة والنواقص وآخر الفواتير — يُحدَّث كل 10 دقائق", ()),
}

PLANS = {
    "basic": ("أساسي", ["pos", "items", "purchases", "partners", "money", "treasury", "profit_monthly", "expiry"]),
    "standard": ("متقدم", ["pos", "items", "purchases", "partners", "money", "treasury", "profit_monthly", "stores", "returns", "promos", "units", "labels",
                           "expiry", "valuation"]),
    "pro": ("شامل", list(MODULES)),
}

# what each business type gets on top of the plan (it is what the business is about)
BUSINESS = {
    "market": ("سوبر ماركت / بقالة", ["labels", "units"]),
    "retail": ("محل تجزئة (ملابس، أدوات، هواتف…)", []),
    "cafe": ("كافيه / مطعم", ["recipes"]),
    "gaming": ("بلايستيشن / بلياردو + كافيه", ["gaming", "recipes"]),
    "clinic": ("عيادة / مركز طبي", ["clinic"]),
}

ALWAYS = ("pos", "items", "profit_monthly", "expiry")
LATER = ("mobile",)  # parts added after copies were set up with an explicit list

# parts that only make sense for some business types: a clothes shop never sees the gaming hall or manufacturing,
# whatever the plan says (the «شامل» plan included)
FOR_BUSINESS = {
    "gaming": ("gaming",),
    "recipes": ("cafe", "gaming"),
    "production": ("market",),  # cafés / restaurants work with the menu (recipes) instead
    "clinic": ("clinic",),
    # a clinic has no barcode labels, pack units or shop promotions
    "labels": ("market", "retail", "cafe", "gaming"),
    "units": ("market", "retail", "cafe", "gaming"),
    "promos": ("market", "retail", "cafe", "gaming"),
}


def fits(key, business):
    return business in FOR_BUSINESS.get(key, (business,))


# 11/2026: no plans for now — every copy (trial or licensed) gets everything that fits its business. The plan / modules a
# licence carries are still saved (for when plans come back: set ALL_OPEN = False and the old rules apply again).
ALL_OPEN = True


def enabled(settings_obj=None):
    """The set of open modules. An empty list (copies set up before modules existed) means the plan's defaults."""
    from .models import CompanySettings

    s = settings_obj or CompanySettings.get()
    business = getattr(s, "business", "") or "market"
    if ALL_OPEN:
        return {m for m in MODULES if fits(m, business)}
    plan = getattr(s, "plan", "pro") or "pro"
    mods = set(s.modules) if s.modules else set(defaults(plan, business))
    if plan == "pro" and s.modules:  # a full «شامل» saved before a part existed gets the new part too
        full = {m for m in PLANS["pro"][1] if m not in LATER and fits(m, business)}
        if full <= mods:
            mods |= set(LATER)
    return {m for m in mods | set(ALWAYS) if m in MODULES and fits(m, business)}


def defaults(plan, business):
    mods = set(PLANS.get(plan, PLANS["pro"])[1]) | set(BUSINESS.get(business, ("", []))[1]) | set(ALWAYS)
    return sorted((m for m in mods if fits(m, business)), key=list(MODULES).index)


# 1.26: pages that belong to some businesses only. A copy shows ONLY its current business (CompanySettings.business): a clinic
# that was a café / hall before 1.23.1 keeps those stations, tables and menus in the database, but none of their pages opens
# (ModuleGuard sends the owner home with a message). Longest prefix wins; "/pos/api/" is checked by each API itself.
SHELF = ("market", "retail")
PAGES_FOR = {
    "/ui/play": ("gaming",),
    "/ui/clinic": ("clinic",),
    "/ui/shop/menu": ("cafe", "gaming"),
    "/ui/shop/recipes": ("cafe", "gaming"),
    "/ui/shop/tables": ("cafe",),
    "/ui/shop/kitchen": ("cafe",),
    "/ui/shop/delivery": ("market", "retail", "cafe"),
    "/ui/shop/platforms": ("market", "retail", "cafe"),
    "/ui/shop/labels": SHELF,
    "/ui/shop/units": SHELF,
    "/ui/shop/promos": SHELF,
    "/ui/shop/season": SHELF,
    "/ui/shop/variants": SHELF,
    "/ui/shop/pharmacy": SHELF,
    "/ui/shop/supply/returns": SHELF,
    "/ui/shop/supply/orders": ("market", "retail", "cafe", "clinic"),
    "/ui/shop/production": ("market",),
    "/pos/": ("market", "retail", "cafe", "gaming"),  # a clinic checks out at the reception, never at a till
}


def page_fits(path, s=None):
    """False when `path` is a page of another business than this copy's (see PAGES_FOR)."""
    if path.startswith("/pos/api/"):
        return True
    best = max((p for p in PAGES_FOR if path.startswith(p)), key=len, default=None)
    if best is None:
        return True
    from .models import CompanySettings

    s = s or CompanySettings.get()
    return (getattr(s, "business", "") or "market") in PAGES_FOR[best]


def module_for_path(path):
    for key, (_, _, prefixes) in MODULES.items():
        for pre in prefixes:
            if (path == pre[1:]) if pre.startswith("=") else path.startswith(pre):
                return key
    return None


# 11/2026 — ONE price: a yearly subscription for the whole program (every part that fits the shop's business, branches
# included), per shop copy (one licence). No plans, add-ons or lifetime for sale; licences issued for life before keep working.
# The Worker sells it at this price (tools/sync_worker_catalog.py writes it into worker.js; tests/test_pay checks they match).
YEAR_PRICE = 3000  # EGP a year, one branch included — new copy or renewal (a renewal extends from the current expiry)
BRANCH_PRICE = 500  # EGP a year for each extra branch (a quantity; renewed with the subscription; licence field «br»)
VISIT_PRICE = 1500  # optional on-site set-up and training visit, one-time
CUSTOM_PRICE = 1500  # optional custom requests — up to 3 changes, one-time
CUSTOM_CHANGES = 3

# 11/2026 — every Arab market sees the price in its own currency (core/prices.json, the ONE table the Worker and the site
# share; the EG row is the prices above). Paymob charges the fixed «egp» amounts; any country not listed → USD.
import json as _json  # noqa: E402
from pathlib import Path as _Path  # noqa: E402

PRICES = _json.loads((_Path(__file__).with_name("prices.json")).read_text(encoding="utf-8"))
MARKETS, COUNTRIES = PRICES["markets"], PRICES["countries"]
VISIT_GOVS = PRICES["visit_govs"]  # the on-site visit: Egypt, Greater Cairo only (القاهرة / الجيزة / القليوبية); elsewhere it is remote
DIAL = {"20": "EG", "966": "SA", "971": "AE", "974": "QA", "965": "KW", "973": "BH", "968": "OM", "962": "JO", "964": "IQ", "212": "MA", "213": "DZ",
        "216": "TN", "218": "LY", "961": "LB", "963": "SY", "970": "PS", "972": "PS", "967": "YE", "249": "SD", "222": "MR", "252": "SO", "253": "DJ", "269": "KM"}


def country_of(phone):
    """The copy's country from its phone (+966… / 00966…); a local number (01…) or none → Egypt."""
    d = "".join(c for c in str(phone or "") if c.isdigit())
    if str(phone or "").strip().startswith("+") or d.startswith("00"):
        d = d[2:] if d.startswith("00") else d
        for n in (3, 2):
            if d[:n] in DIAL:
                return DIAL[d[:n]]
        return "ZZ"
    return "EG"


def market(country="EG"):
    """(country, its market row) — the row has cur, ar/en symbols, year/branch/visit/custom (local) and egp (charged)."""
    c = (country or "EG").upper()
    return c, MARKETS[COUNTRIES.get(c, [0, 0, "USD"])[2]]


def money(n, mk, lang="ar"):
    """«899 ر.س» / «$199» / «3,000 ج.م»."""
    s = mk["ar" if lang == "ar" else "en"]
    return f"${n:,}" if s == "$" else f"{n:,} {s}"


def shop_prices(s=None, branches=0):
    """What the renewal / upgrade pages show, in the copy's currency (its phone's country; else Egypt)."""
    cc, mk = market(country_of(getattr(s, "phone", "")))
    br = max(0, int(branches or 0))
    return dict(country=cc, cur=mk["cur"], year_price=money(mk["year"] + mk["branch"] * br, mk), year_one=money(mk["year"], mk),
                branch_price=money(mk["branch"], mk), visit_price=money(mk["visit"], mk) if "visit" in mk else "", visit_on=cc == "EG" and "visit" in mk, custom_price=money(mk["custom"], mk),
                egp_note="" if cc == "EG" else f"يُحصَّل المبلغ بالجنيه المصري بما يعادل ≈ {mk['egp']['year'] + mk['egp']['branch'] * br:,} ج.م عبر بوابة الدفع")
# Lifetime is not sold online or on the site; Trio License can still issue one (kept as an option for the owner).


def year_total(branches=0):
    """What a year costs: the subscription + each extra branch."""
    return YEAR_PRICE + BRANCH_PRICE * max(0, int(branches or 0))
