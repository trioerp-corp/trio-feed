"""«تجهيز المحل» (1.21): everything the program needs to do every job, asked once at the start — never «you should have
done X first» in the middle of a sale. The home page shows what is still missing until it is done (or skipped)."""
from django.urls import reverse


def _skipped(s, key):
    return key in (s.onboarding or {}).get("skip", [])


def checklist(s=None):
    """[dict(key, title, done, hint)] for this shop's business and plan."""
    from inventory.models import Item
    from ledger.models import Partner

    from .models import CompanySettings
    from .modules import enabled

    s = s or CompanySettings.get()
    mods = enabled(s)
    ob = s.onboarding or {}
    rows = []

    def add(key, title, done, hint=""):
        rows.append(dict(key=key, title=title, done=bool(done) or _skipped(s, key), hint=hint, skipped=_skipped(s, key) and not done))

    cl = s.business == "clinic"
    add("shop", f"بيانات {'العيادة' if cl else 'المحل'} على الإيصال (الهاتف والعنوان)", s.phone and s.address)
    add("tax", "الملف الضريبي (النظام الضريبي والقيمة المضافة)", ob.get("tax"))
    add("money", "طرق الدفع التي تقبلها ورصيد الدرج في البداية", ob.get("money"))
    if "clinic" in mods:
        from django.apps import apps

        Doctor, Service = apps.get_model("clinic", "Doctor"), apps.get_model("clinic", "Service")
        add("doctors", "الأطباء ونسبهم", Doctor.objects.filter(active=True).exists())
        add("services", "الخدمات وأسعارها (كشف، استشارة، جلسة…)", Service.objects.filter(item__active=True).exists())
    if "gaming" in mods:
        from django.apps import apps

        add("stations", "أجهزة الصالة وسعر الساعة", apps.get_model("gaming", "Station").objects.filter(active=True).exists())
    if s.business == "cafe":  # a café / restaurant: its menu, its tables, its receipt and the kitchen's ticket
        from inventory import menu as MN
        from pos.models import DiningTable

        add("menu", "المنيو (المنتجات وأسعارها وأحجامها)", MN.products()[0], "من صفحة المنيو: الاسم والسعر أو الأحجام فقط")
        add("tables", "الترابيزات وبطاقات QR", DiningTable.objects.filter(active=True).exists(), "تخطَّ إن كان البيع تيك أواي فقط")
        add("receipt", "شعار المحل وشكل الإيصال", s.logo_svg or ob.get("receipt"))
        add("kitchen", "ورقة المطبخ (تُطبع مع كل طلب)", s.kitchen_ticket != "off" or ob.get("kitchen"))
    else:
        sellable = Item.objects.filter(active=True, sale_price__isnull=False).exclude(code="0")
        add("items", "المستلزمات وأسعارها" if cl else "الأصناف وأسعار البيع", sellable.exists(), "أضفهم واحدًا تلو الآخر أو ارفع ملف إكسل مرة واحدة")
    if stock_step(s):  # 1.25 the goods already on the shelves, with their purchase price — one opening move
        from inventory.models import StockMove

        add("stock", "رصيد البضاعة الموجودة لديك الآن", ob.get("stock") or StockMove.objects.filter(kind="opening", state="posted").exists(),
            "الكمية وسعر الشراء لكل صنف — مرة واحدة")
    no_limit = Item.objects.filter(active=True, item_type="stock", reorder_level__isnull=True).exclude(code="0").count()
    if s.business != "cafe" or no_limit:  # a menu holds no stock; only the café's stock items (cans) need it
        from .templatetags.ar import count_ar

        left = f" (ناقص في {count_ar(no_limit, 'صنف')})" if no_limit else ""
        add("reorder", f"حد الطلب لكل {'مستلزم' if cl else 'صنف'}{left}", not no_limit)
    if "purchases" in mods:
        add("suppliers", "الموردون الذين تشتري منهم", Partner.objects.filter(kind="supplier", active=True).exists())
    if "partners" in mods:
        add("customers", "المرضى بالآجل (إن كان لديك مرضى بالآجل)" if cl else "عملاء الآجل (إن كنت تبيع بالآجل)", Partner.objects.filter(kind="customer", active=True).exists())
    add("employees", "الموظفين ومرتباتهم", _employees_done(), "من أجل المرتبات والسلف وضريبة كسب العمل والتأمينات")
    from pos import delivery as DL  # 1.26 optional steps: never on the home card as «missing»

    if s.business in DL.BIZ:
        add("delivery", "الدليفري (اختياري)", DL.raw_cfg(s).get("on") or ob.get("delivery"), "رسم التوصيل وعمال التوصيل")
        rows[-1]["opt"] = True
        from pos.credit import platforms as PF

        if PF.available(s):
            add("platforms", "ربط منصات الطلبات (اختياري)", any(c["link"] for c in PF.cfg(s)) or ob.get("platforms"),
                "رابط محلك على طلبات / المنيوز ونسبة العمولة")
            rows[-1]["opt"] = True
    return rows


def stock_step(s):
    """Shops that keep goods: every market / retail shop, and any other business once it has stock items (a hall's buffet,
    a café's cans, a clinic's consumables)."""
    from inventory.models import Item

    if s.business in ("market", "retail"):
        return True
    return Item.objects.filter(active=True, item_type="stock").exclude(code="0").exists()


def _employees_done():
    from django.apps import apps

    return apps.get_model("staff", "Employee").objects.exists()


def missing(s=None):
    return [r for r in checklist(s) if not r["done"] and not r.get("opt")]


def card(request):
    """For the owner's home page: what is left, or None when all is ready."""
    from pos.voids import is_head

    if not is_head(request.user):  # a branch manager does not set up the shop
        return None
    left = missing()
    if not left:
        return None
    return dict(left=left, url=reverse("start"))
