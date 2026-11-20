"""Restaurant / café extras (1.24): service charge %, kitchen stations.

Service charge («خدمة 12%»): off by default; when the owner sets a percentage the till adds it as its own line on table bills
(and, if he chooses, on dine-in orders of a till without tables). It is a line of the service item «خدمة» → service revenue,
VAT like any line. The program recomputes it on every sale it receives and flags a sale whose charge does not match.

Kitchen stations: «المطبخ», «البار»… each with its printer and its categories. «إرسال للمطبخ» prints, for each station, only
its lines (a category not given to a station goes to the first one). No stations (or one) = one ticket, as before.

Settings: CompanySettings.menu_cfg["service"] = {pct, dine}, menu_cfg["stations"] = [{name, printer, cats: [category names]}].
"""
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError

from core.models import CompanySettings
from ledger import services as L

SVC_CODE = "TRIO-SVC"
BIZ = ("cafe", "gaming")  # = pos.tables.TABLE_BIZ
Z = Decimal(0)


def _dec(x, default=Z):
    try:
        v = Decimal(str(x).replace(",", ".").strip())
    except (InvalidOperation, ValueError):
        return default
    return v if v.is_finite() else default


# ---------------------------------------------------------------- service charge
def svc_cfg(s=None):
    s = s or CompanySettings.get()
    c = dict((s.menu_cfg or {}).get("service") or {})
    pct = _dec(c.get("pct"))
    on = s.business in BIZ and pct > 0
    return dict(on=on, pct=str(pct.normalize()) if on else "0", dine=bool(c.get("dine")) and on, code=SVC_CODE if on else "",
                name=f"خدمة {pct.normalize():f}%" if on else "")


def svc_item():
    from .delivery import service_item

    return service_item(SVC_CODE, "خدمة")


def set_svc(pct=None, dine=None):
    s = CompanySettings.get()
    m = dict(s.menu_cfg or {})
    c = dict(m.get("service") or {})
    if pct is not None:
        p = _dec(pct, None) if str(pct).strip() else Z
        if p is None or p < 0 or p > 30:
            raise ValidationError("نسبة الخدمة بين 0 و30%")
        c["pct"] = str(p)
    if dine is not None:
        c["dine"] = bool(dine)
    m["service"] = c
    s.menu_cfg = m
    s.save(update_fields=["menu_cfg"])
    if _dec(c.get("pct")) > 0:
        svc_item()
    return svc_cfg(s)


def check_svc(data):
    """A flag when the service line of a sale does not match the percentage on the goods (never trusts the till's figure)."""
    lines = data.get("lines") or []
    svc = [ln for ln in lines if str(ln.get("item_code")) == SVC_CODE]
    if not svc:
        return ""
    from .delivery import FEE_CODE

    c = svc_cfg()
    base = sum((_dec(ln.get("qty")) * _dec(ln.get("price")) - _dec(ln.get("discount")) for ln in lines
                if str(ln.get("item_code")) not in (SVC_CODE, FEE_CODE)), Z)
    got = sum((_dec(ln.get("qty")) * _dec(ln.get("price")) for ln in svc), Z)
    want = L.q2(base * _dec(c["pct"]) / 100) if c["on"] else Z
    if abs(L.q2(got) - want) > Decimal("0.05"):
        return f"الخدمة {L.q2(got)} والمستحق {want}"
    return ""


# ---------------------------------------------------------------- kitchen stations
def stations(s=None):
    s = s or CompanySettings.get()
    out = []
    for st in (s.menu_cfg or {}).get("stations") or []:
        if isinstance(st, dict) and str(st.get("name") or "").strip():
            out.append(dict(name=str(st["name"]).strip()[:40], printer=str(st.get("printer") or "").strip()[:120],
                            cats=[str(c)[:80] for c in st.get("cats") or []]))
    return out[:8]


def set_stations(rows):
    """rows: [{name, printer, cats}] from the owner's page (empty names dropped; a category belongs to one station)."""
    seen, clean = set(), []
    for r in rows or []:
        name = str(r.get("name") or "").strip()[:40]
        if not name or name in {x["name"] for x in clean}:
            continue
        cats = []
        for c in r.get("cats") or []:
            c = str(c).strip()[:80]
            if c and c not in seen:
                seen.add(c)
                cats.append(c)
        clean.append(dict(name=name, printer=str(r.get("printer") or "").strip()[:120], cats=cats))
    s = CompanySettings.get()
    m = dict(s.menu_cfg or {})
    m["stations"] = clean[:8]
    s.menu_cfg = m
    s.save(update_fields=["menu_cfg"])
    return stations(s)


def station_of(cat, sts):
    for st in sts:
        if cat in st["cats"]:
            return st["name"]
    return sts[0]["name"]


def route(lines, cat_of, sts=None):
    """[(station or None, lines)] in station order; one ticket (None) when the shop has 0 or 1 station."""
    sts = stations() if sts is None else sts
    if len(sts) < 2:
        return [(dict(sts[0], name="") if sts else None, list(lines))] if lines else []  # one station: its printer, no title
    groups = {st["name"]: [] for st in sts}
    for ln in lines:
        groups[station_of(cat_of(ln) or "", sts)].append(ln)
    return [(st, groups[st["name"]]) for st in sts if groups[st["name"]]]


def printer_of(st, s=None):
    s = s or CompanySettings.get()
    return (st or {}).get("printer") or s.kitchen_printer


def item_cats(codes):
    """Item code → its category name (a size belongs to its product's category)."""
    from inventory.models import Item

    out = {}
    for it in Item.objects.filter(code__in=list(codes)).select_related("category", "variant_of__category"):
        out[it.code] = (it.variant_of.category if it.variant_of_id else it.category).name
    return out


def is_extra(code):
    from .delivery import FEE_CODE

    return str(code) in (SVC_CODE, FEE_CODE)
