"""«تجهيز المحل» — one page, filled once by the owner right after setup (and reachable any time from the home card)."""
import datetime as dt
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils import timezone

from core import readiness as RD
from core.models import CompanySettings
from core.modules import enabled


def _dec(v, default=None):
    txt = str(v or "").strip().replace(",", "").translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    if not txt:
        return default
    try:
        return Decimal(txt)
    except InvalidOperation:
        raise ValidationError(f"«{v}» ليس رقمًا")


def _mark(s, key):
    ob = dict(s.onboarding or {})
    ob[key] = timezone.now().isoformat(timespec="seconds")
    ob["skip"] = [k for k in ob.get("skip", []) if k != key]
    s.onboarding = ob
    s.save(update_fields=["onboarding"])


def _names(text):
    """«اسم» or «اسم، تليفون» per line → [(name, phone)]."""
    out = []
    for line in (text or "").splitlines():
        parts = [p.strip() for p in line.replace("،", ",").split(",")]
        if parts and parts[0]:
            out.append((parts[0][:150], (parts[1] if len(parts) > 1 else "")[:40]))
    return out


@transaction.atomic
def _save(request, s, op):
    from ledger.models import Partner
    from pos.models import Branch
    from treasury.models import CashBox

    P = request.POST
    if op == "skip":
        key = P.get("key", "")
        ob = dict(s.onboarding or {})
        ob["skip"] = sorted(set(ob.get("skip", [])) | {key})
        s.onboarding = ob
        s.save(update_fields=["onboarding"])
        return "تم التخطي — يمكنك العودة إليها في أي وقت من هنا"
    if op == "shop":
        from core.setup_service import check_shop_name

        name = " ".join(P.get("name", "").split())
        if not name:
            raise ValidationError("اكتب اسم المحل")
        check_shop_name(name)
        s.name, s.phone, s.address = name, P.get("phone", "").strip()[:40], P.get("address", "").strip()[:250]
        s.save(update_fields=["name", "phone", "address"])
        br = Branch.objects.order_by("pk").first()
        if br and P.get("footer") is not None:
            br.receipt_footer = P.get("footer", "").strip()[:200]
            br.save(update_fields=["receipt_footer"])
        f, logo = request.FILES.get("logo"), ""
        if f:  # the logo on the receipt, the program header and the QR cards
            from core.setup_service import logo_svg

            if f.size > 2 * 1024 * 1024:
                raise ValidationError("صورة الشعار كبيرة — الحد الأقصى 2 ميجابايت")
            s.logo_svg, logo = logo_svg(f.read(), f.name), " والشعار"
            s.save(update_fields=["logo_svg"])
        elif P.get("no_logo") == "1":
            s.logo_svg = ""
            s.save(update_fields=["logo_svg"])
        _mark(s, "shop")
        return f"تم حفظ بيانات المحل{logo} ✔"
    if op == "tax":
        regime = P.get("tax_regime")
        if regime not in dict(s.TAX_REGIMES):
            raise ValidationError("اختر النظام الضريبي")
        s.tax_regime, s.vat_registered = regime, P.get("vat_registered") == "1"
        rate = _dec(P.get("vat_rate"), Decimal("14"))
        if s.vat_registered and not (0 < rate <= 100):
            raise ValidationError("يجب أن تكون نسبة القيمة المضافة بين 1 و100")
        s.default_vat_rate = rate if s.vat_registered else Decimal(0)
        s.pos_prices_include_vat = P.get("incl", "1") == "1"
        for f in ("tax_registration", "tax_file_no", "tax_office", "commercial_register", "insurance_no"):
            setattr(s, f, P.get(f, "").strip()[:80])
        if s.vat_registered and not s.tax_registration:
            raise ValidationError("على المسجّل في القيمة المضافة كتابة رقم التسجيل الضريبي (يظهر على الفاتورة)")
        s.save(update_fields=["tax_regime", "vat_registered", "default_vat_rate", "pos_prices_include_vat", "tax_registration", "tax_file_no",
                              "tax_office", "commercial_register", "insurance_no"])
        _mark(s, "tax")
        return "تم حفظ الملف الضريبي ✔"
    if op == "money":
        on = set(P.getlist("box"))
        for b in CashBox.objects.exclude(kind="cash"):
            if Branch.objects.filter(cashbox=b).exists():
                continue
            b.active = str(b.pk) in on
            b.save(update_fields=["active"])
        new = P.get("new_box", "").strip()
        if new:
            from ui.manage import _new_cashbox

            box = CashBox(name=new[:100], kind=P.get("new_kind") if P.get("new_kind") in ("wallet", "bank") else "wallet", order=20)
            _new_cashbox(box)
            box.save()
        cash = _dec(P.get("opening_cash"), Decimal(0))
        if cash and cash > 0 and not (s.onboarding or {}).get("opening_cash"):
            from pos import money

            drawer = Branch.objects.order_by("pk").first().cashbox
            money.post("deposit", cash, timezone.localdate(), cash_account=drawer.account, uid="OPENING-CASH", note="رصيد الدرج في البداية",
                       by=request.user.get_username(), user=request.user)
            ob = dict(s.onboarding or {})
            ob["opening_cash"] = str(cash)
            s.onboarding = ob
            s.save(update_fields=["onboarding"])
        _mark(s, "money")
        return "تم حفظ طرق الدفع ✔"
    if op in ("suppliers", "customers"):
        kind = op[:-1]
        made = 0
        for name, phone in _names(P.get("names")):
            if not Partner.objects.filter(kind=kind, name=name).exists():
                Partner.objects.create(kind=kind, name=name, phone=phone)
                made += 1
        _mark(s, op)
        return f"تمت الإضافة {made} {'مورد' if kind == 'supplier' else 'عميل'} ✔"
    if op == "doctor":
        from django.apps import apps

        Doctor = apps.get_model("clinic", "Doctor")
        name = P.get("name", "").strip()
        if not name:
            raise ValidationError("اكتب اسم الطبيب")
        share = _dec(P.get("share_pct"), Decimal(0))
        if not (0 <= share <= 100):
            raise ValidationError("النسبة من 0 إلى 100")
        Doctor.objects.create(name=name[:100], specialty=P.get("specialty", "").strip()[:100], share_pct=share, phone=P.get("phone", "")[:40])
        return f"أُضيف د. {name} ✔"
    if op == "service":
        from django.apps import apps

        from pos.clinic import services as C

        Doctor = apps.get_model("clinic", "Doctor")
        doc = Doctor.objects.filter(pk=P.get("doctor") or 0).first()
        C.save_service(P.get("name"), _dec(P.get("price"), Decimal(0)), doctor=doc, minutes=P.get("minutes") or 15)
        return f"تمت إضافة «{P.get('name')}» ✔"
    if op == "gaming_rules":  # how the hall bills time — chosen at setup, changed here any time
        from gaming.services import check_rules

        s.gaming_round_minutes, s.gaming_min_minutes, s.gaming_grace_minutes = check_rules(P.get("g_round"), P.get("g_min"), P.get("g_grace"))
        s.save(update_fields=["gaming_round_minutes", "gaming_min_minutes", "gaming_grace_minutes"])
        from gaming.services import rule_text

        return f"تم الحفظ ✔ — {rule_text(s)} (على الحسابات الجديدة؛ وما أُغلق من قبل يبقى كما هو)"
    if op == "station":
        from django.apps import apps

        Station = apps.get_model("gaming", "Station")
        name = P.get("name", "").strip()
        rate = _dec(P.get("rate"))
        if not name or not rate or rate <= 0:
            raise ValidationError("اكتب اسم الجهاز وسعر الساعة")
        Station.objects.create(name=name[:40], kind=P.get("kind") or "ps5", rate=rate, rate_multi=_dec(P.get("rate_multi")),
                               order=Station.objects.count() + 1)
        return f"تمت الإضافة «{name}» ✔"
    raise ValidationError("عملية غير معروفة")


@login_required
def page(request):
    from pos.voids import is_owner

    if not (is_owner(request.user) or request.user.is_superuser):
        raise PermissionDenied
    s = CompanySettings.get()
    if request.method == "POST":
        try:
            msg = _save(request, s, request.POST.get("op", ""))
            messages.success(request, msg)
        except ValidationError as e:
            messages.error(request, e.messages[0])
        return redirect(f"{request.path}#{request.POST.get('op', '')}")
    from django.apps import apps

    from inventory.models import Item
    from ledger.models import Partner
    from pos.models import Branch
    from treasury.models import CashBox

    mods = enabled(s)
    rows = RD.checklist(s)
    ctx = dict(s=s, rows=rows, left=[r for r in rows if not r["done"]], status={r["key"]: r for r in rows}, mods=mods,
               boxes=[b for b in CashBox.objects.exclude(kind="cash").order_by("order", "pk") if not Branch.objects.filter(cashbox=b).exists()],
               footer=(Branch.objects.order_by("pk").first() or Branch()).receipt_footer,
               items=Item.objects.filter(active=True).exclude(code="0").count(),
               no_limit=Item.objects.filter(active=True, item_type="stock", reorder_level__isnull=True).exclude(code="0").count(),
               suppliers=list(Partner.objects.filter(kind="supplier", active=True).values_list("name", flat=True)[:30]),
               customers=list(Partner.objects.filter(kind="customer", active=True).values_list("name", flat=True)[:30]),
               opening_cash=(s.onboarding or {}).get("opening_cash"), staff_url="/ui/staff/")
    if not (s.onboarding or {}).get("guide"):  # the first time: the guide of his business downloads by itself
        from ui.guide import _pdf
        from core.guide import key_for

        if _pdf(key_for(s)):
            ctx["auto_guide"] = True
            _mark(s, "guide")
    if s.business == "cafe":
        from inventory import menu as MN
        from pos.models import DiningTable

        ctx.update(menu_n=len(MN.products()[0]), tables_n=DiningTable.objects.filter(active=True).count())
    if "clinic" in mods:
        Doctor, Service = apps.get_model("clinic", "Doctor"), apps.get_model("clinic", "Service")
        ctx.update(doctors=Doctor.objects.filter(active=True), services=Service.objects.filter(item__active=True).select_related("item", "doctor"))
    if "gaming" in mods:
        from gaming.services import rule_text

        ctx["stations"] = apps.get_model("gaming", "Station").objects.filter(active=True).order_by("order")
        ctx["g_rule"] = rule_text(s)
    return render(request, "ui/start.html", ctx)
