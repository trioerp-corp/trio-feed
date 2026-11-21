"""Server side of the offline-first POS: idempotent sync, posting, shift close."""
import datetime as dt
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from core.models import CompanySettings
from inventory import services as INV
from inventory.models import Item, StockMove, StockMoveLine, StockQuant
from ledger import services as L
from ledger.models import Account

from .models import Order, OrderLine, Shift

SALES, VAT_OUT, COGS = "4101010101", "2301040301", "3101010101"


def card_clearing():
    parent = Account.objects.get(code="12070101")
    acc, _ = Account.objects.get_or_create(code="1207010108", defaults=dict(
        name="وسيط مدفوعات البطاقات والمحافظ (نقاط البيع)", parent=parent, level=6, is_postable=True, account_type="asset",
        normal_balance="debit", report="balance_sheet", cash_flow_category="cash"))
    return acc


def service_revenue():
    """Revenue of services (play time, delivery…) kept apart from the sale of goods."""
    acc = Account.objects.filter(code="4101010102").first()
    if acc:
        return acc
    return Account.objects.create(code="4101010102", name="إيرادات خدمات وساعات تشغيل", parent=Account.objects.get(code="41010101"), level=6,
                                  is_postable=True, account_type="income", normal_balance="credit", report="profit_loss",
                                  cash_flow_category="operating", currencies="EGP", dim1_policy="optional")


def stock_lines(item, qty):
    """What leaves the store when `qty` of `item` is sold: the item itself, its recipe ingredients, or nothing (a service)."""
    if item.item_type == "service" and not hasattr(item, "recipe"):
        return []
    if item.item_type in ("recipe", "service"):  # a clinic service's recipe = the consumables one visit uses
        r = getattr(item, "recipe", None)
        if r is None:
            return []
        out = []
        for ln in r.lines.select_related("ingredient"):
            q = (qty * ln.qty * (1 + ln.waste_pct / 100)).quantize(Decimal("0.001"))
            if q > 0:
                out.append((ln.ingredient, q))
        return out
    return [(item, qty)]


def _tracked(item):
    """Has the shop ever put this item's stock in (a purchase, an opening balance or a count)? A hall / café item sold before its
    first purchase is not a «review» case on every bill — the reorder alert and the stock pages already show it."""
    if item.item_type != "stock":
        return False
    return StockMoveLine.objects.filter(item=item, move__state="posted", move__kind__in=["receipt", "opening", "adjust_in", "adjust_out", "harvest"]).exists()


def _dt(s):
    d = parse_datetime(s) if isinstance(s, str) else s
    if d and timezone.is_naive(d):
        d = timezone.make_aware(d)
    return d or timezone.now()


def promos():
    import datetime as _dt

    from .models import Promotion

    today = _dt.date.today()
    return [{"item": p.item.code, "kind": p.kind, "name": p.name, "value": str(p.value), "min_qty": str(p.min_qty), "free_qty": str(p.free_qty),
             "start": p.start.isoformat(), "end": p.end.isoformat()}
            for p in Promotion.objects.filter(active=True, end__gte=today).select_related("item")] + __import__("pos.plus", fromlist=["x"]).sale_promos(today)


def customer_balance(partner):
    from django.db.models import Sum

    from ledger.models import JournalLine

    r = JournalLine.objects.filter(account__code="1203010101", partner=partner, entry__state__in=["posted", "reversed"]).aggregate(
        d=Sum("debit"), c=Sum("credit"))
    return (r["d"] or Decimal(0)) - (r["c"] or Decimal(0))


def customers():
    from ledger.models import Partner

    out = []
    for p in Partner.objects.filter(kind="customer", active=True, merged_into__isnull=True).order_by("name"):
        out.append({"id": p.pk, "name": p.name, "phone": p.phone, "balance": str(customer_balance(p)),
                    "limit": str(p.credit_limit) if p.credit_limit is not None else None})
    return out


def catalog(device):
    s = CompanySettings.get()
    from django.contrib.auth import get_user_model

    from ledger.models import Partner

    from core.activities import current

    xfields = current(s)["fields"]
    from django.db.models import Q

    # 1.22: a product with sizes is ONE button (its sizes inside it); add-ons go with an order line, not on the grid
    sizes = {}
    for v in Item.objects.filter(active=True, variant_of__isnull=False, variant_of__active=True, sale_price__isnull=False).order_by("id"):
        sizes.setdefault(v.variant_of_id, []).append(v)
    items = Item.objects.filter(Q(sale_price__isnull=False) | Q(pk__in=list(sizes)), active=True, variant_of__isnull=True, is_addon=False).exclude(
        category__name__startswith="مستلزمات تشغيل").exclude(code="PLAY").values(  # play time is billed by the hall screen only
        "id", "code", "name", "barcode", "sale_price", "vat_rate", "unit", "category__name", "extra", "aliases", "item_type", "qty_on_hand", "reorder_level")

    items = list(items)
    _fresh = __import__("inventory.reorder", fromlist=["x"]).never_stocked(
        [i["id"] for i in items if i["item_type"] == "stock" and (i["reorder_level"] or 0) > 0 and (i["qty_on_hand"] or 0) <= 0])

    def _vat(x):
        return str(x if x is not None else s.default_vat_rate)

    # 1.25 sizes × colours: each cell carries its size / colour, barcode and the branch's stock (the till's picker)
    grid_ids = [v.pk for vs in sizes.values() for v in vs if v.item_type == "stock" and ("size" in (v.extra or {}) or "color" in (v.extra or {}))]
    here = dict(StockQuant.objects.filter(item_id__in=grid_ids, warehouse=device.branch.warehouse).values_list("item_id", "qty")) if grid_ids else {}

    def _cell(v):
        x = v.extra or {}
        if v.pk not in grid_ids and not ("size" in x or "color" in x):
            return {}
        return {"sz": x.get("size", ""), "c": x.get("color", ""), "b": v.barcode, "q": str(here.get(v.pk, 0))}

    def _sizes(pk):
        vs = sorted(sizes.get(pk, []), key=lambda v: (v.extra or {}).get("order", 0))
        return [{"code": v.code, "n": v.size_name, "p": str(v.sale_price), "v": _vat(v.vat_rate), **_cell(v)} for v in vs]

    addons = [{"code": a.code, "name": a.name, "p": str(a.sale_price), "v": _vat(a.vat_rate), "cats": [c.name for c in a.addon_for.all()]}
              for a in Item.objects.filter(active=True, is_addon=True, sale_price__isnull=False).prefetch_related("addon_for").order_by("name")]
    from inventory.models import ItemUnit

    units = {}
    for u in ItemUnit.objects.all():
        units.setdefault(u.item_id, []).append({"name": u.name, "factor": str(u.factor), "barcode": u.barcode,
                                                "price": str(u.sale_price) if u.sale_price is not None else None})
    cashiers = get_user_model().objects.filter(is_active=True).filter(groups__name__in=TILL_GROUPS).distinct()
    return __import__("pos.plus", fromlist=["x"]).decorate({
        "branch": {"code": device.branch.code, "name": device.branch.name, "footer": device.branch.receipt_footer, "policy": receipt_policy(s)},
        "device": device.code, "next_seq": next_seq(device), "company": s.name, "phone": s.phone, "address": s.address, "tax_id": s.tax_registration,
        "prices_include_vat": s.pos_prices_include_vat, "neg": s.allow_negative_stock,
        "default_vat": str(s.default_vat_rate),
        "logo": s.logo_svg,
        "kitchen": s.kitchen_ticket if s.business in ("cafe", "gaming") else "off", "biz": s.business,
        "tables": __import__("pos.tables", fromlist=["x"]).cfg(s), "can_edit": s.cashier_can_edit, "disc_max": str(till_cfg(s)["disc_max"]),
        **_extras(s),
        "addons": addons,
        "scale": {"prefix": s.scale_prefix, "mode": s.scale_mode, "digits": s.scale_item_digits},
        "promos": promos(),
        "employees": [{"id": p.pk, "name": p.name} for p in Partner.objects.filter(kind="employee", active=True, merged_into__isnull=True).order_by("name")],
        "suppliers": [{"id": p.pk, "name": p.name} for p in Partner.objects.filter(kind="supplier", active=True, merged_into__isnull=True).order_by("name")],
        "expenses": [{"id": a.pk, "name": a.name} for a in __import__("pos.money", fromlist=["x"]).expense_accounts()],
        "cashiers": [{"username": u.username, "name": u.first_name or u.username, **({"w": True} if till_role(u) == "waiter" else {})} for u in cashiers],
        "customers": customers(),
        "items": [{**{k: v for k, v in i.items() if k not in ("category__name", "id", "extra", "aliases", "item_type", "qty_on_hand", "reorder_level")},
                   "cat": i["category__name"],
                   # «حد الطلب»: the till warns when a sale takes the stock down to it (refreshed with every catalog load)
                   # (1.24.1: not for an item never bought yet — a sample / new menu item would warn on its first sale)
                   **({"q": str(i["qty_on_hand"]), **({} if i["id"] in _fresh else {"r": str(i["reorder_level"])})}
                      if i["item_type"] == "stock" and (i["reorder_level"] or 0) > 0 else {}), "units": units.get(i["id"], []),
                   "x": " ".join([str(v) for v in (i["extra"] or {}).values() if v and not isinstance(v, dict)] + ([i["aliases"]] if i["aliases"] else [])
                                 + [str(x) for k in ("sizes", "colors") for x in ((i["extra"] or {}).get("grid") or {}).get(k, [])]),
                   "xs": " · ".join(str((i["extra"] or {}).get(f["k"])) for f in xfields if f["s"] and (i["extra"] or {}).get(f["k"]))[:60],
                   **({"sizes": _sizes(i["id"])} if i["id"] in sizes else {}),
                   "d": str((i["extra"] or {}).get("desc", ""))[:120],
                   "sale_price": str(i["sale_price"] if i["sale_price"] is not None else min(v.sale_price for v in sizes[i["id"]])),
                   "vat_rate": _vat(i["vat_rate"])} for i in items],
    }, device, s)


def _extras(s):
    """1.24: delivery (settings + address book), service charge %, kitchen stations — for the till."""
    from . import delivery as D
    from . import restaurant as R

    d, svc = D.cfg(s), R.svc_cfg(s)
    if d["on"]:
        D.fee_item()
    if svc["on"]:
        R.svc_item()
    return {"delivery": d, "dbook": D.book() if d["on"] else [], "svc": svc,
            "stations": [dict(name=x["name"], cats=x["cats"]) for x in R.stations(s)] if s.business in R.BIZ else []}


def _lowest_price(item):
    """The lowest price per base unit the owner set for an item (its own price, or a cheaper box / strip unit price)."""
    from inventory.models import ItemUnit

    low = item.sale_price
    for u in ItemUnit.objects.filter(item=item, sale_price__isnull=False):
        if u.factor and u.factor > 0:
            low = min(low, u.sale_price / u.factor)
    return low


def _same_sale(old, new):
    """Is this the same receipt sent again (answer lost on the way), or another sale that got the same number?"""
    old = old or {}
    if not old.get("created_at") or not new.get("created_at"):
        return True
    return old.get("created_at") == new.get("created_at") and len(old.get("lines") or []) == len(new.get("lines") or [])


def next_seq(device):
    """The highest number this device ever used for a receipt / cash paper / request — the till continues after it even when
    its own counter is gone (new browser, cleared data, Windows reinstalled)."""
    from .models import CashMove, ChangeRequest

    pre, top = f"{device.branch.code}-{device.code}-", 0
    for qs in (Order.objects.filter(device=device), CashMove.objects.filter(uid__startswith=pre + "M"), ChangeRequest.objects.filter(uid__startswith=pre + "R")):
        for uid in qs.order_by("-id").values_list("uid", flat=True)[:40]:
            tail = uid[len(pre):].split("-")[0].split("~")[0].lstrip("MR")
            if uid.startswith(pre) and tail.isdigit():
                top = max(top, int(tail))
    return top


NOS_BLOCK = 50


@transaction.atomic
def reserve_nos(device, n=NOS_BLOCK):
    """Receipt numbers for one till, handed out in blocks so it keeps numbering offline. Never twice: each block starts after
    the highest number this device ever reserved or used (two tabs / a new browser get new numbers)."""
    from .models import Device

    d = Device.objects.select_for_update().get(pk=device.pk)
    n = max(1, min(int(n or NOS_BLOCK), 500))
    start = max(d.next_no, next_seq(d)) + 1
    d.next_no = start + n - 1
    d.save(update_fields=["next_no"])
    return start, d.next_no


@transaction.atomic
def ingest_order(device, data, renumber=False):
    """Create and post one order. Returns (order, created). A uid already received is acknowledged, never doubled."""
    uid = str(data["uid"])
    if not uid.startswith(f"{device.branch.code}-{device.code}-"):
        raise ValidationError("المعرف لا يخص هذا الجهاز")
    existing = Order.objects.filter(uid=uid).first()
    reused = False
    if existing:
        if not renumber or _same_sale(existing.raw, data):
            return existing, False
        # the till lost its counter (browser data wiped, or the same device key opened in a second browser) and numbered a NEW sale
        # with a used number: it is kept under the number + «~n», never dropped
        twins = list(Order.objects.filter(uid__startswith=uid + "~"))
        for t in twins:
            if _same_sale(t.raw, data):
                return t, False
        uid, reused = f"{uid}~{len(twins) + 2}", True
    s = CompanySettings.get()
    branch = device.branch
    shift = Shift.objects.filter(uid=data.get("shift_uid"), device=device).first()
    is_return = bool(data.get("is_return"))
    ret_note = check_return(device, data) if is_return and renumber else ""  # renumber = it came from the till's sync
    order = Order.objects.create(uid=uid, device=device, shift=shift, created_at_device=_dt(data.get("created_at")),
                                 total=0, paid_cash=Decimal(str(data.get("paid_cash", 0))), paid_card=Decimal(str(data.get("paid_card", 0))),
                                 paid_credit=Decimal(str(data.get("paid_credit", 0) or 0)), is_return=is_return, raw=data,
                                 paid_rider=Decimal(str(data.get("paid_rider", 0) or 0)),
                                 shift_seq=data.get("shift_seq") or None)
    if min(order.paid_cash, order.paid_card, order.paid_credit, order.paid_rider) < 0:
        raise ValidationError("مبلغ مدفوع بالسالب — لم يُسجَّل")
    if order.paid_rider and (is_return or not isinstance(data.get("delivery"), dict) or order.paid_rider < 0):
        raise ValidationError("«مع الطيار» لطلبات الدليفري فقط")
    if order.paid_credit:
        from ledger.models import Partner

        cid = str(data.get("customer_id") or "")
        new = data.get("customer_new") if isinstance(data.get("customer_new"), dict) else None
        if cid.isdigit():
            order.customer = Partner.objects.filter(pk=int(cid), kind="customer").first()
        elif new and str(new.get("name", "")).strip():
            # a customer added at the till (1.21): made with his first credit sale; the same name + phone again is the same customer
            name, phone = str(new["name"]).strip()[:150], str(new.get("phone") or "").strip()[:40]
            order.customer = (Partner.objects.filter(kind="customer", name=name, phone=phone, merged_into__isnull=True).first()
                              or Partner.objects.create(kind="customer", name=name, phone=phone))
        else:
            order.customer = None
        if not order.customer:
            raise ValidationError("البيع الآجل يحتاج إلى عميل مسجّل")
        lim = order.customer.credit_limit
        if lim is not None and not is_return and customer_balance(order.customer) + order.paid_credit > lim:
            flags_pre = f"عدّى حد مديونية {order.customer.name} ({lim})"
        else:
            flags_pre = ""
    else:
        flags_pre = ""
    total = vat = Decimal(0)
    flags = [x for x in (flags_pre, ret_note) if x]
    sales_acc, cogs, by_rev = Account.objects.get(code=SALES), Account.objects.get(code=COGS), {}
    move = StockMove.objects.create(kind="return" if is_return else "issue", date=order.created_at_device.date(), warehouse=branch.warehouse,
                                    description=f"نقطة بيع {uid}", legacy_number=uid)
    under, gross, manual, day = [], Decimal(0), Decimal(0), order.created_at_device.date()
    swap = _swap_prices(data) if renumber and not is_return else {}
    for ln in data.get("lines", []):
        item = Item.objects.filter(code=ln["item_code"]).first()
        if item is None:
            raise ValidationError(f"صنف غير معروف: {ln['item_code']}")
        qty, price, disc = Decimal(str(ln["qty"])), Decimal(str(ln["price"])), Decimal(str(ln.get("discount", 0)))
        if qty <= 0 or price < 0 or disc < 0 or qty * price - disc < 0:
            raise ValidationError(f"كمية أو سعر غير صحيح في «{item.name}»")
        if item.variant_of_id is None and isinstance((item.extra or {}).get("grid"), dict):  # 1.25: the picker sells a size / colour
            raise ValidationError(f"«{item.name}» صنف بمقاسات وألوان — اختر المقاس واللون")
        if renumber and not is_return and item.sale_price and price < _lowest_price(item) - Decimal("0.005") \
                and not price >= swap.get(item.variant_of_id or item.pk, price + 1) - Decimal("0.005"):  # a swap at the price paid is not under
            under.append(f"«{item.name}» بيع بـ {L.q2(price)} والسعر {L.q2(_lowest_price(item))}")
        gross += qty * price
        if renumber and not is_return and disc > 0:  # 1.25: what the offers do not explain is a manual discount
            manual += max(Decimal(0), disc - _promo_max(item, qty, price, day))
        line_total = L.q2(qty * price - disc)
        rate = item.vat_rate if item.vat_rate is not None else s.default_vat_rate
        line_vat = L.q2(line_total * rate / (100 + rate)) if s.pos_prices_include_vat else L.q2(line_total * rate / 100)
        if not s.pos_prices_include_vat:
            line_total += line_vat
        total += line_total
        vat += line_vat
        OrderLine.objects.create(order=order, item=item, qty=qty, price=price, discount=disc)
        rev = service_revenue() if item.item_type == "service" else sales_acc
        by_rev[rev] = by_rev.get(rev, Decimal(0)) + line_total - line_vat
        for ing, iq in stock_lines(item, qty):
            if not is_return:
                q = StockQuant.objects.filter(item=ing, warehouse=branch.warehouse).first()
                if (q.qty if q else 0) < iq and _tracked(ing):  # only a stock the shop counts (bought / opened / counted) can go short
                    flags.append(f"رصيد {ing.name} كان {q.qty if q else 0} وقت المزامنة")
            uc = None
            if item.item_type == "recipe" and not is_return:  # menu item: each ingredient at its last purchase invoice price
                from inventory.recipes import unit_cost

                uc = unit_cost(ing) or None
            StockMoveLine.objects.create(move=move, item=ing, qty=iq, dim1=branch.cost_center, account=cogs, unit_cost=uc)
    if renumber and is_return and data.get("ret_of"):
        check_ret_of(data, order.pk)
    from . import plus

    sp = plus.take_sp(data)  # 1.26 «البائع» (a return follows its original invoice)
    if sp:
        data["sp"] = sp
    else:
        data.pop("sp", None)
    loy_amt, loy_note = plus.loy_check(data, is_return, device, order.pk)  # 1.26 points redeemed = a discount the balance approves
    if loy_note:
        flags.append(loy_note)
    if renumber and not is_return:
        manual = max(Decimal(0), manual - loy_amt)
        note = check_discount(device, data, L.q2(manual), gross, order.pk)
        if note:
            flags.append(note)
    from treasury.models import DayClose

    if DayClose.objects.filter(date__gte=order.created_at_device.date()).exists():
        flags.append("وصلت بعد التقفيل النهائي لليوم")
    paid = order.paid_cash + order.paid_card + order.paid_credit + order.paid_rider
    from . import restaurant

    svc_flag = restaurant.check_svc(data)
    if svc_flag:
        flags.append(svc_flag)
    if reused:
        flags.append("كرّر الجهاز رقم فاتورة قديمًا — سُجّلت برقم بديل")
    if under:
        flags.append("بأقل من السعر: " + "، ".join(under[:3]))
    if paid and abs(L.q2(paid) - L.q2(total)) <= Decimal("0.05") * max(1, len(data.get("lines") or [])) and L.q2(paid) != L.q2(total):
        # the till rounds each line in the browser; a piastre of difference is rounding, not a wrong payment: the receipt's total wins
        total = L.q2(paid)
    if L.q2(paid) != L.q2(total):
        flags.append(f"المدفوع {paid} ≠ الإجمالي {total}")
    net = total - vat
    sign_cash, sign_sale = ("credit", "debit") if is_return else ("debit", "credit")
    lines = []
    if order.paid_cash:
        lines.append({"account": branch.cashbox.account, sign_cash: order.paid_cash, "description": f"بيع {uid}"})
    if order.paid_card:
        lines.append({"account": card_clearing(), sign_cash: order.paid_card, "description": f"بيع {uid}"})
    if order.paid_credit:
        lines.append({"account": Account.objects.get(code="1203010101"), "partner": order.customer, sign_cash: order.paid_credit,
                      "description": f"بيع آجل {uid}"})
    if order.paid_rider:  # 1.24 delivery: the money is with the rider until «تم التحصيل» at a till
        from .delivery import clearing

        lines.append({"account": clearing(), sign_cash: order.paid_rider, "description": f"دليفري مع الطيار {uid}"})
    diff = L.q2(total - paid)
    if diff:
        lines.append({"account": card_clearing(), sign_cash: abs(diff) if diff > 0 else 0, sign_sale: abs(diff) if diff < 0 else 0,
                      "description": "فرق مدفوعات للمراجعة"})
    # revenue per account (goods / services such as play time); rounding of the VAT split stays on the goods line
    rest = net - sum(by_rev.values(), Decimal(0))
    for acc, amt in sorted(by_rev.items(), key=lambda kv: kv[0].code):
        if acc == sales_acc:
            amt += rest
            rest = Decimal(0)
        if amt:
            lines.append({"account": acc, sign_sale: amt, "dim1": branch.cost_center, "description": f"مبيعات {uid}"})
    if rest:
        lines.append({"account": sales_acc, sign_sale: rest, "dim1": branch.cost_center, "description": f"مبيعات {uid}"})
    if vat:
        lines.append({"account": Account.objects.get(code=VAT_OUT), sign_sale: vat, "description": f"ق.م {uid}"})
    if lines or not (data.get("zero_ok") and device.code == "CL"):  # zero_ok: a clinic's free follow-up — no money, no revenue; only its stock move posts
        order.entry = L.create_entry("SI", order.created_at_device.date(), f"نقطة بيع {branch.name} {uid}", lines, source=f"pos.order:{order.pk}",
                                     legacy_ref=uid)
    if move.lines.exists():
        INV.post_move(move, allow_negative=True)
        order.stock_move = move
    else:  # only services (play time…): nothing leaves the store
        move.delete()
    order.total, order.vat, order.flags = total, vat, "؛ ".join(flags)[:300]
    plus.loy_earn(order, data)
    order.raw = data
    order.save()
    if isinstance(data.get("delivery"), dict) and not is_return:
        from .delivery import record

        record(order, data)
    return order, True


# ---------------------------------------------------------------- 1.25 till: manual discount, exchange from the original invoice
DISC_SALT = "pos.disc"


def till_cfg(s=None):
    """CompanySettings.menu_cfg["till"] = {disc_max: % a cashier may give without the owner (0 = none)}."""
    s = s or CompanySettings.get()
    c = dict((s.menu_cfg or {}).get("till") or {})
    try:
        if c.get("disc_max") is None:  # never set: a clothes / shoes shop bargains, so the cashier may give 5 % by default
            from core.activities import APPAREL, current

            c["disc_max"] = 5 if current(s).get("key") in APPAREL else 0
        mx = Decimal(str(c.get("disc_max") or 0))
    except (ArithmeticError, ValueError):
        mx = Decimal(0)
    mx = min(max(mx, Decimal(0)), Decimal(100)) if mx.is_finite() else Decimal(0)
    return {"disc_max": Decimal(format(mx.normalize(), "f"))}


POLICY_APPAREL = "الاستبدال خلال 14 يومًا بالفاتورة والبضاعة بحالتها الأصلية"


def receipt_policy(s=None):
    """«سياسة الاستبدال والاسترجاع» printed on the receipt (menu_cfg["till"]["policy"]); never set → the usual one for clothes / shoes."""
    s = s or CompanySettings.get()
    p = ((s.menu_cfg or {}).get("till") or {}).get("policy")
    if p is None:
        from core.activities import APPAREL, current

        try:
            return POLICY_APPAREL if current(s).get("key") in APPAREL else ""
        except Exception:
            return ""
    return p


def set_till_cfg(disc_max, policy=None):
    s = CompanySettings.get()
    try:
        mx = Decimal(str(disc_max).replace(",", ".").strip() or "0")
    except (ArithmeticError, ValueError):
        raise ValidationError("اكتب النسبة بالأرقام")
    if not mx.is_finite() or mx < 0 or mx > 100:
        raise ValidationError("النسبة من 0 إلى 100")
    m = dict(s.menu_cfg or {})
    m["till"] = {**dict(m.get("till") or {}), "disc_max": str(L.q2(mx))}
    if policy is not None:
        m["till"]["policy"] = " ".join(str(policy).split())[:300]
    s.menu_cfg = m
    s.save(update_fields=["menu_cfg"])


def disc_token(device, user, amount=None):
    """The owner's / manager's approval of a discount: for one amount (typed on the till), or — amount None — for every
    discount of the shift he opens himself (he may always discount)."""
    from django.core import signing

    return signing.dumps({"d": device.pk, "u": user.username, "a": None if amount is None else str(L.q2(Decimal(str(amount)))),
                          "n": __import__("secrets").token_hex(6)}, salt=DISC_SALT)


def _promo_max(item, qty, price, day):
    """The largest discount the active offers give this line (the till computes the same) — the rest is a manual discount."""
    from .models import Promotion

    best = Decimal(0)
    for p in Promotion.objects.filter(item=item, active=True, start__lte=day, end__gte=day):
        v, mn, fr, d = p.value, p.min_qty, p.free_qty, Decimal(0)
        if p.kind == "percent" and qty >= mn:
            d = qty * price * v / 100
        elif p.kind == "amount" and qty >= mn:
            d = qty * v
        elif p.kind == "bxgy" and mn + fr > 0:
            d = (qty // (mn + fr)) * fr * price
        elif p.kind == "qty_price" and qty >= mn and v < price:
            d = qty * (price - v)
        best = max(best, d)
    from .plus import sale_pct  # 1.26 «تخفيض موسمي» is an offer too

    sp, _ = sale_pct(item, day)
    if sp > 0:
        best = max(best, qty * price * sp / 100)
    return L.q2(best) + Decimal("0.01") if best > 0 else Decimal(0)  # a piastre of rounding on an offer line


def check_discount(device, data, manual, gross, pk=None):
    """A manual discount above the owner's % needs his approval (signed token from /pos/api/disc/ok/ or the shift he opened).
    Returns the note kept on the order (the owner sees it on the sales page); raises when it has no approval."""
    from django.core import signing

    if manual <= Decimal("0.01"):
        return ""
    pct = (manual / gross * 100) if gross > 0 else Decimal(100)
    mx = till_cfg()["disc_max"]
    d = data.get("disc") if isinstance(data.get("disc"), dict) else {}
    txt = f"خصم يدوي {manual} ({L.q2(pct)}%)"
    if pct <= mx + Decimal("0.01"):
        return f"{txt} — في حدود الكاشير"
    tok = str(d.get("tok") or "")
    try:
        t = signing.loads(tok, salt=DISC_SALT, max_age=RET_AGE)
    except signing.BadSignature:
        raise ValidationError(f"{txt} أكبر من المسموح للكاشير ({mx}%) دون موافقة صاحب المحل — لم يُسجَّل")
    if t.get("d") != device.pk:
        raise ValidationError("موافقة الخصم لا تخص هذا الجهاز — لم يُسجَّل")
    if t.get("a") is None:  # the owner's own shift-wide approval: only on the shift he opened himself
        sh = Shift.objects.filter(uid=data.get("shift_uid"), device=device).first()
        if not sh or sh.cashier != t.get("u"):
            raise ValidationError("موافقة الخصم لصاحب المحل في ورديته فقط — لم يُسجَّل")
    if t.get("a") is not None:
        if manual > Decimal(t["a"]) + Decimal("0.01"):
            raise ValidationError(f"{txt} أكبر من الخصم المعتمد ({t['a']}) — لم يُسجَّل")
        if Order.objects.filter(raw__disc__tok=tok).exclude(pk=pk).exists():
            raise ValidationError("موافقة الخصم استُخدمت من قبل — لم يُسجَّل")
    return f"{txt} — بموافقة {t.get('u')}"


def returned_qty(uid, exclude=None):
    """What was already given back from an invoice (returns / exchanges made from it), per item code."""
    out = {}
    for o in Order.objects.filter(raw__ret_of=uid, is_return=True, cancelled=False).exclude(pk=exclude).prefetch_related("lines__item"):
        for ln in o.lines.all():
            out[ln.item.code] = out.get(ln.item.code, Decimal(0)) + ln.qty
    return out


def find_orders(q, limit=8):
    """The till's «استبدال / مرتجع بفاتورة»: an invoice by its reference (MAIN-01-0000123), its number tail, or its receipt
    number (the big «رقم» of the last days)."""
    q = str(q or "").strip()[:40]
    if not q:
        return []
    qs = Order.objects.filter(is_return=False, cancelled=False)
    hits = list(qs.filter(uid=q)[:1])
    if not hits and q.isdigit():
        since = timezone.now() - dt.timedelta(days=60)
        hits = list(qs.filter(uid__endswith="-" + q.zfill(7)).order_by("-id")[:limit])
        if len(q) <= 6:
            hits += [o for o in qs.filter(shift_seq=int(q), created_at_device__gte=since).order_by("-id")[:limit] if o not in hits]
    out = []
    for o in hits[:limit]:
        back, lines = returned_qty(o.uid), []
        names = {str(x.get("item_code")): x.get("name") for x in (o.raw or {}).get("lines") or [] if isinstance(x, dict)}
        for ln in o.lines.select_related("item").order_by("id"):
            if ln.item.item_type == "service" and not hasattr(ln.item, "recipe"):
                continue  # play time, delivery fee, service charge: not given back over the counter
            left = ln.qty - back.get(ln.item.code, Decimal(0))
            back[ln.item.code] = max(Decimal(0), back.get(ln.item.code, Decimal(0)) - ln.qty)
            lines.append({"item_code": ln.item.code, "name": names.get(ln.item.code) or ln.item.name, "qty": str(ln.qty), "left": str(max(left, Decimal(0))),
                          "price": str(ln.price), "discount": str(ln.discount), "vr": str(ln.item.vat_rate if ln.item.vat_rate is not None else CompanySettings.get().default_vat_rate)})
        out.append({"uid": o.uid, "seq": o.shift_seq, "at": o.created_at_device.isoformat(), "total": str(o.total),
                    "customer": o.customer.name if o.customer_id else "", "lines": lines})
    return out


def _swap_prices(data):
    """An exchange's new invoice: {model (the item, or the product a size / colour belongs to): the lowest unit price paid for it on
    the original invoice} — the same model in another size is sold at the price the customer paid (difference 0)."""
    src = str(data.get("exch_src") or "")
    orig = Order.objects.filter(uid=src, is_return=False, cancelled=False).first() if src and data.get("exch_of") else None
    out = {}
    for ln in orig.lines.select_related("item") if orig else []:
        if ln.qty > 0:
            k, unit = ln.item.variant_of_id or ln.item_id, (ln.qty * ln.price - ln.discount) / ln.qty
            out[k] = min(out.get(k, unit), unit)
    return out


def check_ret_of(data, pk=None):
    """A return / exchange made from an invoice: never more than was sold and not given back yet, never above its price."""
    uid = str(data.get("ret_of"))
    orig = Order.objects.filter(uid=uid, is_return=False, cancelled=False).first()
    if not orig:
        raise ValidationError(f"الفاتورة الأصلية {uid} غير موجودة — لم يُسجَّل المرتجع")
    sold = {}
    for ln in orig.lines.select_related("item"):
        a = sold.setdefault(ln.item.code, [Decimal(0), Decimal(0)])
        a[0] += ln.qty
        a[1] += ln.qty * ln.price - ln.discount
    back = returned_qty(uid, exclude=pk)
    for ln in data.get("lines") or []:
        code, qty = str(ln.get("item_code")), Decimal(str(ln.get("qty") or 0))
        price, disc = Decimal(str(ln.get("price") or 0)), Decimal(str(ln.get("discount") or 0))
        if code not in sold:
            raise ValidationError(f"صنف لم يُبع في الفاتورة {uid} — لم يُسجَّل المرتجع")
        if qty > sold[code][0] - back.get(code, Decimal(0)) + Decimal("0.0005"):
            raise ValidationError(f"كمية المرتجع أكبر من الباقي في الفاتورة {uid} — لم يُسجَّل")
        if qty * price - disc > sold[code][1] / sold[code][0] * qty + Decimal("0.05"):
            raise ValidationError(f"سعر المرتجع أكبر من سعر البيع في الفاتورة {uid} — لم يُسجَّل")
        back[code] = back.get(code, Decimal(0)) + qty


CASH_SHORT, CASH_OVER = ("3801010104", "عجز نقدية", "38010101", "expense", "debit"), ("4401010102", "زيادة نقدية", "44010101", "income", "credit")


def cash_diff_account(short):
    """The account a drawer's count difference goes to (created under its parent on older charts)."""
    code, name, parent, typ, side = CASH_SHORT if short else CASH_OVER
    acc = Account.objects.filter(code=code).first()
    if acc:
        return acc
    par = Account.objects.filter(code=parent).first()
    if par is None:  # an unusual chart: keep the old accounts rather than fail a shift close
        return Account.objects.get(code="3801010102" if short else "4401010101")
    return Account.objects.create(code=code, name=name, parent=par, level=(par.level or 5) + 1, is_postable=True, account_type=typ,
                                  normal_balance=side, report="profit_loss", cash_flow_category="operating", dim1_policy="optional")


@transaction.atomic
def ingest_shift(device, data):
    uid = str(data["uid"])
    existing = Shift.objects.filter(uid=uid).first()
    if existing is not None and existing.device_id != device.pk:
        return existing  # a shift belongs to the device that opened it: another device can never change or close it
    if existing is None and not uid.startswith(f"{device.branch.code}-{device.code}-"):
        raise ValidationError("المعرف لا يخص هذا الجهاز")
    sh, _ = Shift.objects.get_or_create(uid=uid, defaults=dict(
        device=device, cashier=data.get("cashier", ""), verified=bool(data.get("verified")), opened_at=_dt(data.get("opened_at")),
        opening_float=Decimal(str(data.get("opening_float", 0)))))
    if data.get("closed_at") and not sh.closed_at:
        orders = sh.orders.filter(cancelled=False)
        cash = sum((o.paid_cash * (-1 if o.is_return else 1) for o in orders), Decimal(0))
        cash += sum((m.cash_effect for m in sh.cash_moves.filter(cancelled=False)), Decimal(0))
        sh.expected_cash = sh.opening_float + cash
        sh.counted_cash = Decimal(str(data.get("counted_cash", 0)))
        sh.closed_at = _dt(data["closed_at"])
        diff = L.q2(sh.counted_cash - sh.expected_cash)
        if diff:
            box = device.branch.cashbox.account
            other = cash_diff_account(short=diff < 0)  # «عجز نقدية» / «زيادة نقدية» — never the goods' shortage account
            lines = ([dict(account=box, debit=diff), dict(account=other, credit=diff, dim1=device.branch.cost_center)] if diff > 0
                     else [dict(account=other, debit=-diff, dim1=device.branch.cost_center), dict(account=box, credit=-diff)])
            sh.difference_entry = L.create_entry("JV", sh.closed_at.date(), f"فرق جرد وردية {sh.cashier}", lines, source=f"pos.shift:{sh.pk}")
        sh.save()
    return sh


TILL_GROUPS = ["كاشير", "مدير المحل", "ويتر"]


def verify_cashier(username, password):
    from django.contrib.auth import authenticate

    u = authenticate(username=username, password=password)
    if not u or not u.is_active or not (u.is_superuser or u.groups.filter(name__in=TILL_GROUPS).exists()):
        return None
    return u


def till_role(user):
    """«waiter» = tables and the kitchen only (no payment, returns or cash moves); anyone else on the till is a cashier."""
    if user.is_superuser or user.groups.filter(name__in=["كاشير", "مدير المحل"]).exists():
        return "cashier"
    return "waiter" if user.groups.filter(name="ويتر").exists() else "cashier"


# ---------------------------------------------------------------- returns at the till need the owner (unless cashier_can_edit)
RET_SALT, RET_AGE = "pos.return", 3 * 24 * 3600  # the till may go offline right after the approval: 3 days to sync


def return_token(device, user, reason, amount):
    from django.core import signing

    return signing.dumps({"d": device.pk, "u": user.username, "r": str(reason)[:200], "a": str(L.q2(Decimal(str(amount)))),
                          "n": __import__("secrets").token_hex(6)}, salt=RET_SALT)


def check_return(device, data):
    """A return from the till: approved by the owner / manager (signed token from /pos/api/return/ok/) or allowed to cashiers.
    Returns the text kept on the order; raises ValidationError when the money would go out without control."""
    from django.core import signing

    reason = str(data.get("ret_reason") or "").strip()[:200]
    if CompanySettings.get().cashier_can_edit:
        return f"مرتجع — السبب: {reason or 'لم يُكتب'}"
    tok = str(data.get("ret_ok") or "")
    paid = sum((Decimal(str(data.get(k) or 0)) for k in ("paid_cash", "paid_card", "paid_credit")), Decimal(0))
    if not tok and data.get("exch_to"):  # an exchange where no money goes back: its new invoice (synced first) pays the whole return
        sale = Order.objects.filter(device=device, is_return=False, cancelled=False, raw__exch_of=str(data.get("uid"))).first()
        if sale and sale.total >= L.q2(paid) - Decimal("0.01"):
            return f"استبدال بالفاتورة {sale.uid} — لم يُرد مال للعميل"
    try:
        t = signing.loads(tok, salt=RET_SALT, max_age=RET_AGE)
    except signing.BadSignature:  # also SignatureExpired
        raise ValidationError("مرتجع دون موافقة صاحب المحل أو المدير — لم يُسجَّل")
    if t.get("d") != device.pk or L.q2(paid) > Decimal(t.get("a") or 0):
        raise ValidationError("موافقة المرتجع لا تخص هذه الفاتورة — لم يُسجَّل")
    if Order.objects.filter(raw__ret_ok=tok).exists():
        raise ValidationError("موافقة المرتجع استُخدمت من قبل — لم يُسجَّل")
    return f"مرتجع بموافقة {t.get('u')} — السبب: {t.get('r') or reason}"


def ingest_cash_move(device, data):
    from ledger.models import Account, Partner

    from . import money

    uid = str(data["uid"])
    if not uid.startswith(f"{device.branch.code}-{device.code}-"):
        raise ValidationError("المعرف لا يخص هذا الجهاز")
    from .models import CashMove

    def same(m):
        return m.kind == data.get("kind") and m.amount == L.q2(Decimal(str(data.get("amount") or 0)))

    ex = CashMove.objects.filter(uid=uid).first()
    if ex and ex.kind == "delivery" == data.get("kind") and ex.note.endswith(str(data.get("order_uid") or "-")):
        return ex  # a delivery collection sent again (its amount is the program's, not the till's)
    if ex and not same(ex):  # a used number on another paper (the till lost its counter): kept under «~n», never dropped
        twins = list(CashMove.objects.filter(uid__startswith=uid + "~"))
        hit = next((t for t in twins if same(t)), None)
        if hit:
            return hit
        uid = f"{uid}~{len(twins) + 2}"
    shift = Shift.objects.filter(uid=data.get("shift_uid"), device=device).first()
    if data.get("kind") == "collect" and not Partner.objects.filter(pk=data.get("partner_id") or 0, kind="customer").exists():
        raise ValidationError("التحصيل من عميل آجل مسجّل فقط")  # 1.25 «تحصيل من عميل» at the till
    if data.get("kind") not in ("drawing", "deposit", "expense", "wage", "advance", "supplier", "collect", "delivery"):
        raise ValidationError("نوع حركة غير مسموح من الكاشير")
    if data.get("kind") == "delivery":  # 1.24: the rider handed a delivery order's money in
        from .delivery import collect

        return collect(device, dict(data, uid=uid), shift)
    return money.post(data["kind"], data.get("amount"), _dt(data.get("created_at")).date(), cash_account=device.branch.cashbox.account,
                      partner=Partner.objects.filter(pk=data.get("partner_id") or 0).first(),
                      account=Account.objects.filter(pk=data.get("account_id") or 0).first(), deduct_advance=data.get("deduct_advance") or 0,
                      deduct_penalty=data.get("deduct_penalty") or 0, note=data.get("note", ""), uid=uid, shift=shift,
                      cost_center=device.branch.cost_center, by=shift.cashier if shift else "")


def ingest_request(device, data):
    from .models import ChangeRequest

    uid = str(data["uid"])
    if not uid.startswith(f"{device.branch.code}-{device.code}-"):
        raise ValidationError("المعرف لا يخص هذا الجهاز")
    obj, _ = ChangeRequest.objects.get_or_create(uid=uid, defaults=dict(
        kind=data.get("kind", "void") if data.get("kind") in ("void", "edit") else "void", target=str(data.get("target", ""))[:60],
        reason=str(data.get("reason", ""))[:300], requested_by=str(data.get("cashier", ""))[:100], device=device))
    if obj.state == "pending" and CompanySettings.get().cashier_can_edit:  # the owner lets cashiers cancel / edit themselves
        from . import voids

        try:
            voids.decide(obj, True, "system", "يُسمح للكاشير بالإلغاء والتعديل")
            obj.refresh_from_db()
        except ValidationError:
            pass  # the invoice has not arrived yet: stays pending for the owner
    return obj
