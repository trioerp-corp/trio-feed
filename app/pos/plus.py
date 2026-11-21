"""1.26 optional till features (each off by default, set from «إعدادات المحل»), kept in CompanySettings.menu_cfg["plus"]:

- pharm: the till shows each item's stock in the branch (strips / boxes) and offers in-stock alternatives with the same
  active ingredient (Item.extra generic / generic_ar). On by default for a pharmacy (it only shows information).
- sp: «البائع» chosen per invoice (Partner kind employee) + commission % per salesperson / per category → «عمولات البائعين».
- sales: «تخفيض موسمي» — % off categories or chosen items between two dates. It is an offer like pos.Promotion: the till
  applies it (catalog promos) and _promo_max accepts it, so it never counts as a manual discount.
- loy: loyalty points per X EGP for a customer known by his phone; points are redeemed as an invoice discount that the
  server checks against the balance (what the balance does not cover is a manual discount under the usual cap)."""
import datetime as dt
from decimal import ROUND_FLOOR, Decimal, InvalidOperation

from django.core.exceptions import ValidationError

from core.models import CompanySettings

Z = Decimal(0)


def _d(v, default=Z):
    try:
        x = Decimal(str(v).replace(",", ".").strip())
    except (InvalidOperation, ValueError, TypeError):
        return default
    return x if x.is_finite() else default


def _s(x):
    return format(Decimal(x).normalize(), "f") if x else "0"


def is_pharmacy(s):
    return isinstance(s.activity, dict) and s.activity.get("key") == "pharmacy"


def cfg(s=None):
    s = s or CompanySettings.get()
    c = dict((s.menu_cfg or {}).get("plus") or {})
    ph = c.get("pharm") if isinstance(c.get("pharm"), dict) else {}
    sp = c.get("sp") if isinstance(c.get("sp"), dict) else {}
    lo = c.get("loy") if isinstance(c.get("loy"), dict) else {}
    return {
        "pharm": {"on": bool(ph.get("on", is_pharmacy(s)))},
        "sp": {"on": bool(sp.get("on")), "pct": _d(sp.get("pct")), "people": {str(k): _d(v) for k, v in (sp.get("people") or {}).items()},
               "cats": {str(k): _d(v) for k, v in (sp.get("cats") or {}).items()}, "need": bool(sp.get("need"))},
        "sales": [x for x in c.get("sales") or [] if isinstance(x, dict)],
        "loy": {"on": bool(lo.get("on")), "per": _d(lo.get("per"), Decimal(10)), "val": _d(lo.get("val"), Decimal("0.1")),
                "min": int(_d(lo.get("min"), Decimal(50)))},
    }


def _save(key, val):
    s = CompanySettings.get()
    m = dict(s.menu_cfg or {})
    p = dict(m.get("plus") or {})
    p[key] = val
    m["plus"] = p
    s.menu_cfg = m
    s.save(update_fields=["menu_cfg"])


def _pct(v, what="النسبة"):
    x = _d(v, None)
    if x is None or x < 0 or x > 100:
        raise ValidationError(f"{what} رقم من 0 إلى 100")
    return _s(x.quantize(Decimal("0.01")))


def set_pharm(on):
    _save("pharm", {"on": bool(on)})


def set_sp(on, pct, people, cats, need=False):
    """people / cats: {id or category name: % text}; an empty % removes the row (the default applies)."""
    _save("sp", {"on": bool(on), "need": bool(need), "pct": _pct(pct or 0, "عمولة البائع"),
                 "people": {str(k): _pct(v, "عمولة البائع") for k, v in people.items() if str(v).strip()},
                 "cats": {str(k): _pct(v, "عمولة التصنيف") for k, v in cats.items() if str(v).strip()}})


def set_loy(on, per, val, mn):
    per_, val_, mn_ = _d(per, None), _d(val, None), _d(mn, None)
    if per_ is None or per_ <= 0:
        raise ValidationError("اكتب عدد الجنيهات لكل نقطة (أكبر من صفر)")
    if val_ is None or val_ < 0 or val_ > per_:
        raise ValidationError("قيمة النقطة عند الاستبدال من 0 إلى قيمة الشراء لكل نقطة")
    if mn_ is None or mn_ < 0:
        raise ValidationError("اكتب أقل عدد نقاط للاستبدال")
    _save("loy", {"on": bool(on), "per": _s(per_), "val": _s(val_), "min": int(mn_)})


# ------------------------------------------------------------------ B5 season sale («تخفيض موسمي»)
def _sale_ok(x, day):
    try:
        return bool(x.get("on", True)) and dt.date.fromisoformat(x["start"]) <= day <= dt.date.fromisoformat(x["end"])
    except (KeyError, ValueError, TypeError):
        return False


def save_sale(name, pct, start, end, cats=(), items=(), sid=None):
    p = _d(pct, None)
    if p is None or p <= 0 or p >= 100:
        raise ValidationError("نسبة التخفيض أكبر من صفر وأقل من 100")
    try:
        a, b = dt.date.fromisoformat(str(start)), dt.date.fromisoformat(str(end))
    except ValueError:
        raise ValidationError("اختر تاريخ البداية والنهاية")
    if b < a:
        raise ValidationError("تاريخ النهاية قبل البداية")
    cats, items = [str(c) for c in cats if str(c).strip()], [str(i) for i in items if str(i).strip()]
    if not cats and not items:
        raise ValidationError("اختر تصنيفًا أو أصنافًا للتخفيض")
    rows = cfg()["sales"]
    sid = sid or f"s{int(dt.datetime.now().timestamp() * 1000)}"
    row = {"id": sid, "name": (str(name).strip() or "تخفيض موسمي")[:80], "pct": _s(p.quantize(Decimal("0.01"))), "start": a.isoformat(),
           "end": b.isoformat(), "cats": cats, "items": items, "on": True}
    rows = [r for r in rows if r.get("id") != sid] + [row]
    _save("sales", rows)
    return row


def sale_action(sid, op):
    rows = cfg()["sales"]
    if op == "delete":
        rows = [r for r in rows if r.get("id") != sid]
    else:
        for r in rows:
            if r.get("id") == sid:
                r["on"] = not r.get("on", True)
    _save("sales", rows)


def _cat_of(item):
    if item.variant_of_id:
        return item.variant_of.category.name
    return item.category.name


def sale_pct(item, day, rows=None):
    """The biggest season-sale % that covers this item on `day` (a size / colour follows its product)."""
    best, name = Z, ""
    for x in (cfg()["sales"] if rows is None else rows):
        if not _sale_ok(x, day):
            continue
        codes = set(x.get("items") or [])
        hit = item.code in codes or (item.variant_of_id and item.variant_of.code in codes) or _cat_of(item) in (x.get("cats") or [])
        if hit and _d(x.get("pct")) > best:
            best, name = _d(x.get("pct")), x.get("name") or "تخفيض موسمي"
    return best, name


def sale_promos(day=None):
    """The season sales as till offers (kind «percent», one per item code)."""
    from inventory.models import Item

    day = day or dt.date.today()
    rows = [x for x in cfg()["sales"] if _sale_ok(x, day)]
    if not rows:
        return []
    out = []
    qs = Item.objects.filter(active=True, sale_price__isnull=False).select_related("category", "variant_of", "variant_of__category")
    for it in qs:
        p, n = sale_pct(it, day, rows)
        if p > 0:
            out.append({"item": it.code, "kind": "percent", "name": n, "value": _s(p), "min_qty": "1", "free_qty": "0",
                        "start": day.isoformat(), "end": day.isoformat()})
    return out


def slow_movers(days=60, branch=None):
    """Items with stock and no sale for `days` days, counted from the last sale or — never sold since — the first time the stock came
    in (today's purchase is not «slow»). Sizes / colours of a product are one row (the product): [dict(item, qty, value, last, days)]."""
    from django.db.models import Max, Min

    from inventory.models import Item, StockMoveLine, StockQuant

    from .models import OrderLine

    today = dt.date.today()
    since = today - dt.timedelta(days=int(days))
    last = dict(OrderLine.objects.filter(order__is_return=False, order__cancelled=False).values("item_id").annotate(m=Max("order__created_at_device"))
                .values_list("item_id", "m"))
    came = dict(StockMoveLine.objects.filter(move__state="posted", move__kind__in=["receipt", "opening", "adjust_in", "transfer"], qty__gt=0)
                .values("item_id").annotate(m=Min("move__date")).values_list("item_id", "m"))
    qs = Item.objects.filter(active=True, item_type="stock", qty_on_hand__gt=0).select_related("category", "variant_of", "variant_of__category")
    here = None
    if branch is not None:
        here = dict(StockQuant.objects.filter(warehouse=branch.warehouse, qty__gt=0).values_list("item_id", "qty"))
    rows = {}
    for it in qs:
        q = here.get(it.pk, Z) if here is not None else it.qty_on_hand
        if q <= 0:
            continue
        ls = last.get(it.pk)
        ls = ls.date() if ls else None
        ref = max(x for x in (ls, came.get(it.pk)) if x) if (ls or came.get(it.pk)) else None
        if ref and ref > since:
            continue
        model = it.variant_of or it
        r = rows.setdefault(model.pk, {"item": model, "qty": Z, "value": Z, "last": None, "ref": None, "n": 0})
        r["qty"] += q
        r["value"] += (it.avg_cost * q).quantize(Decimal("0.01"))
        r["n"] += 1
        if ls and (r["last"] is None or ls > r["last"]):
            r["last"] = ls
        if ref and (r["ref"] is None or ref > r["ref"]):
            r["ref"] = ref
    out = []
    for r in rows.values():
        r["days"] = (today - r["last"]).days if r["last"] else None
        r["idle"] = (today - r["ref"]).days if r["ref"] else None  # days since the last sale, or since the stock came in
        out.append(r)
    out.sort(key=lambda r: -r["value"])
    return out


# ------------------------------------------------------------------ B1 pharmacy at the till
def decorate(out, device, s=None):
    """Adds to the till catalog: «plus» (what is on) and, for the pharmacy, each item's branch stock + active ingredient."""
    from inventory.models import Item, StockMoveLine, StockQuant

    c = cfg(s)
    from ledger.models import Partner

    plus = {"pharm": c["pharm"]["on"], "loy": {k: str(v) for k, v in c["loy"].items()} if c["loy"]["on"] else None,
            "sp": None}
    if c["sp"]["on"]:
        plus["sp"] = {"need": c["sp"]["need"], "list": [{"id": p.pk, "name": p.name} for p in
                                                        Partner.objects.filter(kind="employee", active=True, merged_into__isnull=True).order_by("name")]}
    out["plus"] = plus
    if not c["pharm"]["on"]:
        return out
    codes = [i["code"] for i in out["items"]]
    ph = is_pharmacy(s or CompanySettings.get())
    info = {i.code: i for i in Item.objects.filter(code__in=codes).only("id", "code", "item_type", "extra", "unit")}
    ids = [i.pk for i in info.values() if i.item_type == "stock"]
    here = dict(StockQuant.objects.filter(item_id__in=ids, warehouse=device.branch.warehouse).values_list("item_id", "qty"))
    tracked = set(StockMoveLine.objects.filter(item_id__in=ids, move__state="posted",
                                               move__kind__in=["receipt", "opening", "adjust_in", "adjust_out", "transfer"])
                  .values_list("item_id", flat=True).distinct())
    for row in out["items"]:
        it = info.get(row["code"])
        if not it:
            continue
        x = it.extra or {}
        g = str(x.get("generic") or "").strip().lower()
        if g:
            row["g"] = g[:120]
        if x.get("generic_ar"):
            row["ga"] = str(x["generic_ar"])[:120]
        if it.pk in tracked or (ph and it.item_type == "stock"):  # a stock put in (purchase / opening / count) is shown and checked;
            # in a pharmacy every drug is — one never bought is «نفد الرصيد», and tapping it offers the alternatives
            row["bq"] = str(here.get(it.pk, Z))
    return out


# ------------------------------------------------------------------ B4 salesperson
def sp_pct(c, sp, cat):
    if cat and cat in c["cats"]:
        return c["cats"][cat]
    return c["people"].get(str(sp), c["pct"])


def take_sp(data):
    """The salesperson of a till invoice (a return / exchange follows its original invoice). Returns the Partner id or None."""
    from ledger.models import Partner

    from .models import Order

    v = str(data.get("sp") or "")
    if not v.isdigit():
        orig = data.get("ret_of") or data.get("exch_of")
        o = Order.objects.filter(uid=str(orig)).only("raw").first() if orig else None
        v = str((o.raw or {}).get("sp") or "") if o else ""
    if v.isdigit() and Partner.objects.filter(pk=int(v), kind="employee").exists():
        return int(v)
    return None


def commissions(start, end):
    """Per salesperson for the period: invoices, sales, returns, net and commission (line value after discounts)."""
    from collections import defaultdict

    from ledger.models import Partner

    from .models import OrderLine

    c = cfg()
    rows = defaultdict(lambda: {"n": set(), "sales": Z, "ret": Z, "comm": Z})
    qs = (OrderLine.objects.filter(order__cancelled=False, order__created_at_device__date__gte=start, order__created_at_device__date__lte=end,
                                   order__raw__has_key="sp")
          .select_related("order", "item__category", "item__variant_of__category"))
    for ln in qs:
        sp = (ln.order.raw or {}).get("sp")
        if not isinstance(sp, int):
            continue
        v = ln.qty * ln.price - ln.discount
        it = ln.item
        cat = (it.variant_of.category.name if it.variant_of_id else it.category.name)
        pct = sp_pct(c["sp"], sp, cat)
        r = rows[sp]
        r["n"].add(ln.order_id)
        if ln.order.is_return:
            r["ret"] += v
            r["comm"] -= v * pct / 100
        else:
            r["sales"] += v
            r["comm"] += v * pct / 100
    names = dict(Partner.objects.filter(pk__in=list(rows)).values_list("pk", "name"))
    out = [{"id": k, "name": names.get(k, "—"), "n": len(r["n"]), "sales": r["sales"].quantize(Decimal("0.01")), "ret": r["ret"].quantize(Decimal("0.01")),
            "net": (r["sales"] - r["ret"]).quantize(Decimal("0.01")), "comm": r["comm"].quantize(Decimal("0.01")),
            "pct": c["sp"]["people"].get(str(k), c["sp"]["pct"])} for k, r in rows.items()]
    return sorted(out, key=lambda r: -r["net"])


# ------------------------------------------------------------------ B6 loyalty points
def norm_phone(v):
    d = "".join(ch for ch in str(v or "") if ch.isdigit())
    if d.startswith("0020"):
        d = d[4:]
    if d.startswith("20") and len(d) == 12:
        d = d[2:]
    if len(d) == 10 and d.startswith("1"):
        d = "0" + d
    return d if 8 <= len(d) <= 15 else ""


def balance(phone, exclude=None):
    from .models import Order

    qs = Order.objects.filter(cancelled=False, raw__loy__ph=phone)
    if exclude:
        qs = qs.exclude(pk=exclude)
    tot = 0
    for raw in qs.values_list("raw", flat=True):
        lo = (raw or {}).get("loy") or {}
        tot += int(lo.get("earn") or 0) - (int(lo.get("use") or 0) if lo.get("ok") else 0)
    return tot


def who(phone):
    from ledger.models import Partner

    from .delivery import DeliveryCustomer

    p = Partner.objects.filter(kind="customer", phone=phone, merged_into__isnull=True).first()
    if p:
        return p.name
    d = DeliveryCustomer.objects.filter(phone=phone).first()
    return d.name if d else ""


LOY_SALT, LOY_AGE = "trio-loy-look", 2 * 86400


def lookup(phone, device=None):
    """The balance the till shows before redeeming + a signed token of what it saw: a redemption the balance no longer
    covers at sync (used at another till, loyalty turned off) is still posted within that token (flag «نقاط غير مغطاة»)."""
    import secrets

    from django.core import signing

    c = cfg()["loy"]
    ph = norm_phone(phone)
    if not c["on"]:
        raise ValidationError("نقاط الولاء غير مفعلة")
    if not ph:
        raise ValidationError("اكتب رقم موبايل صحيح")
    b = balance(ph)
    tok = signing.dumps({"ph": ph, "b": b, "v": str(c["val"]), "m": c["min"], "d": device.pk if device else None, "n": secrets.token_hex(4)}, salt=LOY_SALT)
    return {"phone": ph, "name": who(ph), "points": b, "value": str((Decimal(b) * c["val"]).quantize(Decimal("0.01"))),
            "min": c["min"], "val": str(c["val"]), "per": str(c["per"]), "tok": tok}


def _loy_seen(lo, ph, use, amt, device, pk):
    """What the till's signed lookup covers: the points it showed (≥ use) at the value it showed. Returns the amount or None."""
    from django.core import signing

    from .models import Order

    tok = str(lo.get("tok") or "")
    if not tok:
        return None
    try:
        t = signing.loads(tok, salt=LOY_SALT, max_age=LOY_AGE)
    except signing.BadSignature:
        return None
    if t.get("ph") != ph or (device is not None and t.get("d") not in (None, device.pk)):
        return None
    if use < int(t.get("m") or 0) or use > int(t.get("b") or 0):
        return None
    cap = (Decimal(use) * _d(t.get("v"))).quantize(Decimal("0.01"))
    if amt <= 0 or amt > cap + Decimal("0.01"):
        return None
    if Order.objects.filter(cancelled=False, raw__loy__tok=tok, raw__loy__ok=True).exclude(pk=pk).exists():
        return None  # one lookup covers one invoice
    return min(amt, cap)


def loy_check(data, is_return, device=None, pk=None):
    """Before the discount check: the points redeemed on this sale. Returns the approved amount (what the balance and
    the rules cover); the rest stays a manual discount. Cleans data["loy"].
    A redemption the balance no longer covers (spent at another till meanwhile, loyalty turned off) never blocks the sale:
    within the till's signed lookup (the points and value it showed) it is posted, the balance goes below zero and the
    order carries «نقاط غير مغطاة» for the owner."""
    c = cfg()["loy"]
    lo = data.get("loy") if isinstance(data.get("loy"), dict) else None
    if lo is None:
        return Z, ""
    ph = norm_phone(lo.get("ph"))
    use = int(_d(lo.get("use")))
    amt = _d(lo.get("amt")).quantize(Decimal("0.01"))
    if not ph or (not c["on"] and (is_return or use <= 0)):
        data.pop("loy", None)
        return Z, ""
    clean = {"ph": ph, "n": str(lo.get("n") or "")[:80], "use": max(use, 0), "amt": str(amt), "ok": False, "earn": 0}
    if lo.get("tok"):
        clean["tok"] = str(lo["tok"])[:400]
    data["loy"] = clean
    if is_return or use <= 0:
        clean["use"], clean["amt"] = 0, "0"
        return Z, ""

    def uncovered(why):
        seen = _loy_seen(clean, ph, use, amt, device, pk)
        if seen is None:
            if not c["on"]:
                data.pop("loy", None)
            return Z, why
        clean["ok"], clean["unc"], clean["amt"] = True, True, str(seen)
        return seen, f"نقاط غير مغطاة: استبدال {use} نقطة ({seen} ج) — {why}"

    if not c["on"]:
        return uncovered("نقاط الولاء أُوقفت")
    bal = balance(ph)
    want = (Decimal(use) * c["val"]).quantize(Decimal("0.01"))
    if use < c["min"]:
        return uncovered(f"استبدال {use} نقطة أقل من الحد ({c['min']})")
    if use > bal:
        return uncovered(f"الرصيد وقت المزامنة {bal}")
    if abs(want - amt) > Decimal("0.01"):
        return uncovered(f"قيمة النقاط {amt} ≠ {want}")
    clean["ok"] = True
    return amt, f"استبدال {use} نقطة ({amt} ج)"


def loy_earn(order, data):
    """After the totals: the points this invoice earns (a return takes back what its value earned)."""
    lo = data.get("loy")
    c = cfg()["loy"]
    if not isinstance(lo, dict) or not c["on"]:
        if order.is_return and c["on"]:  # a return of a points sale takes its points back, even if the phone was not typed again
            from .models import Order

            orig = Order.objects.filter(uid=str(data.get("ret_of") or data.get("exch_of") or "")).only("raw").first()
            olo = (orig.raw or {}).get("loy") if orig else None
            if isinstance(olo, dict) and olo.get("ph"):
                n = int((order.total / c["per"]).to_integral_value(ROUND_FLOOR))
                data["loy"] = {"ph": olo["ph"], "n": olo.get("n", ""), "use": 0, "amt": "0", "ok": False, "earn": -min(n, int(olo.get("earn") or 0))}
        return
    n = int((order.total / c["per"]).to_integral_value(ROUND_FLOOR)) if order.total > 0 else 0
    lo["earn"] = -n if order.is_return else n


def loyalty_rows():
    from collections import defaultdict

    from .models import Order

    agg = defaultdict(lambda: {"earn": 0, "use": 0, "n": 0, "name": "", "last": None})
    for raw, at in Order.objects.filter(cancelled=False, raw__has_key="loy").values_list("raw", "created_at_device"):
        lo = (raw or {}).get("loy") or {}
        ph = lo.get("ph")
        if not ph:
            continue
        r = agg[ph]
        r["earn"] += int(lo.get("earn") or 0)
        r["use"] += int(lo.get("use") or 0) if lo.get("ok") else 0
        r["n"] += 1
        r["name"] = r["name"] or lo.get("n") or ""
        r["last"] = max(r["last"], at) if r["last"] else at
    val = cfg()["loy"]["val"]
    out = []
    for ph, r in agg.items():
        b = r["earn"] - r["use"]
        out.append({"phone": ph, "name": r["name"] or who(ph), "earn": r["earn"], "use": r["use"], "bal": b, "value": (Decimal(b) * val).quantize(Decimal("0.01")),
                    "n": r["n"], "last": r["last"]})
    return sorted(out, key=lambda r: -r["bal"])
