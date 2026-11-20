"""«منيو QR» for cafés / restaurants: the menu (items with their prices, by category) is published to Trio's Worker and
customers open it by scanning a QR on the table: <Worker>/menu/<public id>. The public id is derived from the shop's secret
(CompanySettings.mobile_key) so the menu link never reveals the owner's phone page. Updated by itself when prices change."""
import hashlib
import json
import time
from pathlib import Path

from django.conf import settings

from . import mobile
from .models import CompanySettings

RAW = ("خامات", "مستلزمات تشغيل")  # ingredients / packing are not on the menu


def on(s=None):
    from .modules import enabled

    return "recipes" in enabled(s or CompanySettings.get())


def public_id(s=None):
    """The same id the Worker computes: sha256("acc-menu:" + key)[:10 bytes] in hex."""
    return hashlib.sha256(("acc-menu:" + mobile.key(s)).encode()).hexdigest()[:20]


def link(s=None):
    base = mobile.api()
    return f"{base}/menu/{public_id(s)}" if base else ""


def table_link(table, s=None):
    """The QR printed on a table: the menu, and (with «الطلب من QR») ordering onto this table."""
    base = link(s)
    return f"{base}?t={table.qr}" if base else ""


def hidden(s=None):
    s = s or CompanySettings.get()
    return set((s.menu_cfg or {}).get("hide", []))


def categories():
    from inventory.models import ItemCategory

    return [c for c in ItemCategory.objects.order_by("id") if not any(r in c.name for r in RAW) and c.name not in ("خدمات", "إضافات")]


def data(s=None):
    from inventory.models import Item

    s = s or CompanySettings.get()
    hide = hidden(s)
    cats = []
    for c in categories():
        if c.pk in hide:
            continue
        from django.db.models import Q

        sized = set(Item.objects.filter(variant_of__category=c, variant_of__active=True, active=True, sale_price__gt=0).values_list("variant_of_id", flat=True))
        items = Item.objects.filter(Q(sale_price__gt=0) | Q(pk__in=sized), category=c, active=True, variant_of__isnull=True, is_addon=False).exclude(
            code__in=["0", "PLAY"]).order_by("name")  # «وقت لعب» is the hall's time charge, never on the menu
        rows = []
        for i in items[:150]:
            r = dict(n=i.name[:80], p=float(i.sale_price or 0), d=str((i.extra or {}).get("desc", ""))[:200], c=i.code)
            if i.pk in sized:  # 1.22: one product, its sizes with their prices
                vs = sorted(i.sizes.filter(active=True, sale_price__gt=0), key=lambda v: (v.extra or {}).get("order", 0))
                r["s"] = [[v.size_name[:30], float(v.sale_price), v.code] for v in vs]
                r["p"] = min(x[1] for x in r["s"])
            rows.append(r)
        if rows:
            cats.append(dict(n=c.name[:40], items=rows))
    addons = [dict(n=a.name[:60], p=float(a.sale_price), c=a.code, k=[c.name[:40] for c in a.addon_for.all()])
              for a in Item.objects.filter(active=True, is_addon=True, sale_price__gt=0).prefetch_related("addon_for").order_by("name")[:60]]
    cfg = s.menu_cfg or {}
    out = dict(name=s.name[:80], phone=s.phone[:40], address=s.address[:120], note=str(cfg.get("note", ""))[:200], cats=cats, add=addons,
               vat=bool(s.default_vat_rate and not s.pos_prices_include_vat))
    from pos import tables as T

    tc = T.cfg(s)
    if tc["on"]:  # 1.23: the table's QR carries its code; with «الطلب من QR» the customer orders from his phone
        from pos.models import DiningTable

        out["tb"] = {t.qr: t.name[:40] for t in DiningTable.objects.filter(active=True)}
        out["order"] = tc["qr_order"]
    return out


def _mark_file():
    return Path(settings.BASE_DIR) / "data" / "menu-sent.json"


def push(force=False):
    """Sends the menu when it changed (cheap to call every few minutes). Returns a short status."""
    s = CompanySettings.get()
    if not on(s) or s.suspended:
        return "off"
    if not mobile.api():
        return "no-api"
    d = data(s)
    h = hashlib.sha256(json.dumps(d, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
    f = _mark_file()
    try:
        mark = json.loads(f.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        mark = {}
    if not force and mark.get("h") == h:
        return "same"
    res = mobile.post("/menu", dict(mobile.ident(s), d=d))
    if res.get("error"):
        raise ConnectionError(res["error"])
    f.parent.mkdir(exist_ok=True)
    f.write_text(json.dumps(dict(h=h, t=time.time(), id=res.get("id", ""))), encoding="utf-8")
    return "sent"


def last_sent():
    try:
        return json.loads(_mark_file().read_text(encoding="utf-8")).get("t")
    except Exception:  # noqa: BLE001
        return None
