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

    add("shop", "بيانات المحل على الإيصال (الهاتف والعنوان)", s.phone and s.address)
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
    sellable = Item.objects.filter(active=True, sale_price__isnull=False).exclude(code="0")
    add("items", "الأصناف وأسعار البيع", sellable.exists(), "أضفهم واحدًا تلو الآخر أو ارفع ملف إكسل مرة واحدة")
    no_limit = Item.objects.filter(active=True, item_type="stock", reorder_level__isnull=True).exclude(code="0").count()
    add("reorder", f"حد الطلب لكل صنف{f' (ناقص في {no_limit} صنف)' if no_limit else ''}", not no_limit)
    if "purchases" in mods:
        add("suppliers", "الموردون الذين تشتري منهم", Partner.objects.filter(kind="supplier", active=True).exists())
    if "partners" in mods:
        add("customers", "عملاء الآجل (إن كنت تبيع بالآجل)", Partner.objects.filter(kind="customer", active=True).exists())
    add("employees", "الموظفين ومرتباتهم", _employees_done(), "من أجل المرتبات والسلف وضريبة كسب العمل والتأمينات")
    return rows


def _employees_done():
    from django.apps import apps

    return apps.get_model("staff", "Employee").objects.exists()


def missing(s=None):
    return [r for r in checklist(s) if not r["done"]]


def card(request):
    """For the owner's home page: what is left, or None when all is ready."""
    from pos.voids import is_owner

    if not is_owner(request.user):
        return None
    left = missing()
    if not left:
        return None
    return dict(left=left, url=reverse("start"))
