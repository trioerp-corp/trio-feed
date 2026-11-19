"""The fixed side menu (1.21): grouped, only what fits the shop's business, in its own words.

A clinic never sees «لوحة البيع» or barcode labels, a café calls its stock «الخامات», a gaming hall its buffet. Pages that
belong to the same section light the same link. Links whose page does not exist in this copy (url name missing) are skipped,
so the menu can list parts that are added later without breaking older templates.
"""
from django.urls import NoReverseMatch, reverse

# the words each business uses for the shared parts of the program (everything else keeps the default word)
WORDS = {
    "default": dict(home="الرئيسية", items="الأصناف والأسعار", count="الجرد والتالف", purchases="المشتريات", returns="مرتجع للمورد",
                    sales="المبيعات", partners="العملاء والموردين", money="المصروفات وصاحب المحل", treasury="الخزينة",
                    close="تقفيل اليوم", reports="التقارير", stock="المخزن", sell="البيع"),
    "market": dict(),
    "retail": dict(),
    "cafe": dict(items="الخامات والأصناف", count="جرد الخامات والهالك", purchases="شراء الخامات", sales="الطلبات والمبيعات",
                 stock="المطبخ والمخزن", sell="الطلبات"),
    "gaming": dict(items="البوفيه والخامات", count="جرد البوفيه", purchases="شراء البوفيه", sales="الفواتير والمبيعات", stock="البوفيه",
                   sell="الصالة"),
    "clinic": dict(home="اليوم", items="المستلزمات والأدوية", count="جرد المستلزمات", purchases="شراء مستلزمات",
                   returns="مرتجع مستلزمات", sales="الإيرادات والفواتير", partners="الموردين والحسابات الآجلة", close="تقفيل اليوم والدرج",
                   stock="المستلزمات", sell="العيادة"),
    "pharmacy": dict(items="الأدوية والأصناف", count="جرد الصيدلية", purchases="شراء أدوية", stock="الأدوية والمخزن", sell="البيع"),
}


def words(settings_obj=None):
    """The labels for this shop: its business words, then its activity's own name for items (e.g. «قطع الغيار»)."""
    from .models import CompanySettings

    s = settings_obj or CompanySettings.get()
    business = getattr(s, "business", "") or "market"
    act = s.activity if isinstance(getattr(s, "activity", None), dict) else {}
    w = dict(WORDS["default"], **WORDS.get(business, {}))
    if act.get("key") == "pharmacy":
        w.update(WORDS["pharmacy"])
    elif act.get("items") and business in ("market", "retail"):
        w["items"] = act["items"] + " والأسعار"
    return w


def _url(name, *args):
    try:
        return reverse(name, args=args)
    except NoReverseMatch:
        return None


def build(request, ctx):
    """[(group title, [dict(label, url, on, icon, badge, new_tab)])] for the logged-in user."""
    from .models import CompanySettings

    u = request.user
    s = ctx.get("company") or CompanySettings.get()
    mods = ctx.get("mods") or set()
    owner = ctx.get("is_owner")
    business = getattr(s, "business", "") or "market"
    clinic = "clinic" in mods
    w = words(s)
    path = request.path
    groups = []

    def link(label, url, icon="", match=(), exact=False, badge=None, new_tab=False):
        if not url:
            return None
        keys = match or (url,)
        on = any(path == k for k in keys) if exact else any(path.startswith(k) for k in keys)
        return dict(label=label, url=url, on=on, icon=icon, badge=badge, new_tab=new_tab)

    def group(title, *links):
        rows = [x for x in links if x]
        if rows:
            groups.append((title, rows))

    view_items = u.has_perm("inventory.view_item")
    cash = u.has_perm("treasury.view_voucher") or u.is_superuser

    # ---- the day's work
    home = link(w["home"], _url("home"), "🏠", ("/", "/ui/clinic/"), exact=True)
    if clinic:
        group("العيادة", home,
              link("الاستقبال", _url("clinic_reception"), "🛎", ("/ui/clinic/reception",)),
              link("شاشة الطبيب", _url("clinic_doctor_screen"), "🩺", ("/ui/clinic/doctor",)),
              link("الحجوزات", _url("clinic_appointments"), "📅", ("/ui/clinic/appointments",)),
              link("المرضى", _url("clinic_patients"), "🧑‍⚕️", ("/ui/clinic/patients", "/ui/clinic/visits")),
              link("التأمين الطبي", _url("clinic_insurance") if owner or cash else None, "🛡", ("/ui/clinic/insurance",)),
              link("الأطباء ونسبهم", _url("clinic_doctors") if owner else None, "👨‍⚕️", ("/ui/clinic/doctors",)),
              link("الخدمات والأسعار", _url("clinic_setup") if owner else None, "🧾", ("/ui/clinic/setup",)),
              link("لوحة صاحب العيادة", _url("clinic_owner") if owner else None, "📊", ("/ui/clinic/owner",)))
    else:
        group(w["sell"], home,
              link("🎮 الصالة", "/ui/play/" if "gaming" in mods else None, "", ("/ui/play",)),
              link("لوحة البيع (الكاشير)", "/pos/", "🧾", new_tab=True))

    # ---- stock
    if view_items:
        group(w["stock"],
              link(w["items"], _url("shop_items"), "📦", ("/ui/shop/items",)),
              link("💊 مكتبة الأدوية والشريط", _url("pharmacy") if ctx.get("is_pharmacy") else None, "", ("/ui/shop/pharmacy",)),
              link("المنيو", _url("shop_menu") if "recipes" in mods else None, "🍽", ("/ui/shop/menu",)),
              link("الترابيزات وطلبات QR", _url("shop_tables") if "recipes" in mods and owner else None, "🪑", ("/ui/shop/tables",)),
              link("المكونات والتكلفة", _url("shop_recipes") if "recipes" in mods else None, "🧪", ("/ui/shop/recipes",)),
              link(w["count"], _url("shop_count"), "📋", ("/ui/shop/count", "/ui/shop/damage")),
              link(w["purchases"], _url("shop_purchases") if "purchases" in mods else None, "🛒", ("/ui/shop/purchases",)),
              link(w["returns"], _url("shop_returns") if "returns" in mods else None, "↩", ("/ui/shop/returns",)),
              link("المخازن والتحويلات", _url("move_list") if "stores" in mods else None, "🏬", ("/ui/moves",)),
              link("التصنيع", _url("shop_production") if "production" in mods else None, "🏭", ("/ui/shop/production",)),
              link("الوحدات (كرتونة / علبة)", _url("shop_units") if "units" in mods else None, "📐", ("/ui/shop/units",)),
              link("ملصقات الباركود", _url("shop_labels") if "labels" in mods else None, "🏷", ("/ui/shop/labels",)),
              link("الصلاحية", _url("shop_expiry") if "expiry" in mods else None, "⏳", ("/ui/shop/expiry",)),
              link("العروض والخصومات", _url("shop_promos") if owner and "promos" in mods else None, "🎁", ("/ui/shop/promos",)))

    # ---- money
    if cash:
        group("الأموال",
              link(w["sales"], _url("shop_sales"), "💵", ("/ui/shop/sales",)),
              link(w["partners"], _url("shop_partners") if "partners" in mods else None, "👥", ("/ui/shop/partners", "/ui/shop/statement")),
              link(w["money"], _url("shop_money") if "money" in mods else None, "💸", ("/ui/shop/money",)),
              link(w["treasury"], _url("voucher_list") if "treasury" in mods else None, "🏦", ("/ui/vouchers",)),
              link(w["close"], _url("shop_close"), "🔒", ("/ui/shop/close",)))
        group("الموظفين والضرايب",
              link("الموظفين والمرتبات", _url("staff_list") if owner else None, "🧑‍💼", ("/ui/staff",)),
              link("الإقرارات الضريبية", _url("tax_home") if owner else None, "🧮", ("/ui/tax",)))
        group("التقارير",
              link(w["reports"], _url("shop_reports"), "📈", ("/ui/shop/reports", "/ui/shop/valuation")),
              link("الأرباح والشركاء", _url("shop_profit") if "profit_monthly" in mods else None, "💰", ("/ui/shop/profit",)),
              link("الفروع", _url("branches") if ctx.get("branch_on") and not ctx.get("branch_locked") else None, "🏬",
                   ("/ui/shop/branches",)),
              link("تقرير فرعي", _url("branch_report", ctx["branch_view"].pk) if ctx.get("branch_locked") and ctx.get("branch_view") else None,
                   "🏬", ("/ui/shop/branches",)))

    # ---- the shop itself
    group("الإعدادات والمساعدة",
          link("تجهيز المحل", _url("start") if owner else None, "🧰", ("/ui/start",)),
          link("البيانات الأساسية", _url("data_hub") if owner or u.has_perm("inventory.change_item") else None, "📚", ("/ui/data",)),
          link("الإعدادات", _url("shop_settings") if cash else None, "⚙", ("/ui/shop/settings", "/ui/shop/activity")),
          link("📱 الموبايل والتقارير", _url("connect") if cash else None, "", ("/ui/shop/connect",)),
          link("الطلبات", _url("shop_requests"), "📨", ("/ui/shop/requests",), badge=ctx.get("pending_requests") or None),
          link("دليل البرنامج (PDF)", _url("guide"), "📘", ("/ui/guide",)),
          link("التحديثات", _url("upgrade") if ctx.get("app_version") else None, "🔄", ("/ui/shop/upgrade",)))
    if u.is_superuser:
        group("المطور",
              link("الحسابات (قيود)", _url("entry_list"), "🔧", ("/ui/entries",)),
              link("إعدادات المطور", "/admin/", "🔧", ("/admin/",)),
              link("التثبيت والخطة", "/trio/setup/" if u.username == "ACC" else None, "🔧", ("/trio/setup",)))
    return groups
