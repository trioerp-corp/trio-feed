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
            for p in Promotion.objects.filter(active=True, end__gte=today).select_related("item")]


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

    def _vat(x):
        return str(x if x is not None else s.default_vat_rate)

    def _sizes(pk):
        vs = sorted(sizes.get(pk, []), key=lambda v: (v.extra or {}).get("order", 0))
        return [{"code": v.code, "n": v.size_name, "p": str(v.sale_price), "v": _vat(v.vat_rate)} for v in vs]

    addons = [{"code": a.code, "name": a.name, "p": str(a.sale_price), "v": _vat(a.vat_rate), "cats": [c.name for c in a.addon_for.all()]}
              for a in Item.objects.filter(active=True, is_addon=True, sale_price__isnull=False).prefetch_related("addon_for").order_by("name")]
    from inventory.models import ItemUnit

    units = {}
    for u in ItemUnit.objects.all():
        units.setdefault(u.item_id, []).append({"name": u.name, "factor": str(u.factor), "barcode": u.barcode,
                                                "price": str(u.sale_price) if u.sale_price is not None else None})
    cashiers = get_user_model().objects.filter(is_active=True).filter(groups__name__in=TILL_GROUPS).distinct()
    return {
        "branch": {"code": device.branch.code, "name": device.branch.name, "footer": device.branch.receipt_footer},
        "device": device.code, "next_seq": next_seq(device), "company": s.name, "phone": s.phone, "address": s.address, "tax_id": s.tax_registration,
        "prices_include_vat": s.pos_prices_include_vat,
        "default_vat": str(s.default_vat_rate),
        "logo": s.logo_svg,
        "kitchen": s.kitchen_ticket if s.business in ("cafe", "gaming") else "off", "biz": s.business,
        "tables": __import__("pos.tables", fromlist=["x"]).cfg(s), "can_edit": s.cashier_can_edit,
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
                   **({"q": str(i["qty_on_hand"]), "r": str(i["reorder_level"])} if i["item_type"] == "stock" and (i["reorder_level"] or 0) > 0 else {}), "units": units.get(i["id"], []),
                   "x": " ".join([str(v) for v in (i["extra"] or {}).values() if v] + ([i["aliases"]] if i["aliases"] else [])),
                   "xs": " · ".join(str((i["extra"] or {}).get(f["k"])) for f in xfields if f["s"] and (i["extra"] or {}).get(f["k"]))[:60],
                   **({"sizes": _sizes(i["id"])} if i["id"] in sizes else {}),
                   "d": str((i["extra"] or {}).get("desc", ""))[:120],
                   "sale_price": str(i["sale_price"] if i["sale_price"] is not None else min(v.sale_price for v in sizes[i["id"]])),
                   "vat_rate": _vat(i["vat_rate"])} for i in items],
    }


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
    under = []
    for ln in data.get("lines", []):
        item = Item.objects.filter(code=ln["item_code"]).first()
        if item is None:
            raise ValidationError(f"صنف غير معروف: {ln['item_code']}")
        qty, price, disc = Decimal(str(ln["qty"])), Decimal(str(ln["price"])), Decimal(str(ln.get("discount", 0)))
        if qty <= 0 or price < 0 or disc < 0 or qty * price - disc < 0:
            raise ValidationError(f"كمية أو سعر غير صحيح في «{item.name}»")
        if renumber and not is_return and item.sale_price and price < _lowest_price(item) - Decimal("0.005"):
            under.append(f"«{item.name}» بيع بـ {L.q2(price)} والسعر {L.q2(_lowest_price(item))}")
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
                if (q.qty if q else 0) < iq:
                    flags.append(f"رصيد {ing.name} كان {q.qty if q else 0} وقت المزامنة")
            uc = None
            if item.item_type == "recipe" and not is_return:  # menu item: each ingredient at its last purchase invoice price
                from inventory.recipes import unit_cost

                uc = unit_cost(ing) or None
            StockMoveLine.objects.create(move=move, item=ing, qty=iq, dim1=branch.cost_center, account=cogs, unit_cost=uc)
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
    order.entry = L.create_entry("SI", order.created_at_device.date(), f"نقطة بيع {branch.name} {uid}", lines, source=f"pos.order:{order.pk}",
                                 legacy_ref=uid)
    if move.lines.exists():
        INV.post_move(move, allow_negative=True)
        order.stock_move = move
    else:  # only services (play time…): nothing leaves the store
        move.delete()
    order.total, order.vat, order.flags = total, vat, "؛ ".join(flags)[:300]
    order.save()
    if isinstance(data.get("delivery"), dict) and not is_return:
        from .delivery import record

        record(order, data)
    return order, True


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
            other = Account.objects.get(code="4401010101" if diff > 0 else "3801010102")
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
    try:
        t = signing.loads(tok, salt=RET_SALT, max_age=RET_AGE)
    except signing.BadSignature:  # also SignatureExpired
        raise ValidationError("مرتجع دون موافقة صاحب المحل أو المدير — لم يُسجَّل")
    paid = sum((Decimal(str(data.get(k) or 0)) for k in ("paid_cash", "paid_card", "paid_credit")), Decimal(0))
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
