"""Tables for cafés / restaurants (1.23).

The waiter opens a table and keeps adding to its bill; every «إرسال للمطبخ» is a batch with its own kitchen ticket. When the
customer pays, the till takes the whole bill into an ordinary sale (same receipt, same books) and the table is free again.
A customer may also order from the QR on the table (when the owner allows it): the order comes from Trio's server, lands on
the table as a batch from «الزبون» and goes to the kitchen.

Prices always come from the program (never from the till or the customer's phone), except a «صنف بدون كود» the waiter types.
Settings live in CompanySettings.menu_cfg: tables (on/off), qr_order (customers may order), qr_confirm (the waiter
confirms a customer's order before the kitchen sees it).
"""
from core import currency as _CUR  # 1.26: the shop's currency (display only)
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import CompanySettings

from .models import DiningTable, TableTab

MAX_LINES = 200
TABLE_BIZ = ("cafe", "gaming")


def cfg(s=None):
    s = s or CompanySettings.get()
    c = s.menu_cfg or {}
    return dict(on=s.business in TABLE_BIZ and bool(c.get("tables", True)) and DiningTable.objects.filter(active=True).exists(),
                qr_order=bool(c.get("qr_order")), qr_confirm=bool(c.get("qr_confirm")))


def set_cfg(**kw):
    s = CompanySettings.get()
    c = dict(s.menu_cfg or {})
    c.update({k: bool(v) for k, v in kw.items()})
    s.menu_cfg = c
    s.save(update_fields=["menu_cfg"])


def _d(x, what="الرقم"):
    try:
        v = Decimal(str(x).replace(",", "."))
    except (InvalidOperation, ValueError):
        raise ValidationError(f"{what} غير صحيح")
    if not v.is_finite():
        raise ValidationError(f"{what} غير صحيح")
    return v


def _money(v):
    return str(Decimal(v).quantize(Decimal("0.01")))


def tab_total(tab):
    t = Decimal(0)
    for ln in tab.lines:
        q = Decimal(str(ln["qty"]))
        t += q * Decimal(str(ln["price"])) + sum(q * Decimal(str(a["p"])) for a in ln.get("adds") or [])
    return t.quantize(Decimal("0.01"))


def open_tab(table):
    return TableTab.objects.filter(table=table, state="open").first()


def state():
    """The tables for the till: free / busy, the bill so far, how long, customer orders waiting for the waiter."""
    now = timezone.now()
    tabs = {t.table_id: t for t in TableTab.objects.filter(state="open")}
    out = []
    for t in DiningTable.objects.filter(active=True):
        tab = tabs.get(t.pk)
        row = dict(id=t.pk, name=t.name, area=t.area, seats=t.seats)
        if tab:
            row.update(tab=tab.pk, total=_money(tab_total(tab)), mins=int((now - tab.opened_at).total_seconds() // 60), by=tab.opened_by,
                       n=len(tab.lines), wait=sum(1 for b in tab.batches if b.get("src") == "qr" and b.get("hold")))
        out.append(row)
    return out


def tab_json(tab):
    return dict(id=tab.pk, table=tab.table_id, name=tab.table.name, opened_at=tab.opened_at.isoformat(), by=tab.opened_by, lines=tab.lines,
                batches=tab.batches, total=_money(tab_total(tab)), note=tab.note)


def _item_line(raw, src):
    """One line as the till / the phone sent it → the line we keep, priced by the program."""
    from inventory.models import Item, ItemUnit

    if not isinstance(raw, dict):
        raise ValidationError("سطر غير صحيح")
    code = str(raw.get("code") or "")[:40]
    qty = _d(raw.get("qty", 1), "الكمية")
    if qty <= 0 or qty > 500:
        raise ValidationError("الكمية غير صحيحة")
    note = str(raw.get("note") or "")[:120]
    if code == "0":  # «صنف بدون كود»: only the waiter, with the price he types
        if src != "waiter":
            raise ValidationError("صنف غير موجود")
        price = _d(raw.get("price"), "السعر")
        if price <= 0 or price > 100000:
            raise ValidationError("السعر غير صحيح")
        return dict(code="0", name=str(raw.get("name") or "صنف بدون كود")[:80], price=_money(price), qty=str(qty), factor="1", cat="", vr="0",
                    adds=[], note=note)
    it = Item.objects.filter(code=code, active=True, is_addon=False).select_related("category", "variant_of").first()
    if not it:
        raise ValidationError("صنف غير موجود أو متوقف — حدّث الأصناف")
    s = CompanySettings.get()
    vr = str(it.vat_rate if it.vat_rate is not None else s.default_vat_rate)
    name, price, factor = it.name, it.sale_price, Decimal(1)
    if it.variant_of_id:
        name = f"{it.variant_of.name} — {it.size_name}"
    unit = str(raw.get("unit") or "")
    if unit and src == "waiter":
        u = ItemUnit.objects.filter(item=it, name=unit, sale_price__isnull=False).first()
        if not u:
            raise ValidationError(f"الوحدة «{unit}» غير موجودة لـ «{it.name}»")
        name, price, factor = f"{u.name} {it.name}", u.sale_price, u.factor
    if price is None or price <= 0:
        raise ValidationError(f"«{name}» بلا سعر بيع")
    cat = (it.variant_of.category if it.variant_of_id else it.category).name
    adds = []
    for ac in (raw.get("adds") or [])[:12]:
        a = Item.objects.filter(code=str(ac.get("code") if isinstance(ac, dict) else ac)[:40], active=True, is_addon=True, sale_price__gt=0).first()
        if not a:
            continue
        cats = list(a.addon_for.values_list("name", flat=True))
        if cats and cat not in cats:
            continue
        adds.append(dict(code=a.code, name=a.name, p=_money(a.sale_price), v=str(a.vat_rate if a.vat_rate is not None else s.default_vat_rate)))
    return dict(code=it.code, name=name[:120], price=_money(price), qty=str(qty), factor=str(factor), cat=cat, vr=vr, adds=adds, note=note)


@transaction.atomic
def add(table, raw_lines, by, src="waiter", hold=False, note=""):
    """Puts lines on the table's bill (opening it if free) as one batch → (tab, batch number)."""
    table = DiningTable.objects.select_for_update().get(pk=table.pk)
    if not table.active:
        raise ValidationError("هذه الترابيزة متوقفة")
    if not raw_lines:
        raise ValidationError("لا توجد أصناف")
    lines = [_item_line(r, src) for r in raw_lines[:60]]
    tab = open_tab(table)
    if not tab:
        tab = TableTab.objects.create(table=table, opened_by=str(by)[:100])
    if len(tab.lines) + len(lines) > MAX_LINES:
        raise ValidationError("حساب الترابيزة كبير جدًا — حاسب ثم افتح حسابًا جديدًا")
    n = len(tab.batches) + 1
    at = timezone.now().isoformat()
    nid = max([ln["id"] for ln in tab.lines] or [0])
    for i, ln in enumerate(lines, 1):
        ln.update(id=nid + i, by=str(by)[:60], at=at, src=src, batch=n)
    tab.lines = tab.lines + lines
    tab.batches = tab.batches + [dict(n=n, at=at, by=str(by)[:60], src=src, printed=False, hold=bool(hold), note=str(note or "")[:200])]
    tab.save(update_fields=["lines", "batches"])
    return tab, n


def batch_lines(tab, n):
    return [ln for ln in tab.lines if ln.get("batch") == n]


@transaction.atomic
def confirm(tab, n, by):
    """The waiter accepts a customer's order (when «تأكيد الويتر» is on) — then it goes to the kitchen."""
    tab = TableTab.objects.select_for_update().get(pk=tab.pk)
    for b in tab.batches:
        if b["n"] == n and b.get("hold"):
            b["hold"] = False
            b["ok_by"] = str(by)[:60]
    tab.save(update_fields=["batches"])
    return tab


@transaction.atomic
def drop_batch(tab, n, by):
    """The waiter refuses a customer's order that is still waiting for him."""
    tab = TableTab.objects.select_for_update().get(pk=tab.pk)
    b = next((b for b in tab.batches if b["n"] == n), None)
    if not b or not b.get("hold"):
        raise ValidationError("هذا الطلب لم يعد بانتظار التأكيد")
    tab.lines = [ln for ln in tab.lines if ln.get("batch") != n]
    b.update(hold=False, dropped=str(by)[:60])
    tab.log = tab.log + [dict(at=timezone.now().isoformat(), by=str(by)[:60], what=f"رفض طلب الزبون رقم {n}")]
    tab.save(update_fields=["lines", "batches", "log"])
    _free_if_empty(tab)
    return tab


def _free_if_empty(tab):
    if not tab.lines and tab.state == "open":
        tab.state, tab.closed_at = "void", timezone.now()
        tab.save(update_fields=["state", "closed_at"])


@transaction.atomic
def remove(tab, line_id, by, may_cancel_sent=False, qty=None):
    """Takes a line (or part of it) off the bill. A line already sent to the kitchen needs the right to cancel."""
    tab = TableTab.objects.select_for_update().get(pk=tab.pk)
    if tab.state != "open":
        raise ValidationError("الحساب مغلق")
    ln = next((x for x in tab.lines if x["id"] == int(line_id)), None)
    if not ln:
        raise ValidationError("السطر غير موجود")
    if not may_cancel_sent:
        raise ValidationError("إلغاء صنف أُرسل للمطبخ يحتاج موافقة صاحب المحل")
    q = Decimal(ln["qty"])
    take = min(q, _d(qty, "الكمية")) if qty not in (None, "") else q
    if take <= 0:
        raise ValidationError("الكمية غير صحيحة")
    if take >= q:
        tab.lines = [x for x in tab.lines if x["id"] != ln["id"]]
    else:
        ln["qty"] = str(q - take)
    tab.log = tab.log + [dict(at=timezone.now().isoformat(), by=str(by)[:60], what=f"إلغاء {take} × {ln['name']}")]
    tab.save(update_fields=["lines", "log"])
    _free_if_empty(tab)
    return tab


@transaction.atomic
def move(tab, to_table, by):
    tab = TableTab.objects.select_for_update().get(pk=tab.pk)
    if tab.state != "open":
        raise ValidationError("الحساب مغلق")
    to_table = DiningTable.objects.select_for_update().get(pk=to_table.pk)
    if not to_table.active:
        raise ValidationError("الترابيزة متوقفة")
    other = open_tab(to_table)
    if other and other.pk != tab.pk:  # the other table is busy: the two bills become one
        nid = max([ln["id"] for ln in other.lines] or [0])
        shift = len(other.batches)
        for i, ln in enumerate(tab.lines, 1):
            ln["id"], ln["batch"] = nid + i, ln.get("batch", 0) + shift
        for b in tab.batches:
            b["n"] += shift
        other.lines, other.batches = other.lines + tab.lines, other.batches + tab.batches
        other.log = other.log + tab.log + [dict(at=timezone.now().isoformat(), by=str(by)[:60], what=f"دمج حساب {tab.table.name}")]
        other.save(update_fields=["lines", "batches", "log"])
        tab.lines, tab.state, tab.closed_at, tab.closed_by = [], "void", timezone.now(), str(by)[:100]
        tab.save(update_fields=["lines", "state", "closed_at", "closed_by"])
        return other
    old = tab.table.name
    tab.table = to_table
    tab.log = tab.log + [dict(at=timezone.now().isoformat(), by=str(by)[:60], what=f"نقل من {old}")]
    tab.save(update_fields=["table", "log"])
    return tab


@transaction.atomic
def close(tab, order_uid, by):
    """The customer paid: the bill became the sale `order_uid` (its own receipt and entries come with the till's sync)."""
    tab = TableTab.objects.select_for_update().get(pk=tab.pk)
    if tab.state != "open":
        raise ValidationError("الحساب مغلق بالفعل")
    amt = tab_total(tab)
    tab.state, tab.closed_at, tab.closed_by, tab.order_uid = "paid", timezone.now(), str(by)[:100], str(order_uid)[:60]
    tab.log = tab.log + [dict(at=timezone.now().isoformat(), by=str(by)[:60], part=tab.order_uid, amt=str(amt), last=True,
                              what=f"حوسب الحساب بالفاتورة {tab.order_uid} ({_CUR.fmt(amt)})")]
    tab.save(update_fields=["state", "closed_at", "closed_by", "order_uid", "log"])
    return tab


def missing_sales(hours=24, days=30):
    """Bills of tables paid (whole or a part) at the till whose sale never reached the program within `hours`: the till
    closed the table but its invoice did not sync (device off, browser data wiped, or a closed table with no sale). The
    owner sees them on the tables page."""
    import datetime as dt

    from .models import Order

    now = timezone.now()
    out = []
    for tab in TableTab.objects.filter(state="paid", closed_at__gte=now - dt.timedelta(days=days)).select_related("table"):
        parts = [g for g in tab.log if g.get("part")]
        if tab.order_uid and not any(g.get("part") == tab.order_uid for g in parts):
            parts.append(dict(at=tab.closed_at.isoformat(), part=tab.order_uid, amt="", by=tab.closed_by))
        for g in parts:
            try:
                at = dt.datetime.fromisoformat(g["at"])
            except (TypeError, ValueError, KeyError):
                at = tab.closed_at
            if timezone.is_naive(at):
                at = timezone.make_aware(at)
            if now - at < dt.timedelta(hours=hours):
                continue
            uid = g["part"]
            if Order.objects.filter(uid=uid).exists() or Order.objects.filter(uid__startswith=uid + "~").exists():
                continue
            out.append(dict(tab=tab, table=tab.table.name, uid=uid, at=at, amt=g.get("amt") or "", by=g.get("by") or ""))
    return sorted(out, key=lambda r: r["at"], reverse=True)


@transaction.atomic
def pay_part(tab, order_uid, picks, by):
    """«تقسيم الحساب» by lines (1.24): one part of the bill was paid as its own sale `order_uid`; its lines (or part of a line's
    quantity) leave the bill. The table closes when nothing is left. The same part sent twice changes nothing."""
    tab = TableTab.objects.select_for_update().get(pk=tab.pk)
    uid = str(order_uid)[:60]
    if any(x.get("part") == uid for x in tab.log):
        return tab
    if tab.state != "open":
        raise ValidationError("الحساب مغلق بالفعل")
    take = {}
    for p in picks or []:
        try:
            lid, q = int(p.get("id")), _d(p.get("qty"), "الكمية")
        except (TypeError, ValueError, AttributeError):
            raise ValidationError("سطر غير صحيح")
        take[lid] = take.get(lid, Decimal(0)) + q
    if not take:
        raise ValidationError("اختر أصناف هذا الجزء")
    names, amt = [], Decimal(0)
    for lid, q in take.items():
        ln = next((x for x in tab.lines if x["id"] == lid), None)
        have = Decimal(ln["qty"]) if ln else Decimal(0)
        if not ln or q <= 0 or q > have:
            raise ValidationError("أصناف الجزء لا تطابق حساب الترابيزة — حدّث الشاشة")
        names.append(f"{q.normalize():f} × {ln['name']}")
        amt += q * Decimal(str(ln["price"])) + sum(q * Decimal(str(a["p"])) for a in ln.get("adds") or [])
        if q == have:
            tab.lines = [x for x in tab.lines if x["id"] != lid]
        else:
            ln["qty"] = str(have - q)
    tab.log = tab.log + [dict(at=timezone.now().isoformat(), by=str(by)[:60], part=uid, amt=str(amt.quantize(Decimal("0.01"))), what=f"دفع جزء من الحساب بالفاتورة {uid}: " + "، ".join(names)[:300])]
    fields = ["lines", "log"]
    if not tab.lines:
        tab.state, tab.closed_at, tab.closed_by, tab.order_uid = "paid", timezone.now(), str(by)[:100], uid
        fields += ["state", "closed_at", "closed_by", "order_uid"]
    tab.save(update_fields=fields)
    return tab


@transaction.atomic
def void(tab, by, reason=""):
    tab = TableTab.objects.select_for_update().get(pk=tab.pk)
    if tab.state != "open":
        raise ValidationError("الحساب مغلق")
    tab.state, tab.closed_at, tab.closed_by = "void", timezone.now(), str(by)[:100]
    tab.log = tab.log + [dict(at=timezone.now().isoformat(), by=str(by)[:60], what="إلغاء الحساب كله" + (f" — {reason}" if reason else ""))]
    tab.save(update_fields=["state", "closed_at", "closed_by", "log"])
    return tab


@transaction.atomic
def claim_prints():
    """Customer orders not printed yet (and not waiting for the waiter): the first till to ask prints them."""
    out = []
    for tab in TableTab.objects.select_for_update().filter(state="open"):
        changed = False
        for b in tab.batches:
            if b.get("src") == "qr" and not b.get("printed") and not b.get("hold") and not b.get("dropped"):
                b["printed"] = True
                changed = True
                out.append(dict(table=tab.table.name, n=b["n"], at=b["at"], note=b.get("note", ""), lines=batch_lines(tab, b["n"])))
        if changed:
            tab.save(update_fields=["batches"])
    return out


def kitchen_rows(table_name, lines, note="", who="", station=None):
    """The kitchen printer's rows (pos.kitchen.print_lines format) for a batch of a table."""
    t = timezone.localtime()
    out = [(f"طلب للمطبخ — {station}" if station else "طلب للمطبخ", 12, True, "c"), (str(table_name)[:30], 26, True, "c"), (f"{t:%H:%M} — {who}".strip(" —"), 10, False, "c"), ("-", 0, False, "c")]
    for ln in lines:
        q = Decimal(ln["qty"])
        out.append((f"{q.normalize():f} × {ln['name'][:60]}", 14, True, "r"))
        for a in ln.get("adds") or []:
            out.append((f"    + {a['name'][:50]}", 12, False, "r"))
        if ln.get("note"):
            out.append((f"    ملاحظة: {ln['note'][:120]}", 12, True, "r"))
    if note:
        out += [("-", 0, False, "c"), (f"ملاحظة الطلب: {note[:200]}", 13, True, "r")]
    out.append(("-", 0, False, "c"))
    return out


def print_batch(table_name, lines, note="", who=""):
    """A batch on the kitchen printer(s): each kitchen station prints only its lines (1.24; one station = one ticket)."""
    from . import kitchen
    from .restaurant import printer_of, route

    s = CompanySettings.get()
    for st, ls in route(lines, lambda ln: ln.get("cat", "")):
        kitchen.print_lines(printer_of(st, s), kitchen_rows(table_name, ls, note, who, st["name"] if st else None))


def print_on_kitchen(tab, n):
    """Customer order with «طابعة المطبخ»: the computer the program runs on prints it right away."""
    b = next((b for b in tab.batches if b["n"] == n), {})
    print_batch(tab.table.name, batch_lines(tab, n), b.get("note", ""), "طلب الزبون من QR")
