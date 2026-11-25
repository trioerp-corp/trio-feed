"""1.24.1 «التوريد» — the pages a shop uses to talk to its suppliers:

- returns to companies: what expires within months, grouped by the supplier it came from, printable, returned in one click
  at the batch's OWN purchase cost (never the moving average) and only to that same supplier;
- purchase orders: items at / under their «حد الطلب» grouped by the supplier they were last bought from, and (clothes) the
  sold-out sizes / colours of the models that sell → a printable order / WhatsApp message to each supplier.
"""
from core import currency as _CUR  # 1.26: the shop's currency (display only)
import datetime as dt
import math
from collections import OrderedDict
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils import timezone

from core.models import CompanySettings
from inventory import services as INV
from inventory.models import Item, StockMove, StockMoveLine
from ledger.models import Partner

Z = Decimal(0)
IN_KINDS = ("receipt", "opening")
RET_DAYS = [(30, "خلال شهر"), (60, "خلال شهرين"), (90, "خلال 3 أشهر"), (120, "خلال 4 أشهر"), (180, "خلال 6 أشهر"), (270, "خلال 9 أشهر"), (365, "خلال سنة")]


def batch_cost(line):
    """What one base unit of this received batch cost (the invoice money after discounts / bonus spread on it)."""
    if line.qty and line.value:
        return (Decimal(line.value) / line.qty).quantize(Decimal("0.0001"))
    return Decimal(line.unit_cost or 0)


def source_line(pk, item_id=None):
    """A posted receipt / opening line a return is made from (None when it is not one)."""
    if not str(pk or "").isdigit():
        return None
    qs = StockMoveLine.objects.select_related("move", "move__partner", "item").filter(pk=int(pk), move__state="posted", move__kind__in=IN_KINDS)
    if item_id:
        qs = qs.filter(item_id=item_id)
    return qs.first()


def check_source(src, supplier):
    """A supplier return of a batch goes back to the supplier that sold it."""
    if src.move.partner_id and supplier and src.move.partner_id != supplier.pk:
        raise ValidationError(f"«{src.item.name}» من فاتورة {src.move.number} للمورد «{src.move.partner.name}» — "
                              "يُرجَع إلى المورد نفسه بسعر شرائه")


@transaction.atomic
def return_batches(rows, supplier, warehouse, date, user, note=""):
    """rows: [(source StockMoveLine, base qty)] → one posted supplier return («مرتجع مشتريات») at each batch's own cost."""
    if not supplier:
        raise ValidationError("مرتجع المشتريات يحتاج إلى المورد")
    mv = StockMove.objects.create(kind="preturn", date=date, warehouse=warehouse, partner=supplier, created_by=user, description=note[:300])
    p = warehouse.project
    cc = p if p.is_postable else p.children.filter(is_postable=True).first()
    n = 0
    for src, qty in rows:
        if not qty or qty <= 0:
            continue
        check_source(src, supplier)
        if qty > src.qty:
            raise ValidationError(f"«{src.item.name}»: الكمية أكبر مما ورد في فاتورة {src.move.number} ({src.qty.normalize():f})")
        StockMoveLine.objects.create(move=mv, item=src.item, qty=qty, unit_cost=batch_cost(src), dim1=cc,
                                     note=f"من فاتورة {src.move.number}"[:200])
        n += 1
    if not n:
        raise ValidationError("اختر صنفًا واحدًا على الأقل")
    INV.post_move(mv, user=user)
    return mv


def _need(request, perm):
    if not request.user.has_perm(perm):
        raise PermissionDenied


def _company_of(item):
    x = item.extra or {}
    return x.get("company") or x.get("manufacturer") or x.get("brand") or ""


# ------------------------------------------------------------------ returns to companies (expiring within months)
@login_required
def company_returns(request):
    _need(request, "inventory.view_item")
    from ui.shop import expiring

    if request.method == "POST":
        _need(request, "inventory.add_stockmove")
        P = request.POST
        sup = Partner.objects.filter(pk=P.get("supplier") or 0, kind="supplier").first()
        try:
            rows = []
            for pk in P.getlist("src"):
                src = source_line(pk)
                if not src:
                    raise ValidationError("دفعة غير موجودة — حدّث الصفحة")
                q = INV_num(P.get(f"q_{pk}"))
                if q and q > 0:
                    rows.append((src, q))
            by_wh = OrderedDict()
            for src, q in rows:
                by_wh.setdefault(src.move.warehouse, []).append((src, q))
            if not by_wh:
                raise ValidationError("اختر صنفًا واحدًا على الأقل")
            done = []
            with transaction.atomic():
                for wh, rs in by_wh.items():
                    done.append(return_batches(rs, sup, wh, timezone.localdate(), request.user, note="مرتجع أصناف قاربت صلاحيتها"))
            total = sum((sum((l.value for l in m.lines.all()), Z) for m in done), Z)
            messages.success(request, f"تم تسجيل مرتجع للمورد «{sup.name}» ({'، '.join(m.number for m in done)}) بقيمة {_CUR.fmt(total)} بسعر الشراء — "
                                      "وقلّت مستحقاته بالقيمة نفسها")
        except (ValidationError, ValueError) as e:
            messages.error(request, " ".join(getattr(e, "messages", [str(e)])))
        return redirect(request.get_full_path())
    try:
        days = max(1, min(int(request.GET.get("days") or 180), 365))
    except ValueError:
        days = 180
    groups = OrderedDict()
    for r in expiring(days):
        ln = r["line"]
        cost = batch_cost(ln)
        r.update(cost=cost, ret_value=(r["qty"] * cost).quantize(Decimal("0.01")), company=_company_of(r["item"]))
        sup = r["move"].partner
        g = groups.setdefault(sup.pk if sup else 0, dict(supplier=sup, rows=[], value=Z))
        g["rows"].append(r)
        g["value"] += r["ret_value"]
    gs = sorted(groups.values(), key=lambda g: (g["supplier"] is None, -g["value"]))
    return render(request, "ui/supply_returns.html", dict(groups=gs, days=days, company=CompanySettings.get(), today=timezone.localdate(),
                                                          total=sum((g["value"] for g in gs), Z), day_opts=RET_DAYS,
                                                          can_post=request.user.has_perm("inventory.add_stockmove")))


def INV_num(v):
    from ui.shop import num

    return num(v)


# ------------------------------------------------------------------ purchase orders to suppliers
def last_buys(item_ids):
    """{item id: (supplier or None, cost per base unit, date)} from the newest posted purchase of each item."""
    out = {}
    for ln in (StockMoveLine.objects.filter(item_id__in=list(item_ids), move__state="posted", move__kind__in=IN_KINDS)
               .select_related("move__partner").order_by("item_id", "-move__date", "-id")):
        if ln.item_id not in out or (out[ln.item_id][0] is None and ln.move.partner_id):  # the newest, else the newest with a supplier
            out[ln.item_id] = (ln.move.partner, batch_cost(ln), ln.move.date)
    return out


def _buy_unit(items):
    """{item id: (unit name, factor)} — the unit the item is bought in (the box of a strip drug, the biggest carton)."""
    from ui.shop import _purchase_units

    out = {}
    for pk, (base, us, dflt) in _purchase_units(items).items():
        f = next((u[1] for u in us if u[0] == dflt), None)
        if f and f > 1:
            out[pk] = (dflt, Decimal(str(f)))
    return out


def shortages():
    """Stock items at / under their order limit (never-bought samples left out) with the quantity to order: back up to twice the limit."""
    from inventory.reorder import never_stocked

    items = [i for i in Item.objects.filter(active=True, item_type="stock", reorder_level__gt=0).exclude(code="0").order_by("name")
             if i.qty_on_hand <= i.reorder_level and not (i.variant_of_id is None and isinstance((i.extra or {}).get("grid"), dict))]
    skip = never_stocked([i.pk for i in items])
    items = [i for i in items if i.pk not in skip]
    rows = []
    for i in items:
        need = max(i.reorder_level * 2 - max(i.qty_on_hand, Z), i.reorder_level)
        rows.append(dict(item=i, need=need, why=f"الرصيد {fq(i.qty_on_hand)} — الحد {fq(i.reorder_level)}"))
    return rows


def sold_out_sizes(days=60, min_sold=3):
    """Clothes / shoes: the sizes / colours with nothing left of the models that sell (≥ min_sold pieces in the period) —
    the quantity to order = what that size sold in the period (at least 1)."""
    from pos.models import OrderLine

    d1 = timezone.localdate() - dt.timedelta(days=days)
    sold, by_parent = {}, {}
    for item_id, parent_id, qty, ret in (OrderLine.objects.filter(item__variant_of__isnull=False, item__variant_of__extra__has_key="grid",
                                                                  order__cancelled=False, order__created_at_device__date__gte=d1)
                                         .values_list("item_id", "item__variant_of_id", "qty", "order__is_return")):
        q = -qty if ret else qty
        sold[item_id] = sold.get(item_id, Z) + q
        by_parent[parent_id] = by_parent.get(parent_id, Z) + q
    good = {p for p, q in by_parent.items() if q >= min_sold}
    rows = []
    for v in Item.objects.filter(pk__in=[k for k, q in sold.items() if q > 0], active=True, variant_of_id__in=good).select_related("variant_of").order_by("name"):
        if v.qty_on_hand <= 0:
            rows.append(dict(item=v, need=max(sold[v.pk], Decimal(1)),
                             why=f"نفد — بيع منه {fq(sold[v.pk])} قطعة في {days} يومًا (الموديل كله {fq(by_parent[v.variant_of_id])})"))
    return rows


def fq(d):
    d = Decimal(d or 0)
    return f"{d.normalize():f}" if d == d.to_integral() else f"{d.quantize(Decimal('0.001')).normalize():f}"


def group_order(rows):
    """[dict(item, need, why)] → per supplier: [dict(supplier, rows=[… qty, unit, cost, total]…, total)] — the units the
    supplier sells in (boxes, cartons) and the price last paid him."""
    items = [r["item"] for r in rows]
    last = last_buys([i.pk for i in items])
    # a size never bought alone takes the supplier of its model's other sizes
    for r in rows:
        i = r["item"]
        if i.pk not in last and i.variant_of_id:
            sib = last_buys(list(Item.objects.filter(variant_of_id=i.variant_of_id).values_list("pk", flat=True)))
            got = next((v for v in sib.values() if v[0]), None)
            if got:
                last[i.pk] = got
    unit = _buy_unit(items)
    groups = OrderedDict()
    for r in rows:
        i = r["item"]
        sup, cost, day = last.get(i.pk, (None, Decimal(i.std_cost or 0), None))
        u, f = unit.get(i.pk, (i.unit, Decimal(1)))
        qty = Decimal(math.ceil(r["need"] / f)) if f > 1 else r["need"]
        price = (cost * f).quantize(Decimal("0.01"))
        r.update(qty=qty, qty_txt=fq(qty), unit=u, price=price, price_txt=f"{price:f}", total=(qty * price).quantize(Decimal("0.01")), last=day)
        g = groups.setdefault(sup.pk if sup else 0, dict(supplier=sup, rows=[], total=Z))
        g["rows"].append(r)
        g["total"] += r["total"]
    return sorted(groups.values(), key=lambda g: (g["supplier"] is None, g["supplier"].name if g["supplier"] else ""))


@login_required
def orders(request):
    """«طلبات الشراء من الموردين»: ?kind=low (below the order limit) or ?kind=sizes (sold-out sizes of good sellers)."""
    _need(request, "inventory.view_item")
    from inventory.variants import enabled as variants_on

    sizes_on = variants_on()
    kind = "sizes" if request.GET.get("kind") == "sizes" and sizes_on else "low"
    try:
        days = max(7, min(int(request.GET.get("days") or 60), 365))
    except ValueError:
        days = 60
    rows = sold_out_sizes(days) if kind == "sizes" else shortages()
    s = CompanySettings.get()
    return render(request, "ui/supply_orders.html", dict(groups=group_order(rows), kind=kind, days=days, sizes_on=sizes_on, company=s,
                                                         today=timezone.localdate(), day_opts=[30, 60, 90]))


# ------------------------------------------------------------------ the purchase form's «شراء بالمقاسات» grid
def purchase_grid():
    """{model pk: {n: name, s: sizes, c: colours, v: {"size|colour": [variant pk, last cost]}}} for clothes / shoes shops."""
    from inventory import variants as VR

    if not VR.enabled():
        return {}
    parents = [p for p in Item.objects.filter(active=True, variant_of__isnull=True, extra__has_key="grid").order_by("name")[:500] if VR.is_parent(p)]
    kids = {}
    for v in Item.objects.filter(variant_of__in=parents, active=True).only("pk", "variant_of_id", "extra"):
        kids.setdefault(v.variant_of_id, []).append(v)
    last = last_buys([v.pk for vs in kids.values() for v in vs])
    out = {}
    for p in parents:
        g = p.extra.get("grid") or {}
        cells = {}
        for v in kids.get(p.pk, []):
            x = v.extra or {}
            c = last.get(v.pk, (None, None, None))[1]
            cells[f"{x.get('size', '')}|{x.get('color', '')}"] = [v.pk, f"{c:.2f}" if c else ""]
        if cells:
            out[str(p.pk)] = dict(n=p.name, s=g.get("sizes") or [""], c=g.get("colors") or [""], v=cells)
    return out
