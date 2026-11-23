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
# Lifetime is not sold online or on the site; Trio License can still issue one (kept as an option for the owner).


def year_total(branches=0):
    """What a year costs: the subscription + each extra branch."""
    return YEAR_PRICE + BRANCH_PRICE * max(0, int(branches or 0))
