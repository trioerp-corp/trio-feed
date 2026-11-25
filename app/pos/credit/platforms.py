"""Delivery platforms (1.25, optional; 1.26: per country — Talabat / Elmenus in Egypt, HungerStation / Jahez / Keeta … in Saudi Arabia).

The owner writes his store links and each platform's commission %; the till then shows «طلبات» / «المنيوز» as a way to pay.
Such a sale is not cash: the platform owes it. It posts Dr «مستحقات منصات التوصيل» (the platform's partner) instead of the
drawer, and the commission at the same moment: Dr «عمولات منصات التوصيل» / Cr the same receivable — so the receivable is
always what the platform will really transfer. When the money arrives: «تسجيل تحويل من المنصة» (Dr the bank / wallet / drawer,
Cr the receivable; what the platform kept besides — fees, fines — goes to the commission account).

The orders are rung by hand: importing them automatically needs each platform's partner API (an integration contract with
the platform), which is not available to shops today.
"""
import re
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from core.models import CompanySettings
from ledger import services as L
from ledger.models import Account, JournalLine, Partner

from .models import PlatformPayout

# 1.26: each platform with the countries it works in (ISO codes) and the shops it serves — food: restaurants / cafés,
# mart: supermarkets, pharmacy: pharmacies. The keys are stored on orders (max 12 letters) — never rename one.
_P = [
    ("talabat", "طلبات", "🟠", ("talabat.com",), "EG KW AE QA BH OM JO IQ", "food mart pharmacy", "talabat.com/egypt/…"),
    ("elmenus", "المنيوز", "🔴", ("elmenus.com",), "EG", "food", "elmenus.com/cairo/…"),
    ("rabbit", "رابيت", "🐰", ("rabbitmart.com",), "EG SA", "mart", "rabbitmart.com/…"),
    ("mrsool", "مرسول", "🟢", ("mrsool.co",), "SA EG", "food mart pharmacy", "mrsool.co/…"),
    ("hunger", "هنقرستيشن", "🟡", ("hungerstation.com",), "SA", "food mart pharmacy", "hungerstation.com/sa-ar/…"),
    ("jahez", "جاهز", "🟤", ("jahez.net",), "SA KW BH", "food mart", "jahez.net/…"),
    ("keeta", "كيتا", "🟨", ("keeta.com",), "SA QA KW AE", "food", "keeta.com/…"),
    ("chefz", "ذا شفز", "🍽", ("thechefz.co",), "SA", "food", "thechefz.co/…"),
    ("careem", "كريم", "🟩", ("careem.com",), "AE JO", "food mart", "careem.com/…"),
    ("deliveroo", "ديليفرو", "🔷", ("deliveroo.ae", "deliveroo.com.kw", "deliveroo.qa", "deliveroo.com"), "AE KW QA", "food mart",
     "deliveroo.ae/menu/…"),
    ("snoonu", "سنونو", "🔺", ("snoonu.com",), "QA", "food mart pharmacy", "snoonu.com/…"),
    ("toters", "توترز", "🟦", ("totersapp.com",), "LB IQ", "food mart", "totersapp.com/…"),
    ("lezzoo", "ليزو", "🟥", ("lezzoo.com",), "IQ", "food mart", "lezzoo.com/…"),
    ("glovo", "جلوفو", "🟡", ("glovoapp.com",), "MA TN", "food mart pharmacy", "glovoapp.com/ma/…"),
    ("yassir", "ياسر", "🟣", ("yassir.com",), "DZ MA TN", "food mart", "yassir.com/…"),
]
EN = {'talabat': 'Talabat', 'elmenus': 'Elmenus', 'rabbit': 'Rabbit', 'mrsool': 'Mrsool', 'hunger': 'HungerStation', 'jahez': 'Jahez', 'keeta': 'Keeta', 'chefz': 'The Chefz', 'careem': 'Careem', 'deliveroo': 'Deliveroo', 'snoonu': 'Snoonu', 'toters': 'Toters', 'lezzoo': 'Lezzoo', 'glovo': 'Glovo', 'yassir': 'Yassir'}
PLATFORMS = {k: dict(name=n, en=EN[k], icon=i, hosts=h, cc=set(cc.split()), kinds=set(kd.split()), ph=ph) for k, n, i, h, cc, kd, ph in _P}


def label(key):
    """The platform's name in the viewer's language (its own Latin brand name in English / French: «طلبات» is also a plain word)."""
    from core.currency import lang

    meta = PLATFORMS[key]
    return meta["name"] if lang() == "ar" else meta["en"]
BIZ = ("market", "retail", "cafe")  # the page / menu gate is serves() below: which platform works with which shop


def country(s=None):
    """The shop's country, from the currency it chose in the first setup (EGP → EG, SAR → SA …); "" when it is not one we list."""
    from core.currency import BY_COUNTRY

    s = s or CompanySettings.get()
    return {v: k for k, v in BY_COUNTRY.items()}.get((s.base_currency or "").upper(), "")


def _kind(s):
    if s.business == "cafe":
        return "food"
    if s.business == "market":
        return "mart"
    if s.business == "retail" and (s.activity or {}).get("key") == "pharmacy":
        return "pharmacy"
    return ""


def serves(key, s=None):
    """1.26: a platform shows only in the countries it works in, and only for the shops it serves (a clothes shop, the clinic
    and the gaming hall get none). A country we do not list sees every platform that fits the shop."""
    s = s or CompanySettings.get()
    meta, kind, cc = PLATFORMS.get(key), _kind(s), country(s)
    return bool(meta and kind and kind in meta["kinds"] and (not cc or cc in meta["cc"]))


def available(s=None):
    """The platforms this shop may link (an empty list hides the page, the menu link and the setup step)."""
    s = s or CompanySettings.get()
    return [k for k in PLATFORMS if serves(k, s)]
RECEIVABLE, COMMISSION = "1203010102", "3202010131"
MAX_PCT = Decimal("50")
Z = Decimal(0)


def _dec(x, default=None):
    try:
        v = Decimal(str(x).replace("٫", ".").replace(",", ".").replace("%", "").strip())
    except (InvalidOperation, ValueError):
        return default
    return v if v.is_finite() else default


def raw(s=None):
    s = s or CompanySettings.get()
    return {k: dict(v) for k, v in ((s.menu_cfg or {}).get("platforms") or {}).items() if k in PLATFORMS and isinstance(v, dict)}


def receivable():
    acc = Account.objects.filter(code=RECEIVABLE).first()
    if acc:
        return acc
    return Account.objects.create(code=RECEIVABLE, name="مستحقات منصات التوصيل", parent=Account.objects.get(code="12030101"),
                                  level=6, is_postable=True, account_type="asset", normal_balance="debit", report="balance_sheet",
                                  cash_flow_category="operating", is_partner_control=True)


def commission_account():
    acc = Account.objects.filter(code=COMMISSION).first()
    if acc:
        return acc
    return Account.objects.create(code=COMMISSION, name="عمولات منصات التوصيل", parent=Account.objects.get(code="32020101"), level=6,
                                  is_postable=True, account_type="expense", normal_balance="debit", report="profit_loss",
                                  cash_flow_category="operating", dim1_policy="optional")


def partner(key, s=None):
    c = raw(s).get(key) or {}
    p = Partner.objects.filter(pk=c.get("pid") or 0).first()
    if p:
        return p
    p = Partner.objects.filter(kind="other", name=f"منصة {PLATFORMS[key]['name']}").first() or Partner.objects.create(
        kind="other", name=f"منصة {PLATFORMS[key]['name']}")
    return p


def clean_link(key, link):
    link = str(link or "").strip()
    if not link:
        return ""
    if not re.match(r"^https?://", link, re.I):
        link = "https://" + link
    m = re.match(r"^https?://([^/?#\s]+)(/[^\s]*)?$", link, re.I)
    host = (m.group(1).lower() if m else "").split(":")[0]
    if not m or not any(host == h or host.endswith("." + h) for h in PLATFORMS[key]["hosts"]):
        raise ValidationError(f"رابط {PLATFORMS[key]['name']} يجب أن يكون من موقع {PLATFORMS[key]['hosts'][0]} — انسخه من صفحة محلك على المنصة")
    return link[:300]


def cfg(s=None):
    """[dict(key, name, icon, on, link, pct, pid)] — every platform, configured or not."""
    s = s or CompanySettings.get()
    r = raw(s)
    out = []
    for k, meta in PLATFORMS.items():
        if not serves(k, s) and not (r.get(k) or {}).get("pid"):  # a platform that does not serve this shop (kept if it was used)
            continue
        c = r.get(k) or {}
        out.append(dict(key=k, name=label(k), icon=meta["icon"], on=bool(c.get("on")) and serves(k, s), link=c.get("link", ""),
                        pct=str(_dec(c.get("pct"), Z)), pid=c.get("pid"), ph=meta["ph"]))
    return out


def active(s=None):
    return [c for c in cfg(s) if c["on"]]


def till(s=None):
    """What the till needs: the platforms to show as ways to pay."""
    return [dict(k=c["key"], n=c["name"], i=c["icon"]) for c in active(s)]


@transaction.atomic
def set_cfg(rows):
    """rows: {key: dict(on, link, pct)}"""
    s = CompanySettings.get()
    m = dict(s.menu_cfg or {})
    cur = dict(m.get("platforms") or {})
    for k, v in rows.items():
        if k not in PLATFORMS or not serves(k, s):
            continue
        c = dict(cur.get(k) or {})
        pct = _dec(v.get("pct") or "0")
        if pct is None or pct < 0 or pct > MAX_PCT:
            raise ValidationError(f"عمولة {PLATFORMS[k]['name']} من 0 إلى {MAX_PCT:.0f}%")
        c.update(on=bool(v.get("on")), link=clean_link(k, v.get("link")), pct=str(L.q2(pct)))
        if c["on"]:
            c["pid"] = partner(k, s).pk
            receivable()
            commission_account()
        cur[k] = c
    m["platforms"] = cur
    s.menu_cfg = m
    s.save(update_fields=["menu_cfg"])
    return cfg(s)


def for_order(data, s=None):
    """The platform of a till sale → dict(key, name, partner, pct, off). A platform the owner never set up is refused; one
    he turned off after the till rang it offline is accepted (never drop a sale) and flagged."""
    key = str(data.get("platform") or "")
    if key not in PLATFORMS:
        raise ValidationError("منصة توصيل غير معروفة")
    s = s or CompanySettings.get()
    c = raw(s).get(key) or {}
    if not c.get("pid"):
        raise ValidationError(f"منصة {PLATFORMS[key]['name']} غير مفعّلة في الإعدادات")
    return dict(key=key, name=PLATFORMS[key]["name"], partner=partner(key, s), pct=_dec(c.get("pct"), Z) or Z, off=not c.get("on"))


def owed(key, s=None):
    p = partner(key, s)
    r = JournalLine.objects.filter(account__code=RECEIVABLE, partner=p, entry__state__in=["posted", "reversed"]).aggregate(
        d=Sum("debit"), c=Sum("credit"))
    return L.q2((r["d"] or Z) - (r["c"] or Z))


def report(start, end, s=None):
    """Per platform: orders, sales, commission, net, transferred in the period, still owed now."""
    from pos.models import Order

    s = s or CompanySettings.get()
    rows = []
    for c in cfg(s):
        if not c["pid"]:
            continue
        p = partner(c["key"], s)
        qs = Order.objects.filter(customer=p, cancelled=False, created_at_device__date__gte=start, created_at_device__date__lte=end)
        n = sales = comm = Z
        for o in qs:
            sg = -1 if o.is_return else 1
            n += sg
            sales += sg * o.total
            comm += sg * (_dec((o.raw or {}).get("plat_comm"), Z) or Z)
        pays = PlatformPayout.objects.filter(platform=c["key"], cancelled=False, date__gte=start, date__lte=end)
        got = pays.aggregate(a=Sum("amount"), o=Sum("other"))
        rows.append(dict(c, n=int(n), sales=sales, comm=comm, net=sales - comm, got=got["a"] or Z, other=got["o"] or Z, owed=owed(c["key"], s),
                         orders=qs.order_by("-created_at_device")[:100]))
    return rows


def _who(user):
    if not user or isinstance(user, str):
        return str(user or "")[:100]
    return (user.get_full_name() or user.username)[:100]


@transaction.atomic
def settle(key, amount, box, *, other=0, date=None, note="", user=None):
    """«تسجيل تحويل من المنصة»."""
    if key not in PLATFORMS:
        raise ValidationError("منصة غير معروفة")
    amount, other = L.q2(_dec(amount, Z) or Z), L.q2(_dec(other or 0, Z) or Z)
    if amount < 0 or other < 0 or amount + other <= 0:
        raise ValidationError("اكتب المبلغ المحوَّل")
    date = date or timezone.localdate()
    p = partner(key)
    name = PLATFORMS[key]["name"]
    desc = f"تحويل من منصة {name}" + (f" — {note}" if note else "")
    lines = []
    if amount:
        lines.append(dict(account=box.account, debit=amount, description=desc))
    if other:
        lines.append(dict(account=commission_account(), debit=other, description=f"خصومات أخرى من منصة {name}"))
    lines.append(dict(account=receivable(), partner=p, credit=amount + other, description=desc))
    pay = PlatformPayout.objects.create(platform=key, date=date, amount=amount, other=other, box=box, note=(note or "")[:200], by=_who(user))
    pay.entry = L.create_entry("JV", date, desc, lines, source=f"credit.payout:{pay.pk}", user=user if user and not isinstance(user, str) else None)
    pay.save(update_fields=["entry"])
    return pay


@transaction.atomic
def cancel_payout(pay, reason="", user=None):
    pay = PlatformPayout.objects.select_for_update().get(pk=pay.pk)
    if pay.cancelled:
        raise ValidationError("ملغى بالفعل")
    if pay.entry_id and pay.entry.state == "posted":
        L.reverse(pay.entry, date=timezone.localdate(), reason=reason or "إلغاء تحويل منصة", user=user if user and not isinstance(user, str) else None)
    pay.cancelled = True
    pay.save(update_fields=["cancelled"])
    return pay
