"""Trio setup: turn what the developer chose in the setup screen into a working copy for the client."""
import base64
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import transaction

from . import modules as M
from .models import CompanySettings, UserFlag

DEV_USER = "Trio"
ROLES = {"owner": ("مدير المحل", "صاحب المحل / المدير"), "branch_manager": ("", "مدير فرع"), "cashier": ("كاشير", "كاشير"),
         "storekeeper": ("أمين مخزن", "أمين مخزن"), "accountant": ("محاسب", "محاسب"), "custom": ("", "مسمى خاص (اختر الصلاحيات)")}
LEGACY_GROUPS = ("أمين مخزن", "محاسب")
LOGO_TYPES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp", "gif": "image/gif", "svg": "image/svg+xml"}
USERNAME = re.compile(r"^[\w؀-ۿ.@+-]{2,30}$")


def dec(v, default="0"):
    try:
        return Decimal(("" if v is None else str(v)).replace(",", ".").strip() or default)
    except InvalidOperation:
        raise ValidationError(f"رقم غير صحيح: {v}")


def logo_svg(data, filename):
    """Any uploaded picture becomes an SVG wrapper around the image (scripts inside an uploaded SVG never run that way)."""
    ext = Path(filename or "").suffix.lower().lstrip(".")
    if ext not in LOGO_TYPES:
        raise ValidationError("يجب أن يكون اللوجو صورة PNG أو JPG أو SVG أو WEBP")
    if len(data) > 3 * 1024 * 1024:
        raise ValidationError("صورة اللوجو كبيرة — الحد الأقصى 3 ميجابايت")
    mime = LOGO_TYPES[ext]
    if ext != "svg":
        try:  # shrink big photos (Pillow is optional)
            import io

            from PIL import Image

            im = Image.open(io.BytesIO(data))
            if max(im.size) > 360:
                im.thumbnail((360, 360))
                buf = io.BytesIO()
                im.save(buf, format="PNG", optimize=True)
                data, mime = buf.getvalue(), "image/png"
        except Exception:  # noqa: BLE001
            pass
    b64 = base64.b64encode(data).decode()
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="100%" height="100%" role="img" aria-label="logo">'
            f'<image href="data:{mime};base64,{b64}" x="0" y="0" width="100" height="100" preserveAspectRatio="xMidYMid meet"/></svg>')


def clean(data):
    """Validate the setup form (already parsed into a dict) and return a normalised config."""
    name = (data.get("name") or "").strip()
    if len(name) < 2:
        raise ValidationError("اكتب اسم مشروع العميل")
    from . import activities as A

    act = A.clean(data["activity"]) if isinstance(data.get("activity"), dict) else None
    business = data.get("business") if data.get("business") in M.BUSINESS else (act["base"] if act else "market")
    if CompanySettings.get().setup_done:  # 1.23: the business type is fixed once the shop is set up — it never changes
        business = CompanySettings.get().business
    act = act or (CompanySettings.get().activity if CompanySettings.get().business == business and CompanySettings.get().activity else A.for_base(business))
    plan = data.get("plan") if data.get("plan") in M.PLANS else "pro"
    mods = [m for m in data.get("modules", []) if m in M.MODULES]
    mods = sorted((m for m in set(mods) | set(M.ALWAYS) if M.fits(m, business)), key=list(M.MODULES).index)
    users, seen = [], set()
    for u in data.get("users", []):
        un = (u.get("username") or "").strip()
        if not un and not (u.get("password") or "").strip():
            continue
        if not USERNAME.match(un):
            raise ValidationError(f"اسم المستخدم «{un}» يجب أن يكون من 2 إلى 30 حرفًا بدون مسافات")
        if un.lower() == DEV_USER.lower():
            raise ValidationError("اسم المستخدم Trio محجوز لمطور النظام")
        if un.lower() in seen:
            raise ValidationError(f"اسم المستخدم «{un}» متكرر")
        seen.add(un.lower())
        role = u.get("role") if u.get("role") in ROLES else "cashier"
        pw = (u.get("password") or "").strip()
        exists = get_user_model().objects.filter(username=un).exists()
        if not pw and not exists:
            raise ValidationError(f"اكتب كلمة سر للمستخدم «{un}»")
        if pw and len(pw) < 4:
            raise ValidationError(f"كلمة سر «{un}» قصيرة — 4 حروف/أرقام على الأقل")
        from pos.branches import CAP_KEYS

        caps = [c for c in (u.get("caps") or []) if c in CAP_KEYS]
        if role == "custom" and not caps:
            raise ValidationError(f"«{un}» مسمى خاص — علّم صلاحية واحدة على الأقل")
        users.append(dict(username=un, first_name=(u.get("first_name") or un).strip()[:150], password=pw, role=role,
                          active=u.get("active", True), branch=(u.get("branch") or "").strip(), title=(u.get("title") or "").strip()[:60],
                          caps=caps or None, email=(u.get("email") or "").strip()[:150]))
    branches = []
    if "branches" in mods:
        for k, b in enumerate(data.get("branches", [])):
            nm = (b.get("name") or "").strip()
            if not nm:
                continue
            n = int(dec(b.get("devices") or 1))
            if n < 1 or n > 20:
                raise ValidationError(f"عدد أجهزة الكاشير في «{nm}» من 1 إلى 20")
            mu = (b.get("m_username") or "").strip()
            if mu:
                if not USERNAME.match(mu) or mu.lower() == DEV_USER.lower():
                    raise ValidationError(f"اسم دخول مدير «{nm}» غير صحيح")
                if mu.lower() in seen:
                    raise ValidationError(f"اسم المستخدم «{mu}» متكرر")
                seen.add(mu.lower())
                if len((b.get("m_password") or "").strip()) < 4:
                    raise ValidationError(f"كلمة سر مدير «{nm}» يلزم 4 حروف/أرقام على الأقل")
            branches.append(dict(key=f"new{b.get('i', k)}", name=nm[:100], address=(b.get("address") or "").strip()[:200], phone=(b.get("phone") or "").strip()[:40],
                                 devices=n, m_name=(b.get("m_name") or "").strip(), m_username=mu, m_password=(b.get("m_password") or "").strip(),
                                 m_email=(b.get("m_email") or "").strip()))
    if not any(u["role"] == "owner" and u["active"] and not u["branch"] for u in users) and not get_user_model().objects.filter(
            groups__name="مدير المحل", is_active=True).exists():
        raise ValidationError("يجب وجود مستخدم واحد على الأقل بصلاحية «صاحب المحل / المدير»")
    stations = []
    if "gaming" in mods:
        for g in data.get("stations", []):
            n = int(dec(g.get("count") or 0))
            if n <= 0:
                continue
            if n > 60:
                raise ValidationError("عدد الأجهزة كبير جدًا")
            rate = dec(g.get("rate"))
            if rate <= 0:
                raise ValidationError("اكتب سعر الساعة لكل نوع أجهزة")
            stations.append(dict(kind=g.get("kind") or "ps5", prefix=(g.get("prefix") or "جهاز").strip()[:30], count=n, rate=rate,
                                 rate_multi=dec(g.get("rate_multi"), "0") or None))
    return dict(name=name[:200], name_en=(data.get("name_en") or name).strip()[:200], phone=(data.get("phone") or "").strip()[:60],
                address=(data.get("address") or "").strip()[:200], business=business, plan=plan, modules=mods, activity=act,
                devices=min(max(int(data.get("devices") or 1), 1), 10),
                vat=bool(data.get("vat")), vat_rate=dec(data.get("vat_rate") or "14"), users=users, stations=stations, branches=branches,
                force_change=bool(data.get("force_change")), license_code="".join((data.get("license_code") or "").split()),
                reports=dict(whatsapp="report_whatsapp" in mods, telegram="report_telegram" in mods, email="report_email" in mods),
                owner_whatsapp=wa_number(data.get("owner_whatsapp")), email_to=(data.get("email_to") or "").strip()[:300],
                gaming=dict(zip(("round", "min", "grace"), _gaming_rules(data))))


def _gaming_rules(data):
    from gaming.services import check_rules

    return check_rules(data.get("g_round") or 15, data.get("g_min") if data.get("g_min") not in (None, "") else 30,
                       data.get("g_grace") if data.get("g_grace") not in (None, "") else 5)


def wa_number(v):
    v = "".join(ch for ch in str(v or "") if ch.isdigit())[:15]
    return "2" + v if v.startswith("01") and len(v) == 11 else v


def warehouses(cfg):
    whs = [{"code": "WH01", "name": "المحل" if cfg["business"] in ("market", "retail") else "البار / البوفيه", "role": "shop"}]
    if "stores" in cfg["modules"]:
        whs += [{"code": "WH02", "name": "المخزن الرئيسي", "role": "main"}, {"code": "WH03", "name": "المخزن التشغيلي", "role": "operating"}]
    return whs


@transaction.atomic
def apply(data, logo=None, logo_name=""):
    """First run: builds the whole database for the client. Later (developer interface): updates plan, users, devices."""
    cfg = clean(data)
    s = CompanySettings.get()
    first = not s.setup_done
    if first:
        seed = dict(name=cfg["name"], name_en=cfg["name_en"], phone=cfg["phone"], address=cfg["address"], vat=cfg["vat"],
                    business=cfg["business"], warehouses=warehouses(cfg), reports=cfg["reports"], categories=cfg["activity"]["categories"],
                    cashiers=[{"device": f"{i + 1:02d}", "first_name": u["first_name"]} for i, u in enumerate(
                        [u for u in cfg["users"] if u["role"] == "cashier"])] or [{"device": f"{i + 1:02d}", "first_name": f"كاشير {i + 1}"} for i in range(cfg["devices"])])
        tmp = Path(settings.BASE_DIR) / "data"
        tmp.mkdir(exist_ok=True)
        f = tmp / "setup-config.json"
        f.write_text(json.dumps(seed, ensure_ascii=False, indent=1), encoding="utf-8")
        import io

        call_command("seed_shop", config=str(f), allow_existing=True, stdout=io.StringIO())
        s = CompanySettings.get()
    s.name, s.name_en, s.phone, s.address = cfg["name"], cfg["name_en"], cfg["phone"], cfg["address"]
    s.business, s.plan, s.modules, s.activity = cfg["business"], cfg["plan"], cfg["modules"], cfg["activity"]
    if not first:
        from .activities import add_categories

        add_categories(cfg["activity"]["categories"])
    s.default_vat_rate = cfg["vat_rate"] if cfg["vat"] else 0
    s.report_whatsapp, s.report_telegram, s.report_email = cfg["reports"]["whatsapp"], cfg["reports"]["telegram"], cfg["reports"]["email"]
    if cfg["owner_whatsapp"]:
        s.owner_whatsapp = cfg["owner_whatsapp"]
    if cfg["email_to"]:
        s.email_to = cfg["email_to"]
    s.gaming_round_minutes, s.gaming_min_minutes, s.gaming_grace_minutes = cfg["gaming"]["round"], cfg["gaming"]["min"], cfg["gaming"]["grace"]
    if first:  # a new copy starts without the mobile code; later saves keep whatever the owner turned on
        s.require_2fa = False
    if logo:
        s.logo_svg = logo_svg(logo, logo_name)
    if cfg["license_code"]:
        from . import license as LIC

        try:
            lic = LIC.parse(cfg["license_code"])
        except ValueError as e:
            raise ValidationError(str(e))
        if lic.get("m") != LIC.machine_code():
            raise ValidationError("كود التفعيل هذا صادر لجهاز آخر")
        s.license_code = cfg["license_code"]
    if not s.control_url:
        from . import control

        s.control_url = control.url(s)
    s.setup_done = True
    s.save()
    made = {}
    if cfg["branches"]:
        from pos import branches as BR

        for b in cfg["branches"]:
            br = BR.create_branch(b["name"], b["address"], b["phone"], b["devices"])
            made[b["key"]] = br
            if b["m_username"]:
                BR.create_user(b["m_username"], b["m_name"] or b["m_username"], b["m_password"], "branch_manager", br, "مدير فرع", None, b["m_email"], "")
    save_users(cfg, made)
    if "gaming" in cfg["modules"]:
        save_stations(cfg["stations"])
        from gaming.services import device, play_item

        device()
        play_item()
    (Path(settings.BASE_DIR) / "data").mkdir(exist_ok=True)
    (Path(settings.BASE_DIR) / "data" / "shop_name.txt").write_text(cfg["name"], encoding="utf-8")
    return cfg, first


def dev_user():
    U = get_user_model()
    u = U.objects.filter(username=DEV_USER).first() or U(username=DEV_USER)
    u.first_name, u.is_staff, u.is_superuser, u.is_active = "مطور النظام (Trio)", True, True, True
    u.set_unusable_password()  # the developer opens only with the e-mailed one-time code
    u.save()
    return u


def save_users(cfg, new_branches=None):
    """Users with their branch, job title and permissions (pos.branches turns them into what the program checks)."""
    from pos import branches as BR
    from pos.models import Branch

    new_branches = new_branches or {}
    U = get_user_model()
    for u in cfg["users"]:
        obj = U.objects.filter(username=u["username"]).first() or U(username=u["username"])
        new = obj.pk is None
        obj.first_name, obj.is_staff, obj.is_superuser, obj.is_active = u["first_name"], False, False, bool(u["active"])
        if u["password"]:
            obj.set_password(u["password"])
        obj.save()
        key = u.get("branch") or ""
        branch = new_branches.get(key) or (Branch.objects.filter(code=key).first() if key else None)
        legacy = ROLES[u["role"]][0]
        obj.groups.remove(*Group.objects.filter(name__in=LEGACY_GROUPS))
        if legacy in LEGACY_GROUPS:
            obj.groups.add(Group.objects.get_or_create(name=legacy)[0])
        BR.apply_staff(obj, u["role"], u.get("caps"), branch, u.get("title") or "", u.get("email"), None)
        if u["password"] and (new or cfg["force_change"]):
            UserFlag.objects.update_or_create(user=obj, defaults={"must_change_password": cfg["force_change"]})


def save_stations(groups):
    from gaming.models import Station

    order = (Station.objects.order_by("-order").values_list("order", flat=True).first() or 0)
    for g in groups:
        have = Station.objects.filter(kind=g["kind"], name__startswith=g["prefix"]).count()
        for i in range(have + 1, have + g["count"] + 1):
            order += 1
            Station.objects.create(name=f"{g['prefix']} {i}", kind=g["kind"], rate=g["rate"], rate_multi=g["rate_multi"], order=order)
