"""Lite edition for shops / supermarkets: home, sales list, items & prices, sales and profit reports."""
import datetime as dt
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q, Sum
from django.shortcuts import redirect, render
from django.utils import timezone

from core.models import CompanySettings
from inventory.models import Item, ItemCategory, StockMoveLine
from ledger.models import JournalLine
from pos.models import Device, Order, OrderLine, Shift
from treasury import services as T
from treasury.models import CashBox

Z = Decimal("0")


def _b(request):
    """The branch the screens are showing (a branch manager is locked to his own)."""
    from pos.branches import view_branch

    return view_branch(request)


def _own_branch(request, branch_id):
    """A branch manager / cashier tied to a branch never opens another branch's papers by typing their number."""
    from pos.branches import home_branch

    mine = home_branch(request.user)
    if mine and branch_id and mine.pk != branch_id:
        raise PermissionDenied


def _locked(request):
    from pos.branches import home_branch

    return bool(home_branch(request.user))


def _branch_ccs(b):
    """Cost centres of a branch (its own + its warehouse's) — its revenue, cost of goods and expenses carry them."""
    ids = {b.cost_center_id}
    if b.warehouse.project_id:
        ids.add(b.warehouse.project_id)
    return list(ids)


def _branch_stock_value(b):
    from inventory.models import StockQuant

    return sum((q.qty * q.item.avg_cost for q in StockQuant.objects.filter(warehouse=b.warehouse).select_related("item")), Z).quantize(Decimal("0.01"))


def _low(b=None):
    """Items at or under their reorder level — 1.25: for one branch, on that branch's own shelf (its store), not the company's total.
    Each item carries `here` (the quantity shown next to it)."""
    from inventory.models import StockQuant

    qs = Item.objects.filter(active=True, reorder_level__isnull=False).order_by("name")
    if not b:
        return [setattr(i, "here", i.qty_on_hand) or i for i in qs if i.qty_on_hand <= i.reorder_level]
    have = dict(StockQuant.objects.filter(warehouse=b.warehouse).values_list("item_id", "qty"))
    return [setattr(i, "here", have.get(i.pk, Z)) or i for i in qs.filter(item_type="stock") if have.get(i.pk, Z) <= i.reorder_level]


def num(v):
    """Numbers typed or shown with an Arabic decimal comma («36,50») or Arabic digits."""
    v = str(v or "").strip().replace("٫", ".").replace(",", ".").translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    if not v:
        return None
    try:
        d = Decimal(v)
    except InvalidOperation:  # «abc», «1.2.3» — the form shows its own «ليس رقمًا» message instead of a server error
        return None
    return d if d.is_finite() else None


def _range(request, default_days=0):
    today = timezone.localdate()
    try:
        d1 = dt.date.fromisoformat(request.GET.get("from", ""))
    except ValueError:
        d1 = today.replace(day=1) if default_days is None else today - dt.timedelta(days=default_days)
    try:
        d2 = dt.date.fromisoformat(request.GET.get("to", ""))
    except ValueError:
        d2 = today
    return d1, d2


def _sales(orders):
    t = dict(n=0, gross=Z, returns=Z, vat=Z, cash=Z, card=Z, credit=Z, rider=Z, disc=Z, insta=Z)
    for o in orders:
        if o.cancelled:
            continue
        sign = -1 if o.is_return else 1
        t["n"] += 0 if o.is_return else 1
        if o.is_return:
            t["returns"] += o.total
        else:
            t["gross"] += o.total
        t["vat"] += sign * o.vat
        t["cash"] += sign * o.paid_cash
        t["card"] += sign * o.paid_card
        t["insta"] += sign * (o.paid_insta or Z)  # 1.25 InstaPay transfers: their own column, never cash
        t["credit"] += sign * o.paid_credit
        t["rider"] += sign * (o.paid_rider or Z)  # 1.24 delivery: money still with / handed in by the rider
        if not o.is_return:  # 1.25 manual discounts (lines + invoice), kept on the receipt's raw data
            t["disc"] += Decimal(str(((o.raw or {}).get("disc") or {}).get("total") or 0)) if isinstance((o.raw or {}).get("disc"), dict) else Z
    t["net"] = t["gross"] - t["returns"]
    t["net_ex_vat"] = t["net"] - t["vat"]
    return t


def _kinds(orders):
    """Day close: the service charge, the delivery fees, and the sales by kind (dine-in tables / takeaway / delivery / counter)."""
    from pos.delivery import FEE_CODE
    from pos.restaurant import SVC_CODE

    k = dict(svc=Z, fees=Z, split=[])
    by = {}
    for o in orders:
        if o.cancelled:
            continue
        sign, raw = (-1 if o.is_return else 1), (o.raw or {})
        for ln in raw.get("lines") or []:
            if not isinstance(ln, dict):
                continue
            try:
                v = Decimal(str(ln.get("qty") or 0)) * Decimal(str(ln.get("price") or 0)) - Decimal(str(ln.get("discount") or 0))
            except (InvalidOperation, ValueError):
                continue
            code = str(ln.get("item_code"))
            if code == SVC_CODE:
                k["svc"] += sign * v
            elif code == FEE_CODE:
                k["fees"] += sign * v
        kind = "tbl" if raw.get("table") else "dlv" if isinstance(raw.get("delivery"), dict) else "away" if raw.get("away") else "ctr"
        by[kind] = by.get(kind, Z) + sign * o.total
    names = dict(tbl="الصالة (الترابيزات)", away="تيك أواي", dlv="دليفري", ctr="بيع مباشر")
    k["split"] = [(names[x], by[x]) for x in ("tbl", "away", "dlv", "ctr") if by.get(x)]
    return k


def _cogs(orders):
    moves = [o.stock_move_id for o in orders if o.stock_move_id and not o.cancelled]
    out = Z
    for ln in StockMoveLine.objects.filter(move_id__in=moves).select_related("move"):
        out += (-1 if ln.move.kind == "return" else 1) * (ln.value or Z)
    return out


def home(request):
    from core.modules import enabled

    s = CompanySettings.get()
    if s.business == "clinic" and "clinic" in enabled(s):  # a clinic's day starts at the reception queue
        return redirect("/ui/clinic/")
    if not can_see_cash(request.user):
        if s.business == "gaming" and "gaming" in enabled(s):
            return redirect("/ui/play/")
        return render(request, "ui/shop_home_cashier.html", dict(sell_first=expiring(14)[:10]))
    today = timezone.localdate()
    b = _b(request)
    oq = Order.objects.filter(created_at_device__date=today)
    if b:
        oq = oq.filter(device__branch=b)
    orders = list(oq)
    t = _sales(orders)
    drawer = b.cashbox if b else CashBox.objects.filter(active=True, kind="cash").first()
    low = _low(b)
    stock_value = _branch_stock_value(b) if b else (Item.objects.aggregate(s=Sum("stock_value"))["s"] or Z)
    devices = Device.objects.filter(active=True).select_related("branch")
    if b:
        devices = devices.filter(branch=b)
    from treasury.models import DayClose

    from pos.money import owner_balance

    from core.dbcare import problem

    from pos.models import ShopPartner

    from pos.branches import branches_on

    tr_wait = __import__("pos.transfers", fromlist=["x"]).waiting(request.user) if branches_on() else 0
    return render(request, "ui/shop_home.html", dict(tr_wait=tr_wait, multi_hint=not branches_on() and bool((s.menu_cfg or {}).get("multi")) and "branches" in enabled(s),
                                                     many_stores=Warehouse.objects.filter(active=True).count() > 1, no_expiry="expiry" in __import__("core.activities", fromlist=["x"]).unused(), no_production="production" in __import__("core.activities", fromlist=["x"]).unused(), has_partners=ShopPartner.objects.filter(active=True).exists(), db_problem=problem(), owner=owner_balance(), expiring_n=len(expiring(14)), settings_obj=CompanySettings.get(), open_shifts=Shift.objects.filter(closed_at__isnull=True, **({"device__branch": b} if b else {})).count(),
                                                     last_close=DayClose.objects.order_by("-date").first(),t=t, today=today, drawer=drawer, drawer_bal=T.cashbox_balance(drawer) if drawer else Z,
                                                     low=low[:15], low_n=len(low), stock_value=stock_value, devices=devices,
                                                     recent=(Order.objects.filter(device__branch=b) if b else Order.objects).select_related("device").order_by("-created_at_device")[:10],
                                                     no_price=Item.objects.filter(active=True, sale_price__isnull=True, sizes__isnull=True).count()))


def _need_cash(request):
    if not can_see_cash(request.user):
        raise PermissionDenied("هذه الصفحة للمدير فقط")


@login_required
def sales(request):
    _need_cash(request)
    d1, d2 = _range(request, 0)
    qs = Order.objects.filter(created_at_device__date__range=(d1, d2)).select_related("device", "shift").order_by("-created_at_device")
    b = _b(request)
    if b:
        qs = qs.filter(device__branch=b)
    elif request.GET.get("branch"):
        qs = qs.filter(device__branch_id=request.GET["branch"])
    if request.GET.get("returns"):
        qs = qs.filter(is_return=True)
    if request.GET.get("flags"):
        qs = qs.exclude(flags="")
    orders = list(qs[:500])
    from pos.models import Branch

    return render(request, "ui/shop_sales.html", dict(orders=orders, t=_sales(orders), d1=d1, d2=d2, g=request.GET, branches=Branch.objects.all(),
                                                       stuck=[d for d in Device.objects.select_related("branch") if d.stuck and (not b or d.branch_id == b.pk)]))


@login_required
def items(request):
    if not request.user.has_perm("inventory.view_item"):
        raise PermissionDenied
    from core.activities import current

    act = current()
    fields = act["fields"]
    if request.method == "POST":
        if not request.user.has_perm("inventory.change_item"):
            messages.error(request, "ليست لديك صلاحية تعديل الأصناف")
            return redirect("shop_items")
        if "new" in request.POST:
            name = request.POST.get("name", "").strip()
            from inventory.reorder import parse as reorder_of

            try:
                reorder = reorder_of(request.POST.get("reorder"))
            except ValidationError as e:
                reorder, name = None, name or ""
                messages.error(request, e.messages[0])
                return redirect(request.get_full_path())
            from inventory import names as NM

            name = NM.clean(name)
            if not name:
                messages.error(request, "اكتب اسم الصنف")
            elif NM.clash(name):
                messages.error(request, f"يوجد صنف باسم «{NM.clash(name)}» بالفعل — عدّل الصنف الموجود أو اكتب اسمًا يميّزه")
            elif (num(request.POST.get("price")) or 0) < 0:
                messages.error(request, "سعر البيع لا يكون سالبًا")
            else:
                last = Item.objects.filter(code__regex=r"^\d+$").order_by("-id").first()
                code = request.POST.get("code", "").strip() or str((int(last.code) + 1) if last else 1001)
                if Item.objects.filter(code=code).exists():
                    messages.error(request, f"الكود {code} موجود من قبل")
                else:
                    Item.objects.create(code=code, name=name, category=ItemCategory.objects.get(pk=request.POST["category"]),
                                        unit=request.POST.get("unit") or "قطعة", barcode=request.POST.get("barcode", "").strip(),
                                        sale_price=num(request.POST.get("price")) or None,
                                        reorder_level=reorder, extra=_extra(request.POST, "x_{}", fields))
                    request.session["last_cat"] = request.POST.get("category")
                    messages.success(request, f"تمت الإضافة «{name}» بكود {code}")
        elif "save" in request.POST:
            # every row the owner changed is saved — the row's own «حفظ», Enter after a scan, or «حفظ الكل» (nothing typed is lost)
            from inventory import variants as VR
            from inventory.reorder import parse as reorder_of

            pks = sorted({int(k.split("_")[1]) for k in request.POST if k.startswith("price_") and k.split("_")[1].isdigit()})
            done, bad, at = [], [], request.POST.get("save")
            for it in Item.objects.filter(pk__in=pks):
                p = request.POST.get(f"price_{it.pk}", "").strip()
                bc = request.POST.get(f"barcode_{it.pk}", "").strip()
                ro = request.POST.get(f"reorder_{it.pk}", "").strip()
                xs = _extra(request.POST, "x_{}_" + str(it.pk), fields, keep_empty=True) if fields else {}
                same = (num(p) == it.sale_price or (not p and it.sale_price is None)) and bc == (it.barcode or "") and \
                    ro == ("" if it.reorder_level is None else f"{it.reorder_level.normalize():f}") and \
                    all((v or "") == str((it.extra or {}).get(k, "") or "") for k, v in xs.items())
                if same and str(it.pk) != at:
                    continue
                if VR.is_parent(it):  # its price / barcode live on its sizes and colours
                    if str(it.pk) == at:
                        messages.info(request, f"«{it.name}» صنف بمقاسات وألوان — عدّله من هنا")
                        return redirect(f"/ui/shop/variants/?edit={it.pk}#vform")
                    continue
                if (num(p) or 0) < 0:
                    bad.append(f"«{it.name}»: سعر البيع لا يكون سالبًا")
                    continue
                try:
                    reorder = reorder_of(ro, it.item_type)
                except ValidationError as e:
                    bad.append(f"«{it.name}»: {e.messages[0]}")
                    continue
                if bc and Item.objects.filter(barcode=bc).exclude(pk=it.pk).exists():
                    bad.append(f"«{it.name}»: الباركود {bc} مسجّل لصنف آخر")
                    continue
                it.sale_price, it.barcode, it.reorder_level = num(p), bc, reorder
                if fields:
                    it.extra = {k: v for k, v in {**(it.extra or {}), **xs}.items() if v}
                it.save()
                done.append(it.name)
            for m in bad:
                messages.error(request, m)
            if done:
                messages.success(request, f"تم الحفظ «{done[0]}»" if len(done) == 1 else f"تم حفظ {len(done)} أصناف: " + "، ".join(done[:8]))
            elif not bad:
                messages.info(request, "لا يوجد تعديل للحفظ")
            return redirect(request.get_full_path() + (f"#r{at}" if at and at.isdigit() else ""))
        return redirect(request.get_full_path())
    q = request.GET.get("q", "").strip()
    qs = Item.objects.select_related("category").exclude(code="0").order_by("category__name", "name")  # «صنف بدون كود» is the till's, not a row
    # the hall's play time and package items are sold from the hall screen and priced on «الأجهزة والأسعار» / «الباقات» — not buffet rows
    qs = qs.exclude(code="PLAY").exclude(play_package__isnull=False)
    if q:
        from inventory.names import norm as nz  # «اوجمنتين» finds «أوجمنتين», «panadol» finds «PANADOL»

        k = nz(q)
        ids = [pk for pk, n, a, x in Item.objects.values_list("pk", "name", "aliases", "extra") if k and k in nz(f"{n} {a} {' '.join(map(str, (x or {}).values()))}")]
        words = [w for w in k.split() if w]  # 1.25: «قميص أزرق L» finds «قميص — L / أزرق»
        ids += [pk for pk, n, a, x in Item.objects.values_list("pk", "name", "aliases", "extra")
                if len(words) > 1 and all(w in nz(f"{n} {a} {' '.join(map(str, (x or {}).values()))}") for w in words)]
        qs = qs.filter(Q(name__icontains=q) | Q(code=q) | Q(barcode=q) | Q(aliases__icontains=q) | Q(extra__icontains=q) | Q(pk__in=ids))
    if request.GET.get("cat"):
        qs = qs.filter(category_id=request.GET["cat"])
    if request.GET.get("noreorder"):
        qs = qs.filter(item_type="stock", reorder_level__isnull=True).exclude(code="0")
    if request.GET.get("noprice"):
        qs = qs.filter(sale_price__isnull=True)
    rows = list(qs[:400])
    for i in rows:
        i.xvals = [(f, (i.extra or {}).get(f["k"], "")) for f in fields]
    cats = sorted(ItemCategory.objects.all(), key=lambda c: (c.name == "أخرى" or c.name.startswith("مستلزمات تشغيل"), c.name))
    last = str(request.session.get("last_cat") or "")
    if not any(str(c.pk) == last for c in cats):  # the category used last, else the shop's main one
        main = next((c for c in cats if c.name in ("بقالة وتموين", "بقالة", "مشروبات")), None)
        last = str(main.pk) if main else ""
    return render(request, "ui/shop_items.html", dict(items=rows, q=q, cats=cats, last_cat=last,
                                                      g=request.GET, act=act, fields=fields, variants_on=__import__("inventory.variants", fromlist=["x"]).enabled()))


def _extra(P, pat, fields, keep_empty=False):
    out = {}
    for f in fields:
        v = (P.get(pat.format(f["k"])) or "").strip()[:120]
        if v or keep_empty:
            out[f["k"]] = v
    return out


@login_required
def reports(request):
    _need_cash(request)
    d1, d2 = _range(request, None)
    from pos.models import Branch

    oq = Order.objects.filter(created_at_device__date__range=(d1, d2))
    b = _b(request)
    if b:
        oq = oq.filter(device__branch=b)
    elif request.GET.get("branch"):
        oq = oq.filter(device__branch_id=request.GET["branch"])
    orders = list(oq)
    t = _sales(orders)
    cogs = _cogs(orders)
    by_day = defaultdict(list)
    for o in orders:
        by_day[o.created_at_device.date()].append(o)
    days = [(d, _sales(os_)) for d, os_ in sorted(by_day.items(), reverse=True)]
    lines = OrderLine.objects.filter(order__in=[o for o in orders if not o.cancelled]).select_related("item", "order")
    top = defaultdict(lambda: dict(qty=Z, revenue=Z))
    for ln in lines:
        sign = -1 if ln.order.is_return else 1
        top[ln.item]["qty"] += sign * ln.qty
        top[ln.item]["revenue"] += sign * (ln.qty * ln.price - ln.discount)
    top = sorted(top.items(), key=lambda x: -x[1]["revenue"])[:25]
    exp = (JournalLine.objects.filter(entry__state__in=["posted", "reversed"], entry__date__range=(d1, d2), account__report="profit_loss",
                                      account__account_type="expense").exclude(account__code="3101010101")
           .filter(**({"dim1__in": _branch_ccs(b)} if b else {}))
           .values("account__name").annotate(d=Sum("debit"), c=Sum("credit")).order_by("account__name"))
    expenses = [(e["account__name"], (e["d"] or Z) - (e["c"] or Z)) for e in exp if (e["d"] or Z) - (e["c"] or Z)]
    exp_total = sum((x for _, x in expenses), Z)
    gross_profit = t["net_ex_vat"] - cogs
    shifts = Shift.objects.filter(opened_at__date__range=(d1, d2)).order_by("-opened_at")
    if b:
        shifts = shifts.filter(device__branch=b)
    low = _low(b)
    from pos.delivery import cfg as _dcfg

    return render(request, "ui/shop_reports.html", dict(
        dlv=bool(t["rider"]) or _dcfg()["on"], d1=d1, d2=d2, t=t, cogs=cogs, gross_profit=gross_profit, margin=(gross_profit / t["net_ex_vat"] * 100) if t["net_ex_vat"] else Z,
        days=days, top=top, expenses=expenses, exp_total=exp_total, net_profit=gross_profit - exp_total, shifts=shifts, low=low,
        stock_value=_branch_stock_value(b) if b else (Item.objects.aggregate(s=Sum("stock_value"))["s"] or Z), company=CompanySettings.get(),
        branches=Branch.objects.all() if not b else [], g=request.GET, branch=b))


# ------------------------------------------------------------------ who may see money
def can_see_cash(user):
    return user.is_superuser or user.groups.filter(name="مدير المحل").exists() or user.has_perm("treasury.view_voucher")


# ------------------------------------------------------------------ purchases (cash / credit)
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied, ValidationError  # noqa: E402
from django.db import IntegrityError, transaction  # noqa: E402

from inventory import services as INV  # noqa: E402
from inventory.models import StockMove, StockMoveLine, Warehouse  # noqa: E402
from ledger.models import Account, Partner  # noqa: E402
from treasury.models import Voucher, VoucherLine  # noqa: E402


def _errs(request, e):
    for m in getattr(e, "messages", [str(e)]):
        messages.error(request, m)


@login_required
def purchases(request):
    if not request.user.has_perm("inventory.view_stockmove"):
        raise PermissionDenied
    b = _b(request)
    qs = StockMove.objects.filter(kind="receipt", partner__isnull=False, **({"warehouse": b.warehouse} if b else {})).select_related("partner", "warehouse").order_by("-date", "-id")[:300]
    paid = {}
    for v in Voucher.objects.filter(legacy_number__startswith="PUR-", state="posted").values("legacy_number", "amount"):
        paid[v["legacy_number"]] = paid.get(v["legacy_number"], Z) + v["amount"]
    vat = {}
    for src, d in JournalLine.objects.filter(entry__source__startswith="inventory.purchase-vat:", entry__state="posted", account__code="2301040301") \
            .values_list("entry__source", "debit"):
        vat[src] = vat.get(src, Z) + (d or Z)
    for v in CashMove.objects.filter(uid__startswith="PUR-", uid__endswith="-OWNER", cancelled=False).values("uid", "amount"):
        paid[v["uid"][:-6]] = paid.get(v["uid"][:-6], Z) + v["amount"]  # paid by the owner from his pocket
    rows = []
    for m in qs:
        total = sum((l.value or Z for l in m.lines.all()), Z) + vat.get(f"inventory.purchase-vat:{m.pk}", Z)
        pd = paid.get(f"PUR-{m.pk}", Z)
        rows.append((m, total, pd, total - pd))
    saved = StockMove.objects.filter(pk=request.GET.get("saved") or 0, kind="receipt").first() if str(request.GET.get("saved", "")).isdigit() else None
    from core.modules import enabled

    labels_on = "labels" in enabled() and CompanySettings.get().business in ("market", "retail")
    return render(request, "ui/shop_purchases.html", dict(rows=rows, saved=saved, labels_on=labels_on))


@login_required
def purchase_new(request):
    if not request.user.has_perm("inventory.add_stockmove"):
        raise PermissionDenied
    from core.modules import enabled
    from inventory import purchases as PUR

    b = _b(request)
    whs = Warehouse.objects.filter(active=True, **({"pk": b.warehouse_id} if b else {}))
    boxes = CashBox.objects.filter(active=True, **({"pk": b.cashbox_id} if b else {}))
    P = request.POST
    old = []
    if request.method == "POST":
        for k, item_id in enumerate(P.getlist("item")):
            g = lambda n: (P.getlist(n)[k] if k < len(P.getlist(n)) else "")  # noqa: E731
            if item_id or g("qty"):
                old.append(dict(item=item_id, text=g("item_text").strip()[:150], qty=g("qty"), unit=g("unit"), factor=g("factor") or "1", cost=g("cost"),
                                bonus=g("bonus"), public=g("public"), disc=g("disc"), produced=g("produced"), expiry=g("expiry")))
    items = list(Item.objects.filter(active=True).order_by("name"))
    ctx = dict(whs=whs, boxes=boxes, suppliers=Partner.objects.filter(kind="supplier", active=True).order_by("name"),
               items=items, item_units=_purchase_units(items), today=timezone.localdate().isoformat(), p=P, old_lines=old,
               units_on="units" in enabled(), vat_registered=CompanySettings.get().vat_registered, ask=[])
    off = __import__("core.activities", fromlist=["x"]).unused()
    ctx.update(exp_on="expiry" not in off, ph_on="expiry" not in off)  # clothes / shoes: no expiry, no public price / bonus columns
    ctx["grids"] = __import__("ui.supply", fromlist=["x"]).purchase_grid()  # 1.24.1 «شراء بالمقاسات»: one model, every size × colour at once
    if request.method == "POST":
        try:
            with transaction.atomic():
                sup = Partner.objects.filter(pk=P.get("supplier") or 0).first()
                if not sup and P.get("new_supplier", "").strip():
                    sup = _purchase_supplier(P["new_supplier"], P.get("phone", ""), ok=P.get("confirm") == "1", ask=ctx["ask"])
                if not sup:
                    raise ValidationError("اختر المورد أو اكتب اسم مورد جديد")
                inv_no = P.get("invoice", "").strip()[:30]
                if inv_no and sup.pk and P.get("confirm") != "1":
                    dup = StockMove.objects.filter(kind="receipt", partner=sup, legacy_number=inv_no).exclude(state="cancelled").first()
                    if dup:
                        ctx["ask"].append(f"فاتورة رقم {inv_no} من «{sup.name}» مسجّلة من قبل ({dup.number} بتاريخ {dup.date:%d/%m/%Y}) — "
                                          "راجعها حتى لا تدخل البضاعة مرتين")
                if ctx["ask"]:
                    raise ValidationError("راجع التنبيه في أعلى الصفحة، ثم علّم «نعم، راجعتها — سجّلها» واضغط «حفظ الفاتورة» مرة أخرى إن كانت صحيحة")
                date = dt.date.fromisoformat(P["date"])
                rows, new_items = [], []
                for ln in (dict(x) for x in old):  # copies: a refused save rolls the new item back, the form keeps its text
                    if not ln["item"] and ln["qty"] and ln["text"]:  # a material bought for the first time: added right here
                        it, made = _purchase_new_item(ln["text"], public=num(ln["public"]), unit=ln["unit"])
                        ln["item"] = str(it.pk)
                        if made:
                            new_items.append(it.name)
                            if not ln["unit"] and it.units.filter(name="علبة").exists():  # a new drug sold by the strip is bought by the box
                                ln["unit"] = "علبة"
                    if not ln["item"] or not ln["qty"]:
                        if ln["qty"] and not ln["item"]:
                            raise ValidationError("يوجد سطر بلا اسم صنف — اكتب اسم الصنف أو اختره من القائمة")
                        continue
                    item = Item.objects.get(pk=ln["item"])
                    unit = item.units.filter(name=ln["unit"]).first() if ln["unit"] else None
                    f = unit.factor if unit else (num(ln["factor"]) or Decimal(1))  # a known unit's content comes from the item, never the page
                    qty = num(ln["qty"])
                    if not qty or qty <= 0:
                        raise ValidationError("يجب أن تكون الكمية أكبر من صفر")
                    public = num(ln["public"])
                    rows.append(dict(item=item, qty=qty, factor=f, cost=num(ln["cost"]), bonus=num(ln["bonus"]) or Z, public=public, disc_pct=num(ln["disc"]),
                                     produced=dt.date.fromisoformat(ln["produced"]) if ln["produced"] else None,
                                     expiry=dt.date.fromisoformat(ln["expiry"]) if ln["expiry"] else None))
                    if public and public > 0:  # the public price printed on the invoice becomes the selling price when there is none
                        if unit and unit.sale_price is None:
                            unit.sale_price = public.quantize(Decimal("0.01"))
                            unit.save(update_fields=["sale_price"])
                        elif not unit and f == 1 and item.sale_price is None:
                            item.sale_price = public.quantize(Decimal("0.01"))
                            item.save(update_fields=["sale_price"])
                if not rows:
                    raise ValidationError("أضف صنفًا واحدًا على الأقل")
                lines, vat = PUR.lines_from_invoice(rows, discount=num(P.get("inv_disc")), vat_pct=num(P.get("vat_pct")),
                                                    vat_registered=ctx["vat_registered"])
                pay = P.get("pay", "credit") if P.get("pay") in ("credit", "cash", "split", "owner") else "credit"
                box = boxes.filter(pk=P.get("cashbox") or 0).first() if pay in ("cash", "split") else None
                if pay in ("cash", "split") and not box:
                    raise CashBox.DoesNotExist

                mv, total, msg = PUR.record(warehouse=whs.get(pk=P["warehouse"]), supplier=sup, date=date, lines=lines, invoice=inv_no,
                                            pay=pay, paid=num(P.get("paid")) or Z, box=box, user=request.user, vat=vat)
            if new_items:
                msg += f" — وأُضيف صنف جديد: {'، '.join(new_items)} (اضبط وحدته وحد الطلب من الأصناف)"
            messages.success(request, msg)
            return redirect(__import__("django.urls", fromlist=["x"]).reverse("shop_purchases") + f"?saved={mv.pk}")  # → «اطبع ملصقات هذه الفاتورة»
        except (ValidationError, ValueError, KeyError, CashBox.DoesNotExist) as e:
            _errs(request, e)
    return render(request, "ui/shop_purchase_form.html", ctx)


def _purchase_units(items):
    """{item pk: [base unit, [[unit, factor]…], the unit bought by default]} for the purchase / return pages: a drug sold by the strip
    is bought by the box («علبة»), a supermarket item with a carton by the carton (its biggest unit)."""
    from inventory.models import ItemUnit
    from inventory.units import BOX, STRIP

    by = defaultdict(list)
    for u in ItemUnit.objects.filter(item__active=True).order_by("factor"):
        by[u.item_id].append([u.name, float(u.factor)])
    out = {}
    for i in items:
        us = by.get(i.pk)
        if us:
            dflt = BOX if i.unit == STRIP and any(u[0] == BOX for u in us) else us[-1][0]
            out[i.pk] = [i.unit, us, dflt]
    return out


def _purchase_supplier(name, phone, ok, ask):
    """A supplier typed by name: the existing one when it is the same name written another way («شركة النيل» = «شركه النيل»),
    a question when it is close to one («النيل للتوزيع» / «النيل للتوزيغ»), else a new supplier."""
    import difflib

    from inventory import names as NM

    name = NM.clean(name, 200)
    k = NM.norm(name)
    sups = list(Partner.objects.filter(kind="supplier", active=True))
    same = next((s for s in sups if NM.norm(s.name) == k), None)
    if same:
        return same
    near = [s.name for s in sups if difflib.SequenceMatcher(None, NM.norm(s.name), k).ratio() >= 0.8
            or (min(len(k), len(NM.norm(s.name))) >= 4 and (k in NM.norm(s.name) or NM.norm(s.name) in k))]
    if near and not ok:
        ask.append(f"المورد «{name}» قريب من مورد موجود: {'، '.join('«' + n + '»' for n in near[:3])} — اختره من القائمة إن كان هو نفسه")
        return Partner(name=name, kind="supplier")  # not saved: the page asks first
    return Partner.objects.create(name=name, kind="supplier", phone=(phone or "")[:40])


def _purchase_new_item(text, public=None, unit=""):
    """A name typed on a purchase line that is not picked from the list → (item, made). The item already there when the text is
    its exact name — a size / colour of a model too («بلوزة — M / أسود») — its code or its barcode; else a new stock item (the
    materials category of a café / restaurant, a drug of the library in a pharmacy, «أخرى» elsewhere) without a reorder alert."""
    from inventory import menu as MN
    from inventory import names as NM
    from inventory.models import ItemUnit

    name = NM.clean(text.split(" | ")[0])
    if len(name) < 2:
        raise ValidationError("اكتب اسم الصنف الجديد كاملًا")
    got = Item.objects.filter(active=True).filter(Q(code=name) | Q(barcode=name)).order_by("variant_of_id", "pk").first() \
        or getattr(ItemUnit.objects.filter(barcode=name, item__active=True).select_related("item").first(), "item", None)
    if got:
        return got, False
    k = NM.norm(name)
    got = NM.clash(name)
    if got:
        return Item.objects.filter(name=got, active=True, variant_of__isnull=True).first(), False
    got = next((v for v in Item.objects.filter(active=True, variant_of__isnull=False).only("pk", "name") if NM.norm(v.name) == k), None)
    if got:
        return Item.objects.get(pk=got.pk), False
    if (CompanySettings.get().activity or {}).get("key") == "pharmacy":  # a drug of the library: its Arabic names, generic, strips and price
        from inventory import drugs as D

        k = D.norm(name)
        r = next((r for r in D.library() if D.norm(r["en"]) == k), None)
        if r:  # the public price on the invoice is the box's (or the strip's when bought by the strip): it beats the library's reference
            n = D.guess_strips(r["en"], r["route"])
            box = (public * n if unit == "شريط" and n > 1 else public) if public and public > 0 else None
            return D.add(r, box_price=box)[0], True
    cat = (ItemCategory.objects.filter(name__startswith="خامات").order_by("pk").first() if CompanySettings.get().business in ("cafe", "gaming") else None) \
        or ItemCategory.objects.filter(name="أخرى").first() or ItemCategory.objects.exclude(name__startswith="مستلزمات").order_by("pk").first()
    if not cat:
        raise ValidationError("لا يوجد تصنيف للأصناف — أضف تصنيفًا أولًا")
    return Item.objects.create(code=MN._code(), name=name, category=cat, unit="قطعة", reorder_level=Z), True


# ------------------------------------------------------------------ customers & suppliers with balances
@login_required
def partners(request):
    _need_cash(request)
    kind = request.GET.get("kind", "customer")
    if kind not in ("customer", "supplier", "employee"):
        kind = "customer"
    if request.method == "POST" and "limit_for" in request.POST:
        if not (_is_manager(request.user) or request.user.has_perm("ledger.change_partner")):
            raise PermissionDenied
        pk = request.POST["limit_for"]
        p = Partner.objects.filter(pk=pk if pk.isdigit() else 0).first()
        try:
            if p is None:
                raise ValidationError("لم أجد هذا الطرف")
            p.credit_limit = num(request.POST.get("limit"))
            p.save(update_fields=["credit_limit"])
            messages.success(request, f"حد مديونية {p.name}: {p.credit_limit if p.credit_limit is not None else 'مفتوح'}")
        except ValidationError as e:
            _errs(request, e)
        return redirect(f"{request.path}?kind={kind}")
    if request.method == "POST" and request.POST.get("name", "").strip():
        if not (_is_manager(request.user) or request.user.has_perm("ledger.add_partner")):
            raise PermissionDenied
        new_kind = request.POST.get("kind", kind)
        Partner.objects.create(name=request.POST["name"].strip()[:200], kind=new_kind if new_kind in ("customer", "supplier", "employee") else kind,
                               phone=request.POST.get("phone", "")[:40])
        messages.success(request, "تمت الإضافة")
        return redirect(f"{request.path}?kind={kind}")
    code = {"customer": "1203010101", "supplier": "2301020101", "employee": "1203030115"}.get(kind, "1203010101")
    bal = {r["partner"]: (r["d"] or Z) - (r["c"] or Z) for r in JournalLine.objects.filter(account__code=code, entry__state__in=["posted", "reversed"])
           .values("partner").annotate(d=Sum("debit"), c=Sum("credit"))}
    rows = [(p, bal.get(p.pk, Z) * (-1 if kind == "supplier" else 1)) for p in Partner.objects.filter(kind=kind, active=True).order_by("name")]
    late = {}
    if kind == "customer":  # 1.25 instalments / credit sales past their due date
        from pos.credit.services import late_amount

        late = {p.pk: late_amount(p) for p, b in rows if b > 0}
    return render(request, "ui/shop_partners.html", dict(rows=rows, kind=kind, total=sum((b for _, b in rows), Z), code=code, late=late,
                                                         late_total=sum(late.values(), Z)))


# ------------------------------------------------------------------ production (kitchen)
from inventory import production as PR  # noqa: E402
from inventory.models import ProductionLine, ProductionOrder  # noqa: E402


@login_required
def production(request):
    if not request.user.has_perm("inventory.add_stockmove"):
        raise PermissionDenied
    if request.method == "POST" and "new" in request.POST:
        try:
            with transaction.atomic():
                lb = _b(request) if _locked(request) else None
                if lb and {str(request.POST.get("from_wh")), str(request.POST.get("to_wh"))} != {str(lb.warehouse_id)}:
                    raise ValidationError("التصنيع من مخزن فرعك وإليه فقط")
                po = ProductionOrder.objects.create(name=request.POST.get("name") or "تصنيع", date_out=dt.date.fromisoformat(request.POST["date"]),
                                                    from_warehouse_id=request.POST["from_wh"], to_warehouse_id=request.POST["to_wh"])
                for side in ("in", "out"):
                    for item_id, qty in zip(request.POST.getlist(f"{side}_item"), request.POST.getlist(f"{side}_qty")):
                        if item_id and qty:
                            ProductionLine.objects.create(order=po, side=side, item_id=item_id, qty=num(qty))
                PR.send_to_kitchen(po, user=request.user)
            messages.success(request, f"صُرفت الخامات للمعمل — تكلفتها {po.cost if hasattr(po, 'cost') else ''}")
        except (ValidationError, ValueError, KeyError) as e:
            _errs(request, e)
        return redirect("shop_production")
    if request.method == "POST" and "receive" in request.POST:
        try:
            po = ProductionOrder.objects.get(pk=request.POST["receive"])
            actual = {k[4:]: num(v) for k, v in request.POST.items() if k.startswith("out_") and v}
            PR.receive_output(po, dt.date.fromisoformat(request.POST.get("date_in") or timezone.localdate().isoformat()), actual, user=request.user)
            messages.success(request, f"الإنتاج دخل المخزن — {po.name}")
        except (ValidationError, ValueError, KeyError, ProductionOrder.DoesNotExist) as e:
            _errs(request, e)
        return redirect("shop_production")
    if request.method == "POST" and request.POST.get("cancel"):  # cancel (or cancel and redo = edit) a production order — owner / allowed
        try:
            if not V.can_edit(request.user):
                raise ValidationError("إلغاء التصنيع من حساب صاحب المحل")
            if not request.POST.get("reason", "").strip():
                raise ValidationError("اكتب السبب")
            po = ProductionOrder.objects.get(pk=request.POST["cancel"])
            PR.cancel(po, request.POST["reason"].strip(), request.user)
            if request.POST.get("redo"):
                messages.success(request, f"أُلغي «{po.name}» وعادت الخامات — عدّل الكميات في الأسفل وأرسله مرة أخرى")
                return redirect(f"{request.path}?redo={po.pk}#new")
            messages.success(request, f"أُلغي «{po.name}»: خرج المنتج النهائي وعادت الخامات إلى المخزن")
        except (ValidationError, ValueError, KeyError, ProductionOrder.DoesNotExist) as e:
            _errs(request, e)
        return redirect("shop_production")
    redo = ProductionOrder.objects.filter(pk=request.GET.get("redo") or 0).prefetch_related("lines").first()
    pb = _b(request) if _locked(request) else None  # a branch's kitchen works from its own store
    orders = ProductionOrder.objects.filter(**({"from_warehouse": pb.warehouse} if pb else {})).select_related("from_warehouse", "to_warehouse").prefetch_related("lines__item")[:100]
    return render(request, "ui/shop_production.html", dict(orders=orders, whs=Warehouse.objects.filter(active=True, **({"pk": pb.warehouse_id} if pb else {})), redo=redo, can_edit=V.can_edit(request.user),
                                                          items=Item.objects.filter(active=True).exclude(code="0").order_by("name"),
                                                          today=timezone.localdate().isoformat()))


# ------------------------------------------------------------------ day close (after the two shift closes)
@login_required
def day_close(request):
    if not can_see_cash(request.user):
        raise PermissionDenied
    try:
        day = dt.date.fromisoformat(request.GET.get("date") or request.POST.get("date") or "")
    except ValueError:
        day = timezone.localdate()
    b = _b(request)
    shifts = list(Shift.objects.filter(opened_at__date=day, **({"device__branch": b} if b else {})).select_related("device").order_by("opened_at"))
    orders = list(Order.objects.filter(created_at_device__date=day, **({"device__branch": b} if b else {})))
    t = _sales(orders)
    drawer = b.cashbox if b else CashBox.objects.filter(active=True, kind="cash").first()
    closed = T.last_closed_day()
    if request.method == "POST" and "close" in request.POST:
        if not (request.user.is_superuser or request.user.groups.filter(name="مدير المحل").exists()):
            raise PermissionDenied
        if b:
            shifts = list(Shift.objects.filter(opened_at__date=day))  # the final close is for the whole business
        if any(not s.closed_at for s in shifts):
            messages.error(request, "توجد وردية ما زالت مفتوحة — يجب أن يُغلقها الكاشير أولًا")
        else:
            try:
                counted = request.POST.get("counted")
                T.close_day(day, counts={drawer.pk: num(counted)} if counted and drawer else None, notes=request.POST.get("notes", ""),
                            user=request.user)
                messages.success(request, f"أُقفل يوم {day:%d/%m/%Y} نهائيًا — لا تعديل على حركاته بعد ذلك")
                from core.reports_send import send_daily

                for ch, ok, msg in send_daily(day, force=True):
                    (messages.success if ok else messages.error)(request, f"ملخص اليوم على {ch}: {msg}")
            except (ValidationError, TypeError) as e:
                _errs(request, e)
        return redirect(f"{request.path}?date={day.isoformat()}")
    unverified = [s for s in shifts if not s.verified]
    from urllib.parse import quote

    from pos.delivery import open_orders

    kinds = _kinds(orders)
    dq = [d for d in open_orders(b) if timezone.localtime(d.order.created_at_device).date() <= day]
    kinds["dlv_out"], kinds["dlv_n"] = sum((d.due for d in dq), Z), len(dq)

    wa_phone = "".join(ch for ch in CompanySettings.get().owner_whatsapp if ch.isdigit())
    wa = f"https://wa.me/{wa_phone}?text={quote(whatsapp_text(day))}" if wa_phone else f"https://wa.me/?text={quote(whatsapp_text(day))}"
    return render(request, "ui/shop_close.html", dict(day=day, shifts=shifts, t=t, k=kinds, drawer=drawer, drawer_bal=T.cashbox_balance(drawer, upto=day) if drawer else Z,
                                                      closed=closed and closed >= day, unverified=unverified, wa=wa,
                                                      branch=b, wallets=[(x, T.cashbox_balance(x, upto=day)) for x in ([b.cashbox] if b else CashBox.objects.filter(active=True))]))


# ------------------------------------------------------------------ money: drawings, expenses, wages, supplier payments
from pos import money as M  # noqa: E402
from pos.models import CashMove, ShopPartner  # noqa: E402


def _is_manager(user):
    return user.is_superuser or user.groups.filter(name="مدير المحل").exists()


@login_required
def money(request):
    _need_cash(request)
    b = _b(request)
    boxes = CashBox.objects.filter(active=True, **({"pk": b.cashbox_id} if b else {}))
    if request.method == "POST":
        if not (_is_manager(request.user) or request.user.has_perm("pos.add_cashmove")):
            messages.error(request, "صلاحيتك عرض فقط — تسجيل المصروفات والمسحوبات يحتاج إلى صلاحية «المصروفات والخزينة»")
            return redirect("shop_money")
        try:
            src = request.POST.get("source", "")
            box = None if src == "owner" else boxes.get(pk=src)
            import uuid

            if _locked(request) and request.POST.get("kind") in ("drawing", "deposit"):
                raise ValidationError("المسحوبات والإيداع لصاحب النشاط فقط")
            mv = M.post(request.POST["kind"], num(request.POST.get("amount")), dt.date.fromisoformat(request.POST["date"]),
                        cash_account=box.account if box else None, from_owner=src == "owner",
                        partner=Partner.objects.filter(pk=request.POST.get("partner") or 0).first(),
                        account=Account.objects.filter(pk=request.POST.get("account") or 0).first(),
                        deduct_advance=num(request.POST.get("adv")) or 0, deduct_penalty=num(request.POST.get("pen")) or 0,
                        note=request.POST.get("note", ""), uid=f"WEB-{uuid.uuid4().hex[:16]}",
                        cost_center=(b or (box and box.branch_set.first()) or getattr(Device.objects.select_related("branch").first(), "branch", None)).cost_center
                        if (b or Device.objects.exists()) else None,
                        by=request.user.get_full_name() or request.user.get_username(), user=request.user, strict_partner=True)
            messages.success(request, f"تم التسجيل: {mv.get_kind_display()} {mv.amount:,.2f}")
        except (ValidationError, ValueError, KeyError, CashBox.DoesNotExist) as e:
            _errs(request, e)
        return redirect("shop_money")
    return render(request, "ui/shop_money.html", dict(
        boxes=boxes, owner=M.owner_balance(), moves=(CashMove.objects.filter(entry__lines__account=b.cashbox.account).distinct() if b else CashMove.objects)
        .select_related("partner", "account", "entry")[:60],
        shop_partners=ShopPartner.objects.filter(active=True).select_related("partner"),
        employees=Partner.objects.filter(kind="employee", active=True).order_by("name"), suppliers=Partner.objects.filter(kind="supplier", active=True).order_by("name"),
        expenses=M.expense_accounts(), today=timezone.localdate().isoformat(),
        kinds=[k for k in CashMove.KINDS if k[0] != "delivery" and not (_locked(request) and k[0] in ("drawing", "deposit"))],
        drawings=sum((m.amount for m in CashMove.objects.filter(kind="drawing")), Z),
        paid_by_owner=sum((m.amount - m.deduct_advance - m.deduct_penalty for m in CashMove.objects.filter(from_owner=True)), Z)))


# ------------------------------------------------------------------ purchase returns & expired goods
@login_required
def returns(request):
    if not request.user.has_perm("inventory.add_stockmove"):
        raise PermissionDenied
    if request.method == "POST":
        try:
            with transaction.atomic():
                kind = request.POST["kind"]
                if kind not in ("preturn", "expired"):
                    raise ValidationError("نوع غير معروف")
                sup = Partner.objects.filter(pk=request.POST.get("supplier") or 0).first() if kind == "preturn" else None
                rb = _b(request)
                if rb and str(request.POST.get("warehouse")) != str(rb.warehouse_id):  # 1.25: a branch returns from its own store
                    raise ValidationError("المرتجع من مخزن فرعك فقط")
                mv = StockMove.objects.create(kind=kind, date=dt.date.fromisoformat(request.POST["date"]), warehouse_id=request.POST["warehouse"],
                                              partner=sup, created_by=request.user, description=request.POST.get("note", "")[:300])
                cc = None
                p = mv.warehouse.project
                cc = p if p.is_postable else p.children.filter(is_postable=True).first()
                from inventory.models import ItemUnit

                from ui import supply as SUP

                n = 0
                units, srcs = request.POST.getlist("unit"), request.POST.getlist("src")
                for k, (item_id, qty) in enumerate(zip(request.POST.getlist("item"), request.POST.getlist("qty"))):
                    if item_id and qty:
                        q = num(qty)
                        if not q or q <= 0:
                            raise ValidationError("يجب أن تكون الكمية أكبر من صفر")
                        u = ItemUnit.objects.filter(item_id=item_id, name=units[k]).first() if k < len(units) and units[k] else None
                        src = SUP.source_line(srcs[k], item_id) if kind == "preturn" and k < len(srcs) else None
                        if src:  # 1.24.1: a batch from the expiry page goes back to its own supplier at its own purchase cost
                            SUP.check_source(src, sup)
                        StockMoveLine.objects.create(move=mv, item_id=item_id, qty=q * (u.factor if u else 1), dim1=cc,  # 2 boxes = 2 × strips
                                                     unit_cost=SUP.batch_cost(src) if src else None,
                                                     note=f"من فاتورة {src.move.number}" if src else "")
                        n += 1
                if not n:
                    raise ValidationError("أضف صنفًا واحدًا على الأقل")
                INV.post_move(mv, user=request.user)
            messages.success(request, f"تم التسجيل {mv.get_kind_display()} {mv.number}")
        except (ValidationError, ValueError, KeyError) as e:
            _errs(request, e)
        return redirect("shop_returns")
    rb = _b(request)
    qs = StockMove.objects.filter(kind__in=["preturn", "expired"], **({"warehouse": rb.warehouse} if rb else {})).select_related("partner", "warehouse").order_by("-date", "-id")[:200]
    items = list(Item.objects.filter(active=True).exclude(code="0").order_by("name"))
    G = request.GET  # from the expiry page: «مرتجع للمورد» on a batch → this page filled in
    pre = None
    if (G.get("item") or "").isdigit():
        it = next((i for i in items if i.pk == int(G["item"])), None)
        if it:
            pre = dict(item=it.pk, text=f"{it.name} | {it.code}", qty=G.get("qty", "")[:12], unit="", src=(G.get("src") or "")[:12])
            from ui import supply as SUP

            src = SUP.source_line(pre["src"], it.pk)
            if src:  # the batch's own cost and supplier, shown before saving
                pre.update(src_txt=f"من فاتورة {src.move.number} بسعر شراء {SUP.batch_cost(src):,.2f} ج للوحدة")
    return render(request, "ui/shop_returns.html", dict(
        rows=[(m, sum((l.value or Z for l in m.lines.all()), Z)) for m in qs], whs=Warehouse.objects.filter(active=True, **({"pk": rb.warehouse_id} if rb else {})),
        suppliers=Partner.objects.filter(kind="supplier", active=True).order_by("name"), item_units=_purchase_units(items),
        items=items, today=timezone.localdate().isoformat(), pre=pre, pre_supplier=G.get("supplier", ""),
        pre_kind="expired" if G.get("kind") == "expired" else "preturn"))


# ------------------------------------------------------------------ stock valuation (cost and selling price)
@login_required
def valuation(request):
    _need_cash(request)
    from inventory.models import StockQuant

    b = _b(request)
    whs = Warehouse.objects.filter(active=True, **({"pk": b.warehouse_id} if b else {}))
    wh = b.warehouse if b else whs.filter(pk=request.GET.get("warehouse") or 0).first()  # 1.25: a branch sees its own store
    rows, tot = [], dict(cost=Z, sale=Z, qty_items=0)
    by_cat = defaultdict(lambda: dict(cost=Z, sale=Z))
    for it in Item.objects.filter(active=True).exclude(code="0").select_related("category").order_by("category__name", "name"):
        qty = (StockQuant.objects.filter(item=it, warehouse=wh).values_list("qty", flat=True).first() or Z) if wh else it.qty_on_hand
        if not qty:
            continue
        cost = qty * it.avg_cost
        sale = qty * (it.sale_price or Z)
        rows.append(dict(item=it, qty=qty, cost=cost, sale=sale, profit=sale - cost))
        tot["cost"] += cost
        tot["sale"] += sale
        by_cat[it.category.name]["cost"] += cost
        by_cat[it.category.name]["sale"] += sale
    tot["profit"] = tot["sale"] - tot["cost"]
    return render(request, "ui/shop_valuation.html", dict(rows=rows, tot=tot, cats=sorted(by_cat.items()), whs=whs, wh=wh))


def _bkey(request):
    from pos.branches import is_owner_level

    if request.user.is_superuser or is_owner_level(request.user):
        from core import backups as BK

        return BK.key()
    return ""


def _temp_password():
    """A one-off password the owner reads out to the employee — never a fixed «0000» anyone on the shop's Wi-Fi could try."""
    import secrets

    return f"{secrets.randbelow(900000) + 100000}"


# ------------------------------------------------------------------ shop settings for the owner: cashier devices & users
@login_required
def shop_settings(request):
    if not _is_manager(request.user):
        raise PermissionDenied
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Group

    from core.models import UserFlag
    from pos.models import Branch

    U = get_user_model()
    if request.method == "POST":
        a = request.POST.get("action")
        if a == "device":  # 1.25: the owner picks the branch the new till belongs to
            br = (Branch.objects.filter(pk=request.POST.get("branch") or 0, active=True).first() if not _locked(request) else None) or _b(request) \
                or Branch.objects.order_by("id").first()
            n = Device.objects.filter(branch=br).count() + 1
            while Device.objects.filter(branch=br, code=f"{n:02d}").exists():
                n += 1
            code = f"{n:02d}"
            Device.objects.create(branch=br, code=code, name=(request.POST.get("name") or "").strip()[:60] or f"كاشير {int(code)}")
            messages.success(request, f"أُضيف جهاز في «{br.name}» — افتح لوحة البيع على الجهاز الجديد وأنت داخل بحسابك واختر «اربط هذا الجهاز»")
        elif a == "toggle_device":
            d = Device.objects.get(pk=request.POST["id"])
            d.active = not d.active
            d.save(update_fields=["active"])
        elif a == "cashier":
            name = request.POST.get("username", "").strip()
            if name and not U.objects.filter(username=name).exists():
                u = U(username=name, first_name=request.POST.get("first_name", "") or name)
                tmp = _temp_password()
                u.set_password(tmp)
                u.save()
                from pos import branches as BR

                offered = BR.templates_for(CompanySettings.get().business)
                role = request.POST.get("role") if request.POST.get("role") in offered and request.POST.get("role") != "owner" else "cashier"
                ub = (Branch.objects.filter(pk=request.POST.get("branch") or 0, active=True).first() if not _locked(request) else None) or _b(request)
                BR.apply_staff(u, role, None, ub, request.POST.get("title", "") or offered[role][0], "", "")  # 1.25: the branch he works in
                if role == "doctor":  # his login opens his own «شاشة الدكتور»
                    from django.apps import apps

                    apps.get_model("clinic", "Doctor").objects.filter(pk=request.POST.get("doctor") or 0, user__isnull=True).update(user=u)
                UserFlag.objects.update_or_create(user=u, defaults={"must_change_password": True})
                messages.success(request, f"أُنشئ المستخدم «{name}» ({offered[role][0]}) — كلمة السر المؤقتة: {tmp} (سيغيّرها عند أول دخول، ولن تظهر مرة أخرى)")
            else:
                messages.error(request, "اكتب اسم مستخدم غير مكرر")
        elif a == "reset":
            u = U.objects.get(pk=request.POST["id"], is_superuser=False)
            tmp = _temp_password()
            u.set_password(tmp)
            u.save()
            UserFlag.objects.update_or_create(user=u, defaults={"must_change_password": True})
            messages.success(request, f"كلمة السر المؤقتة لـ«{u.username}»: {tmp} — يجب أن يغيّرها عند أول دخول (لن تظهر مرة أخرى)")
        elif a == "toggle_user":
            u = U.objects.get(pk=request.POST["id"], is_superuser=False)
            if u != request.user:
                u.is_active = not u.is_active
                u.save(update_fields=["is_active"])
        elif a == "cashier_edit" and V.is_owner(request.user):
            s = CompanySettings.get()
            s.cashier_can_edit = request.POST.get("on") == "1"
            s.save(update_fields=["cashier_can_edit"])
            messages.success(request, "أصبح بإمكان الكاشير إلغاء الفواتير وتعديلها" if s.cashier_can_edit else "أصبح الإلغاء والتعديل لصاحب المحل فقط — يرسل الكاشير طلبًا")
        elif a == "till" and V.is_owner(request.user):  # 1.25: the cashier's discount limit + the scale label
            from pos.services import set_till_cfg

            try:
                set_till_cfg(request.POST.get("disc_max", "0"), policy=request.POST.get("policy") if "policy" in request.POST else None)
                s = CompanySettings.get()
                pre = request.POST.get("scale_preset", "")
                if pre in SCALE_PRESETS:
                    s.scale_prefix, s.scale_item_digits, s.scale_mode = SCALE_PRESETS[pre][1:]
                    s.save(update_fields=["scale_prefix", "scale_item_digits", "scale_mode"])
                messages.success(request, "تم حفظ إعدادات الكاشير ✔ — تُطبّق على شاشة الكاشير خلال دقائق أو عند إعادة فتحها")
            except ValidationError as e:
                messages.error(request, "؛ ".join(e.messages))
        elif a == "insta" and V.is_owner(request.user):  # 1.25 InstaPay link → QR on receipts + «InstaPay» at the till
            from pos import instapay as IP

            try:
                link = IP.save(request.POST.get("insta", ""), rc=request.POST.get("rc") == "1")
                messages.success(request, "تم حفظ InstaPay ✔ — يظهر الـQR على الإيصال وزر «InstaPay» في الكاشير" if link else "تم الحفظ — لا يوجد رابط InstaPay")
            except ValidationError as e:
                messages.error(request, e.messages[0])
            return redirect("/ui/shop/settings/#insta")
        elif a == "plus" and V.is_owner(request.user):  # 1.26 optional till features (switches; details on their pages)
            from . import plus as PP

            try:
                PP.settings_post(request)
            except ValidationError as e:
                messages.error(request, "؛ ".join(e.messages))
        elif a == "backup":
            from core.management.commands.backup_now import run_backup

            s = CompanySettings.get()
            s.backup_drive_dir = request.POST.get("drive", "").strip()
            s.owner_whatsapp = request.POST.get("whatsapp", "").strip()
            s.save(update_fields=["backup_drive_dir", "owner_whatsapp"])
            if "now" in request.POST:
                try:
                    _, msg = run_backup()
                    messages.success(request, msg)
                except Exception as e:  # noqa: BLE001 — show the reason to the owner
                    messages.error(request, f"النسخة الاحتياطية فشلت: {e}")
            else:
                messages.success(request, "تم الحفظ")
        elif a == "alerts" and V.is_head(request.user):  # 1.25 the owner's WhatsApp alerts: his number + a switch per alert
            from core import alerts as AL

            s = CompanySettings.get()
            s.owner_whatsapp = request.POST.get("whatsapp", "").strip()[:40]
            s.save(update_fields=["owner_whatsapp"])
            try:
                AL.save_cfg(request.POST.getlist("on"), request.POST.get("short_min"), s)
                if "test" in request.POST:
                    if not AL.owner_number(s):
                        messages.error(request, "اكتب رقم واتساب صاحب المحل أولًا")
                    else:
                        res = AL.deliver("void", "هذا تنبيه تجربة من الإعدادات — إن وصلك فالتنبيهات تعمل ✔", s)
                        if res.get("wa") == "ok":
                            messages.success(request, "أُرسل تنبيه التجربة على واتساب ✔")
                        else:
                            fb = "، ".join(f"{'تليجرام' if k == 'tg' else 'البريد'}: {'وصل ✔' if v == 'ok' else v}" for k, v in res.items() if k in ("tg", "mail"))
                            msg = "واتساب التلقائي غير مفعّل على خادم Trio بعد" if res.get("wa") == "off" else f"واتساب: {res.get('wa')}"
                            (messages.success if "ok" in res.values() else messages.error)(request, msg + (f" — {fb}" if fb else ""))
                else:
                    messages.success(request, "حُفظت التنبيهات ✔")
            except ValidationError as e:
                messages.error(request, "؛ ".join(e.messages))
        elif a == "report" and request.user.is_superuser:
            s = CompanySettings.get()
            for f in ("telegram_token", "telegram_chat", "email_to", "smtp_host", "smtp_user"):
                if f in request.POST:
                    setattr(s, f, request.POST.get(f, "").strip())
            if request.POST.get("smtp_password"):
                s.smtp_password = request.POST["smtp_password"].strip()
            s.smtp_port = int(request.POST.get("smtp_port") or s.smtp_port or 587)
            s.report_hour = int(request.POST.get("report_hour") or 23)
            s.save()
            if "find_chat" in request.POST:
                from core.reports_send import telegram_chats

                try:
                    chats = telegram_chats(s.telegram_token)
                    if chats:
                        s.telegram_chat = ",".join(chats)
                        s.save(update_fields=["telegram_chat"])
                        messages.success(request, "وُجد: " + "، ".join(f"{n} ({i})" for i, n in chats.items()))
                    else:
                        messages.error(request, "لا توجد محادثة — اطلب من صاحب المحل فتح البوت والضغط على Start الأول")
                except Exception as e:  # noqa: BLE001
                    messages.error(request, f"تليجرام: {e}")
            elif "test" in request.POST:
                from core.reports_send import send_daily

                res = send_daily(force=True)
                if not res:
                    messages.error(request, "لا توجد قناة تلقائية مفعّلة")
                for ch, ok, msg in res:
                    (messages.success if ok else messages.error)(request, f"{ch}: {msg}")
            else:
                messages.success(request, "تم حفظ إعدادات ملخص اليوم")
        elif a == "branch" and not _locked(request):  # older forms / scripts: now the same proper branch as «الفروع» makes
            from pos import branches as BR

            try:
                br = BR.create_branch(request.POST.get("name", ""))
                messages.success(request, f"أُنشئ فرع «{br.name}» بمخزنه وخزينته وجهاز كاشير")
            except ValidationError as e:
                messages.error(request, e.messages[0])
        elif a == "receipt":
            s = CompanySettings.get()
            s.phone, s.address = request.POST.get("phone", ""), request.POST.get("address", "")
            s.save(update_fields=["phone", "address"])
            br = Branch.objects.first()
            if br:
                br.receipt_footer = request.POST.get("footer", "")
                br.save(update_fields=["receipt_footer"])
            f = request.FILES.get("logo")
            if f:  # the logo on the receipt and the program header (PNG / JPG / SVG)
                from core.setup_service import logo_svg

                if f.size > 2 * 1024 * 1024:
                    messages.error(request, "حجم اللوجو كبير — الحد الأقصى 2 ميجا")
                    return redirect("shop_settings")
                try:
                    s.logo_svg = logo_svg(f.read(), f.name)
                    s.save(update_fields=["logo_svg"])
                except Exception:  # noqa: BLE001
                    messages.error(request, "ملف اللوجو غير صالح — استخدم PNG أو JPG أو SVG")
                    return redirect("shop_settings")
            elif request.POST.get("no_logo") == "1":
                s.logo_svg = ""
                s.save(update_fields=["logo_svg"])
            messages.success(request, "تم الحفظ ✔")
        elif a == "kitchen":
            s = CompanySettings.get()
            mode = request.POST.get("mode")
            s.kitchen_ticket = mode if mode in dict(s.KITCHEN) else "off"
            s.kitchen_printer = request.POST.get("printer", "").strip()[:120]
            if s.kitchen_ticket == "printer" and not s.kitchen_printer:
                messages.error(request, "اختر طابعة المطبخ")
                return redirect("shop_settings")
            s.save(update_fields=["kitchen_ticket", "kitchen_printer"])
            if "test" in request.POST:
                from pos import kitchen

                try:
                    kitchen.test_page()
                    messages.success(request, f"تم إرسال صفحة تجربة إلى «{s.kitchen_printer}» ✔")
                except (RuntimeError, OSError) as e:
                    messages.error(request, str(e))
            else:
                messages.success(request, "تم حفظ إعداد ورقة المطبخ ✔ — أعد فتح شاشة الكاشير ليُطبّق")
        return redirect("shop_settings")
    from pos.models import Branch

    from core.management.commands.backup_now import drive_candidates

    from core.activities import current

    return render(request, "ui/shop_settings.html", dict(act=current(),devices=Device.objects.select_related("branch").order_by("branch__code", "code"),
                                                          branches=Branch.objects.all(), drives=drive_candidates(),
                                                          users=U.objects.filter(is_superuser=False).order_by("username"),
                                                          s=CompanySettings.get(), br=Branch.objects.first(), bkey=_bkey(request),
                                                          printers=__import__("pos.kitchen", fromlist=["x"]).printers(),
                                                          insta=__import__("pos.instapay", fromlist=["x"]).cfg(), till=__import__("pos.services", fromlist=["x"]).till_cfg(), policy=__import__("pos.services", fromlist=["x"]).receipt_policy(), plus=__import__("pos.plus", fromlist=["x"]).cfg(), scale_presets=[
                                                              (k, v[0]) for k, v in SCALE_PRESETS.items()], scale_now=_scale_now(), off=__import__("core.activities", fromlist=["x"]).unused(),
                                                          **_alerts_ctx(), **_user_roles()))


def _alerts_ctx():
    """1.25: «تنبيهات صاحب المحل» in the settings — what is on, and whether Trio's server sends WhatsApp yet."""
    from core import alerts as AL
    from core import mobile as MB

    try:
        wa_on, base = bool(MB.info().get("wa")), MB.api()
    except Exception:  # noqa: BLE001 — offline
        wa_on, base = False, ""
    c = AL.cfg()
    return dict(alerts=c, alert_kinds=[(k, v, c["on"][k]) for k, v, _, _ in AL.KINDS], wa_on=wa_on, wa_guide=f"{base}/wa/guide" if base else "")


# 1.25 scale label presets: (label, prefix, item digits, mode) — 13 digits with the check digit
SCALE_PRESETS = {
    "w2": ("يبدأ بـ 2 — كود الصنف 5 أرقام — الوزن بالجرام (الافتراضي)", "2", 5, "weight"),
    "w21": ("يبدأ بـ 21 — كود الصنف 5 أرقام — الوزن بالجرام (CAS / DIGI)", "21", 5, "weight"),
    "w20": ("يبدأ بـ 20 — كود الصنف 5 أرقام — الوزن بالجرام", "20", 5, "weight"),
    "p2": ("يبدأ بـ 2 — كود الصنف 5 أرقام — السعر بالقرش", "2", 5, "price"),
    "p22": ("يبدأ بـ 22 — كود الصنف 5 أرقام — السعر بالقرش", "22", 5, "price"),
}


def _scale_now():
    s = CompanySettings.get()
    for k, v in SCALE_PRESETS.items():
        if (v[1], v[2], v[3]) == (s.scale_prefix, s.scale_item_digits, s.scale_mode):
            return k
    return ""


def _user_roles():
    """The roles the settings page can give a new user (+ the clinic's doctors to link a doctor's login to)."""
    from django.apps import apps

    from pos import branches as BR

    s = CompanySettings.get()
    roles = [(k, v) for k, v in BR.templates_for(s.business).items() if k not in ("owner", "custom", "branch_manager")]
    first = "reception" if s.business == "clinic" else "cashier"  # a clinic hires a receptionist first, not a cashier
    roles.sort(key=lambda kv: kv[0] != first)
    docs = list(apps.get_model("clinic", "Doctor").objects.filter(active=True, user__isnull=True)) if s.business == "clinic" else []
    return dict(roles=roles, clinic_doctors=docs)


# ------------------------------------------------------------------ cancelling (owner) and cashier requests
from pos import voids as V  # noqa: E402
from pos.models import ChangeRequest  # noqa: E402


@login_required
def void(request):
    """POST: what=order|cashmove|move|zero, id, reason — owner only."""
    if request.method != "POST":
        return redirect("/")
    from core.middleware import safe_next

    back = safe_next(request)
    reason = request.POST.get("reason", "").strip()
    try:
        if not reason:
            raise ValidationError("اكتب سبب الإلغاء")
        what, pk = request.POST["what"], request.POST["id"]
        if what == "order":
            o = Order.objects.select_related("device").get(pk=pk)
            _own_branch(request, o.device.branch_id)
            V.void_order(o, reason, request.user)
        elif what == "cashmove":
            V.void_cash_move(CashMove.objects.get(pk=pk), reason, request.user)
        elif what == "move":
            V.void_stock_move(StockMove.objects.get(pk=pk), reason, request.user)
        elif what == "zero":
            V.zero_partner(Partner.objects.get(pk=pk), request.POST["code"], reason, request.user)
        messages.success(request, "تم — وسُجّل قيد عكسي بالسبب")
    except (ValidationError, KeyError, ObjectDoesNotExist) as e:
        _errs(request, e)
    return redirect(back)


@login_required
def order_find(request):
    uid = request.GET.get("uid", "").strip()
    o = Order.objects.filter(uid=uid).first() or Order.objects.filter(uid__endswith=uid).order_by("-id").first() if uid else None
    if not o:
        messages.error(request, f"لم أجد الفاتورة {uid} — ربما لم تصل بعد من جهاز الكاشير")
        return redirect("/")
    return redirect("order_edit", o.pk)


@login_required
def order_edit(request, pk):
    """One invoice: cancel it, cancel one item in it, or change quantities / prices / items (owner — or a cashier when allowed)."""
    order = Order.objects.select_related("device", "shift", "customer").get(pk=pk)
    if not (V.can_edit(request.user) or can_see_cash(request.user)):
        raise PermissionDenied
    _own_branch(request, order.device.branch_id)
    editable = V.can_edit(request.user) and not order.cancelled
    if request.method == "POST" and editable:
        reason = request.POST.get("reason", "").strip()
        try:
            if not reason:
                raise ValidationError("اكتب السبب")
            if request.POST.get("action") == "void":
                V.void_order(order, reason, request.user)
                messages.success(request, f"الفاتورة {order.uid} أُلغيت وعادت البضاعة إلى المخزن")
                return redirect("shop_sales")
            if request.POST.get("action") == "pkg_refund":  # 1.24.1 a partly played hall package: the unused minutes come back
                if not V.is_owner(request.user):
                    raise PermissionDenied
                from gaming.bookings import refund_package

                ret = refund_package(order, reason, request.user)
                messages.success(request, f"سُجّل الاسترداد {ret.total:,.2f} ج بالفاتورة {ret.uid} وخُصمت الدقائق من رصيد العميل")
                return redirect("order_edit", order.pk)
            lines = []
            for i in range(int(request.POST.get("rows") or 0)):
                code = request.POST.get(f"code_{i}", "").strip()
                if not code or request.POST.get(f"drop_{i}"):
                    continue
                lines.append(dict(item_code=code, qty=num(request.POST.get(f"qty_{i}")) or 0, price=num(request.POST.get(f"price_{i}")) or 0,
                                  discount=num(request.POST.get(f"disc_{i}")) or 0))
            new = V.edit_order(order, lines, reason, request.user, pay=request.POST.get("pay", ""))
            messages.success(request, f"عُدّلت — الفاتورة الجديدة {new.uid} بإجمالي {new.total:,.2f}")
            return redirect("order_edit", new.pk)
        except (ValidationError, ObjectDoesNotExist, ValueError) as e:
            _errs(request, e)
        return redirect("order_edit", order.pk)
    items = Item.objects.filter(active=True, sale_price__isnull=False).order_by("name").values("code", "name", "sale_price")
    later = Order.objects.filter(uid__startswith=order.uid + "-E").order_by("-id").first()
    from gaming.bookings import refund_info

    pkg = refund_info(order) if editable and V.is_owner(request.user) else None
    return render(request, "ui/order_edit.html", dict(o=order, lines=list(order.lines.select_related("item")), editable=editable, items=items,
                                                      insta_on=__import__("pos.instapay", fromlist=["x"]).on(),
                                                      later=later, can_cash=can_see_cash(request.user), pkg=pkg))


@login_required
def requests_page(request):
    owner = V.is_owner(request.user)
    if request.method == "POST":
        try:
            if "decide" in request.POST:
                V.decide(ChangeRequest.objects.get(pk=request.POST["decide"]), request.POST.get("approve") == "1", request.user,
                         request.POST.get("note", ""))
                messages.success(request, "تم تسجيل القرار")
            elif "new" in request.POST:
                import uuid

                if not request.POST.get("target") or not request.POST.get("reason"):
                    raise ValidationError("اكتب رقم الفاتورة/الحركة والسبب")
                ChangeRequest.objects.create(uid=f"WEB-{uuid.uuid4().hex[:16]}", kind=request.POST.get("kind", "void"),
                                             target=request.POST["target"].strip(), reason=request.POST["reason"][:300],
                                             requested_by=request.user.first_name or request.user.get_username())
                messages.success(request, "أُرسل الطلب إلى صاحب المحل")
        except (ValidationError, ChangeRequest.DoesNotExist) as e:
            _errs(request, e)
        return redirect("shop_requests")
    qs = ChangeRequest.objects.all() if owner else ChangeRequest.objects.filter(
        requested_by__in=[request.user.first_name, request.user.get_username()])
    return render(request, "ui/shop_requests.html", dict(pending=qs.filter(state="pending"), done=qs.exclude(state="pending")[:100], owner=owner))


# ------------------------------------------------------------------ offers, units, labels, expiry, WhatsApp summary
from inventory.models import ItemUnit  # noqa: E402
from pos.models import Promotion  # noqa: E402


@login_required
def promos(request):
    if not V.is_head(request.user):
        raise PermissionDenied
    if request.method == "POST":
        try:
            if "delete" in request.POST:  # an offer is only a rule: the sales it gave keep their discount lines
                Promotion.objects.filter(pk=request.POST["delete"]).delete()
                messages.success(request, "حُذف العرض")
            elif "toggle" in request.POST:
                p = Promotion.objects.get(pk=request.POST["toggle"])
                p.active = not p.active
                p.save(update_fields=["active"])
            else:
                Promotion.objects.create(name=request.POST["name"] or "عرض", kind=request.POST["kind"], item_id=request.POST["item"],
                                         value=num(request.POST.get("value")) or 0, min_qty=num(request.POST.get("min_qty")) or 1,
                                         free_qty=num(request.POST.get("free_qty")) or 0, start=dt.date.fromisoformat(request.POST["start"]),
                                         end=dt.date.fromisoformat(request.POST["end"]))
                messages.success(request, "أُضيف العرض — سيظهر في الكاشير مع آخر تحديث للأصناف")
        except (KeyError, ValueError, Promotion.DoesNotExist) as e:
            _errs(request, e)
        return redirect("shop_promos")
    today = timezone.localdate()
    return render(request, "ui/shop_promos.html", dict(promos=Promotion.objects.select_related("item")[:200], kinds=Promotion.KINDS, today=today,
                                                        month=(today + dt.timedelta(days=30)), items=Item.objects.filter(active=True).exclude(code="0").order_by("name")))


@login_required
def units(request):
    if not request.user.has_perm("inventory.change_item"):
        raise PermissionDenied
    if request.method == "POST":
        try:
            if "delete" in request.POST:
                ItemUnit.objects.filter(pk=request.POST["delete"]).delete()
            else:
                factor, name = num(request.POST.get("factor")), (request.POST.get("name") or "").strip()[:30]
                price = num(request.POST.get("price"))
                if not name:
                    raise ValueError("اكتب اسم الوحدة (كرتونة، علبة…)")
                if not factor or factor <= 0:
                    raise ValueError("عدد القطع في الوحدة يجب أن يكون رقمًا أكبر من صفر")
                if price is not None and price < 0:
                    raise ValueError("السعر لا يكون سالبًا")
                item = Item.objects.filter(pk=request.POST.get("item") if str(request.POST.get("item") or "").isdigit() else 0).first()
                if not item:
                    raise ValueError("اختر الصنف")
                if ItemUnit.objects.filter(item=item, name=name).exists():
                    raise ValueError(f"الوحدة «{name}» موجودة بالفعل لهذا الصنف")
                ItemUnit.objects.create(item=item, name=name, factor=factor, barcode=request.POST.get("barcode", "").strip()[:60], sale_price=price)
                messages.success(request, "أُضيفت الوحدة")
        except (KeyError, ValueError, TypeError, IntegrityError) as e:
            _errs(request, e)
        return redirect("shop_units")
    return render(request, "ui/shop_units.html", dict(units=ItemUnit.objects.select_related("item").order_by("item__name"),
                                                       items=Item.objects.filter(active=True).exclude(code="0").order_by("name")))


@login_required
def labels(request):
    if not request.user.has_perm("inventory.view_item"):
        raise PermissionDenied
    q = request.GET.get("q", "").strip()
    qs = Item.objects.filter(active=True, sale_price__isnull=False).exclude(code="0").order_by("name")
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(barcode=q) | Q(code=q))
    parent = Item.objects.filter(pk=request.GET.get("parent") or 0, variant_of__isnull=True).first()
    if parent:  # 1.25: the labels of one product's sizes / colours, as many as are in stock
        from inventory import variants as VR

        qs = VR.variants(parent)
        for v in qs:
            v.n_default, v.lbl_on = (max(1, int(v.qty_on_hand)), True) if v.qty_on_hand > 0 else (1, False)  # only what is on the shelf
    move = StockMove.objects.filter(pk=request.GET.get("move") or 0, kind__in=("receipt", "opening")).first() if str(request.GET.get("move", "")).isdigit() else None
    if move:  # 1.24.1 «اطبع ملصقات هذه الفاتورة»: every item of a purchase, as many labels as were received
        got = {}
        for ln in move.lines.select_related("item__variant_of"):
            got[ln.item_id] = got.get(ln.item_id, Decimal(0)) + ln.qty
        qs = sorted(Item.objects.filter(pk__in=got).select_related("variant_of"), key=lambda i: i.name)
        for v in qs:
            v.n_default, v.lbl_on = max(1, min(int(got[v.pk]), 200)), True
    chosen = []
    if request.method == "POST":
        for it in Item.objects.filter(pk__in=request.POST.getlist("item")).select_related("variant_of"):
            n = int(num(request.POST.get(f"n_{it.pk}")) or 1)
            chosen += [it] * max(1, min(n, 200))
    return render(request, "ui/shop_labels.html", dict(items=qs[:300], chosen=chosen, q=q, company=CompanySettings.get(), parent=parent, move=move))


def expiring(days=30):
    """Batches still on the shelf whose expiry is close (or passed). The stock left of an item is taken from its newest receipts
    (first expiry sells first), so a batch already sold out does not alarm."""
    today = timezone.localdate()
    limit = today + dt.timedelta(days=days)
    rows = []
    lines = (StockMoveLine.objects.filter(expiry__isnull=False, move__state="posted", move__kind__in=["receipt", "opening"], item__qty_on_hand__gt=0)
             .select_related("item", "move").order_by("item_id", "-move__date", "-id"))
    left, cur = Decimal(0), None
    for ln in lines:
        if ln.item_id != cur:
            cur, left = ln.item_id, ln.item.qty_on_hand
        if left <= 0:
            continue
        in_stock = min(ln.qty, left)
        left -= in_stock
        if ln.expiry <= limit:
            rows.append(dict(item=ln.item, expiry=ln.expiry, produced=ln.produced, qty=in_stock, days=(ln.expiry - today).days, move=ln.move, line=ln,
                             value=(in_stock * ln.item.avg_cost).quantize(Decimal("0.01"))))
    return sorted(rows, key=lambda r: r["expiry"])


@login_required
def expiry(request):
    if not request.user.has_perm("inventory.view_item"):
        raise PermissionDenied
    dflt = 180 if (CompanySettings.get().activity or {}).get("key") == "pharmacy" else 30  # a pharmacy returns to the company months before
    try:
        days = max(1, min(int(request.GET.get("days") or dflt), 365))
    except ValueError:
        days = dflt
    rows = expiring(days)
    return render(request, "ui/shop_expiry.html", dict(rows=rows, days=days, expired=[r for r in rows if r["days"] < 0],
                                                       value=sum((r["value"] for r in rows), Z)))


def whatsapp_text(day):
    orders = list(Order.objects.filter(created_at_device__date=day))
    t = _sales(orders)
    cogs = _cogs(orders)
    from pos.money import owner_balance

    drawer = CashBox.objects.filter(active=True, kind="cash").first()
    lines = [f"ملخص {CompanySettings.get().name} — {day:%d/%m/%Y}",
             f"المبيعات: {t['net']:,.2f} ج ({t['n']} فاتورة)", f"نقدي {t['cash']:,.2f} — فيزا/محافظ {t['card']:,.2f}" + (f" — InstaPay {t['insta']:,.2f}" if t['insta'] else "") + f" — آجل {t['credit']:,.2f}",
             f"مجمل الربح التقريبي: {t['net_ex_vat'] - cogs:,.2f}", f"رصيد الدرج: {T.cashbox_balance(drawer, upto=day) if drawer else 0:,.2f}",
             f"رصيد صاحب المحل: {owner_balance():,.2f}", "بواسطة برنامج Trio"]
    return "\n".join(lines)


# ------------------------------------------------------------------ recipes & suggested price (cafe / gaming hall)
@login_required
def recipes(request):
    from inventory import recipes as R
    from inventory.models import Recipe, RecipeLine

    if not (_is_manager(request.user) or request.user.has_perm("inventory.change_item")):
        raise PermissionDenied
    s = CompanySettings.get()
    vat = s.default_vat_rate or Z
    if request.method == "POST":
        try:
            with transaction.atomic():
                if request.POST.get("op") == "ingredient":
                    from inventory import names as NM

                    name = NM.clean(request.POST.get("name"))
                    if not name:
                        raise ValidationError("اكتب اسم الخامة")
                    NM.check(name)
                    stock = Account.objects.get(code="1202010101")
                    cat, _ = ItemCategory.objects.get_or_create(name="خامات المطبخ والبار", defaults=dict(
                        stock_account=stock, farm_account=Account.objects.get(code="3101010101"), admin_account=Account.objects.get(code="3101010101"),
                        needs_equipment=False))
                    last = Item.objects.filter(code__regex=r"^\d+$").order_by("-id").first()
                    code = str((int(last.code) + 1) if last else 1001)
                    while Item.objects.filter(code=code).exists():
                        code = str(int(code) + 1)
                    from inventory.reorder import parse as reorder_of

                    Item.objects.create(code=code, name=name, category=cat, unit=request.POST.get("unit") or "جرام", std_cost=num(request.POST.get("cost")),
                                        reorder_level=reorder_of(request.POST.get("reorder")))
                    messages.success(request, f"أُضيفت الخامة «{name}» — تُستخدم تكلفتها التقديرية حتى أول شراء")
                elif request.POST.get("op") == "remove":  # off the menu: the item stops selling; its history and recipe stay
                    r = Recipe.objects.select_related("item").get(pk=request.POST["recipe"])
                    r.item.active = False
                    r.item.save(update_fields=["active"])
                    messages.success(request, f"أُزيل «{r.item.name}» من المنيو — يمكنك إعادته من الأصناف")
                elif request.POST.get("op") == "price":
                    r = Recipe.objects.select_related("item").get(pk=request.POST["recipe"])
                    if (num(request.POST.get("price")) or 0) < 0:
                        raise ValueError("السعر لا يكون سالبًا")
                    r.item.sale_price = num(request.POST.get("price"))
                    r.item.save(update_fields=["sale_price"])
                    messages.success(request, f"سعر «{r.item.name}» أصبح {r.item.sale_price}")
                else:
                    rid = request.POST.get("recipe")
                    if rid:
                        r = Recipe.objects.select_related("item").get(pk=rid)
                        it = r.item
                    elif request.POST.get("item"):  # ingredients for a product (or one size) already on the menu (1.22)
                        it = Item.objects.get(pk=request.POST["item"], active=True)
                        r = Recipe.objects.filter(item=it).first() or Recipe(item=it)
                    else:
                        name = request.POST.get("item_name", "").strip()
                        if not name:
                            raise ValidationError("اكتب اسم المشروب / الأكلة")
                        if Item.objects.filter(name=name, active=True).exists():
                            raise ValidationError(f"فيه صنف اسمه «{name}» بالفعل — افتح وصفته وعدّلها")
                        last = Item.objects.filter(code__regex=r"^\d+$").order_by("-id").first()
                        code = str((int(last.code) + 1) if last else 1001)
                        while Item.objects.filter(code=code).exists():
                            code = str(int(code) + 1)
                        it = Item.objects.create(code=code, name=name, category=ItemCategory.objects.get(pk=request.POST["category"]), unit="واحدة",
                                                 item_type="recipe")
                        r = Recipe(item=it)
                    it.name = request.POST.get("item_name", it.name).strip() or it.name
                    it.item_type = "recipe"
                    if request.POST.get("category"):
                        it.category_id = int(request.POST["category"])
                    it.save()
                    r.target_margin = num(request.POST.get("margin")) or Decimal("65")
                    r.extra_cost = num(request.POST.get("extra")) or Z
                    r.notes = request.POST.get("notes", "")[:300]
                    r.save()
                    r.lines.all().delete()
                    n = 0
                    for i in range(int(request.POST.get("rows") or 0)):
                        ing, q = request.POST.get(f"ing_{i}"), num(request.POST.get(f"qty_{i}"))
                        if ing and q:
                            if int(ing) == it.pk:
                                raise ValidationError("لا يمكن أن يكون الصنف خامة في نفسه")
                            RecipeLine.objects.create(recipe=r, ingredient_id=int(ing), qty=q, waste_pct=num(request.POST.get(f"waste_{i}")) or Z)
                            n += 1
                    if not n:
                        raise ValidationError("أضف خامة واحدة على الأقل")
                    c = R.costing(r, vat, s.pos_prices_include_vat)
                    if request.POST.get("use_suggested") == "1" or it.sale_price is None:
                        it.sale_price = c["suggested"]
                        it.save(update_fields=["sale_price"])
                    messages.success(request, f"تم حفظ صنف المنيو «{it.name}» — التكلفة {c['cost']:.2f} والسعر المقترح {c['suggested']}")
        except (ValidationError, ObjectDoesNotExist, ValueError) as e:
            _errs(request, e)
        return redirect(request.get_full_path())
    from inventory import menu as MN

    recs = {r.item_id: r for r in Recipe.objects.filter(item__active=True).select_related("item__category")}
    rows, shown = [], set()
    prods, _addons = MN.products()
    for pr in prods:  # every product of the menu (each size apart), with its ingredients or «+ المكونات»
        for it in pr["sizes"] or [pr["item"]]:
            if it.item_type == "stock":  # a can of soda is bought and sold as it is
                continue
            r = recs.get(it.pk)
            shown.add(it.pk)
            rows.append(dict(item=it, label=f"{pr['item'].name} — {it.size_name}" if it.variant_of_id else it.name, cat=pr["item"].category.name,
                             r=r, c=R.costing(r, vat, s.pos_prices_include_vat) if r else None))
    for r in sorted((r for k, r in recs.items() if k not in shown), key=lambda r: (r.item.category.name, r.item.name)):
        rows.append(dict(item=r.item, label=r.item.name, cat=r.item.category.name, r=r, c=R.costing(r, vat, s.pos_prices_include_vat)))
    edit = Recipe.objects.filter(pk=request.GET.get("edit") or 0).first()
    for_item = None
    if not edit and request.GET.get("item"):
        for_item = Item.objects.filter(pk=request.GET["item"], active=True).first()
        edit = Recipe.objects.filter(item=for_item).first() if for_item else None
        if edit:
            for_item = None
    ings = list(R.ingredients())
    costs = {i.pk: str(R.unit_cost(i)) for i in ings}
    import json

    sell_cats = ItemCategory.objects.exclude(name__startswith="خامات").exclude(name__startswith="مستلزمات").order_by("name")
    return render(request, "ui/shop_recipes.html", dict(
        rows=rows, edit=edit, for_item=for_item, edit_lines=list(edit.lines.all()) if edit else [], ings=ings, cats=sell_cats, vat=vat,
        incl=s.pos_prices_include_vat, js=json.dumps(dict(costs=costs, units={i.pk: i.unit for i in ings}, vat=str(vat), incl=s.pos_prices_include_vat,
                                                          lines=[dict(ing=ln.ingredient_id, qty=str(ln.qty), waste=str(ln.waste_pct))
                                                                 for ln in (edit.lines.all() if edit else [])]))))


# ------------------------------------------------------------------ stock count inside the program (every plan)
def _count_rows(wh, counted):
    """counted: {item_id: Decimal} → rows with book qty, difference and its value at average cost."""
    from inventory.models import StockQuant

    book = {q.item_id: q.qty for q in StockQuant.objects.filter(warehouse=wh)}
    rows = []
    for it in Item.objects.filter(pk__in=counted.keys()).order_by("name"):
        b = book.get(it.pk, Z)
        c = counted[it.pk]
        cost = it.avg_cost if it.qty_on_hand else (it.std_cost or Z)
        rows.append(dict(item=it.pk, code=it.code, name=it.name, unit=it.unit, book=b, counted=c, diff=c - b,
                         cost=cost.quantize(Decimal("0.0001")), value=((c - b) * cost).quantize(Decimal("0.01"))))
    return rows


@login_required
def count(request):
    """جرد: every item with its book balance, type what was counted, see the differences, then settle them (shortage → losses)."""
    if not request.user.has_perm("inventory.view_item"):
        raise PermissionDenied
    if CompanySettings.get().business in ("gaming", "clinic") and not (V.is_owner(request.user) or request.user.has_perm("inventory.add_stockmove")):
        # 1.24.1 the hall worker / the reception: counting the buffet or the store is the owner's (or the store keeper's)
        raise PermissionDenied("جرد المخزن لصاحب المحل أو أمين المخزن فقط")
    from inventory import count as CNT

    b = _b(request)
    whs = Warehouse.objects.filter(active=True, **({"pk": b.warehouse_id} if b else {}))
    src = request.POST if request.method == "POST" else request.GET
    wh = whs.filter(pk=src.get("warehouse") or 0).first() or whs.first()
    cats = ItemCategory.objects.filter(item__item_type="stock", item__active=True).distinct().order_by("name")
    q, cat = src.get("q", "").strip(), src.get("cat", "")
    items = Item.objects.filter(active=True, item_type="stock").exclude(code="0").select_related("category").order_by("category__name", "name")
    if cat:
        items = items.filter(category_id=cat)
    if q:
        items = items.filter(Q(name__icontains=q) | Q(code__icontains=q) | Q(barcode=q))
    from inventory.models import StockQuant

    book = {qq.item_id: qq.qty for qq in StockQuant.objects.filter(warehouse=wh)}
    ctx = dict(whs=whs, wh=wh, cats=cats, cat=cat, q=q, date=src.get("date") or timezone.localdate().isoformat(),
               note=src.get("note", ""), can_post=request.user.has_perm("inventory.change_stockmove"))
    counted = {}
    if request.method == "POST":
        for k, v in request.POST.items():
            if k.startswith("c_") and v.strip() != "":
                n = num(v)
                if n is None or n < 0:
                    messages.error(request, "توجد كمية ليست رقمًا صحيحًا")
                    break
                counted[int(k[2:])] = n
    if request.method == "POST" and request.POST.get("action") == "post":
        if not ctx["can_post"]:
            raise PermissionDenied("التسوية لصاحب المحل أو المدير")
        rows = [r for r in _count_rows(wh, counted) if r["diff"]]
        if not rows:
            messages.success(request, "لا توجد فروق — الجرد مطابق ✔")
            return redirect(f"{request.path}?warehouse={wh.pk}")
        try:
            CNT.post_adjustment(wh, dt.date.fromisoformat(ctx["date"]), [dict(r, cost=str(r["cost"])) for r in rows], note=ctx["note"], user=request.user)
        except ValidationError as e:
            _errs(request, e)
            return redirect(f"{request.path}?warehouse={wh.pk}")
        loss = -sum((r["value"] for r in rows if r["value"] < 0), Z)
        gain = sum((r["value"] for r in rows if r["value"] > 0), Z)
        messages.success(request, f"تمت التسوية ✔ — {len(rows)} صنف: عجز {loss:,.2f} سُجّلت خسائر مخزون، وزيادة {gain:,.2f}")
        return redirect(f"{request.path}?warehouse={wh.pk}")
    if request.method == "POST" and request.POST.get("action") == "review":
        rows = _count_rows(wh, counted)
        diffs = [r for r in rows if r["diff"]]
        ctx.update(review=True, rows=rows, diffs=diffs, counted_n=len(rows),
                   loss=-sum((r["value"] for r in diffs if r["value"] < 0), Z), gain=sum((r["value"] for r in diffs if r["value"] > 0), Z))
        ctx["net"] = ctx["gain"] - ctx["loss"]
        return render(request, "ui/shop_count.html", ctx)
    ctx["items"] = [dict(it=it, book=book.get(it.pk, Z), val=counted.get(it.pk, "")) for it in items[:2000]]
    ctx["recent"] = (StockMove.objects.filter(kind__in=["adjust_in", "adjust_out"], warehouse=wh).order_by("-date", "-id")[:12])
    ctx["recent"] = [(m, sum((l.value or Z for l in m.lines.all()), Z)) for m in ctx["recent"]]
    return render(request, "ui/shop_count.html", ctx)


# ------------------------------------------------------------------ damaged / expired goods (every plan)
@login_required
def damage(request):
    if not request.user.has_perm("inventory.add_stockmove"):
        raise PermissionDenied
    b = _b(request)
    whs = Warehouse.objects.filter(active=True, **({"pk": b.warehouse_id} if b else {}))
    if request.method == "POST":
        try:
            with transaction.atomic():
                wh = whs.get(pk=request.POST["warehouse"])
                reason = request.POST.get("reason", "تالف")
                mv = StockMove.objects.create(kind="expired", date=dt.date.fromisoformat(request.POST["date"]), warehouse=wh, created_by=request.user,
                                              description=f"{reason}{' — ' + request.POST['note'] if request.POST.get('note') else ''}"[:300])
                p = wh.project
                cc = p if p.is_postable else p.children.filter(is_postable=True).first()
                n = 0
                for item_id, qty in zip(request.POST.getlist("item"), request.POST.getlist("qty")):
                    if item_id and qty:
                        q = num(qty)
                        if not q or q <= 0:
                            raise ValidationError("يجب أن تكون الكمية أكبر من صفر")
                        StockMoveLine.objects.create(move=mv, item_id=item_id, qty=q, dim1=cc, note=reason[:200])
                        n += 1
                    elif qty and not item_id:
                        raise ValidationError("يوجد سطر صنفه غير معرّف — اختر الصنف من القائمة")
                if not n:
                    raise ValidationError("أضف صنفًا واحدًا على الأقل")
                INV.post_move(mv, user=request.user)
                v = sum((l.value or Z for l in mv.lines.all()), Z)
            messages.success(request, f"تم التسجيل {reason} {mv.number} — خسارة {v:,.2f} بالتكلفة وعُدّل الرصيد")
        except (ValidationError, ValueError, KeyError, Warehouse.DoesNotExist) as e:
            _errs(request, e)
        return redirect("shop_damage")
    qs = StockMove.objects.filter(kind="expired", **({"warehouse": b.warehouse} if b else {})).select_related("warehouse").order_by("-date", "-id")[:100]
    rows = [(m, sum((l.value or Z for l in m.lines.all()), Z)) for m in qs]
    month = timezone.localdate().replace(day=1)
    return render(request, "ui/shop_damage.html", dict(
        rows=rows, whs=whs, today=timezone.localdate().isoformat(), soon=expiring(30)[:8],
        month_loss=sum((v for m, v in rows if m.date >= month and m.state == "posted"), Z),
        items=Item.objects.filter(active=True, item_type="stock").exclude(code="0").order_by("name")))


# ------------------------------------------------------------------ account statement for the owner (customer / supplier / employee / partners)
STATEMENT_ACCOUNTS = {"1203010101": "كشف حساب عميل", "2301020101": "كشف حساب مورد", "1203030115": "كشف سلف موظف", "2304010101": "كشف حساب صاحب المحل / الشركاء",
                      "2301050103": "كشف حساب طبيب"}


@login_required
def statement(request):
    _need_cash(request)
    code = request.GET.get("account", "")
    if code not in STATEMENT_ACCOUNTS or (code == "2304010101" and _locked(request)):
        raise PermissionDenied
    partner = Partner.objects.filter(pk=request.GET.get("partner") or 0).first()
    d1, d2 = _range(request, 3650)
    qs = JournalLine.objects.filter(account__code=code, entry__state__in=["posted", "reversed"]).select_related("entry", "partner")
    if partner:
        qs = qs.filter(partner=partner)
    # customers / advances: + = they owe us; suppliers / owner / partners: + = we owe them
    owes_us = code in ("1203010101", "1203030115")
    sign = 1 if owes_us else -1
    before = qs.filter(entry__date__lt=d1).aggregate(d=Sum("debit"), c=Sum("credit"))
    run = sign * ((before["d"] or Z) - (before["c"] or Z))
    opening = run
    rows = []
    for ln in qs.filter(entry__date__range=(d1, d2)).order_by("entry__date", "entry_id", "id"):
        run += sign * (ln.debit - ln.credit)
        rows.append(dict(date=ln.entry.date, number=ln.entry.number, desc=ln.description or ln.entry.description, partner=ln.partner,
                         debit=ln.debit, credit=ln.credit, bal=run))
    return render(request, "ui/shop_statement.html", dict(title=STATEMENT_ACCOUNTS[code], partner=partner, rows=rows, opening=opening, closing=run,
                                                         d1=d1, d2=d2, code=code, owes_us=owes_us))


@login_required
def activity(request):
    """The owner re-tells the program his activity: new categories, item fields and words (the plan does not change)."""
    from core import activities as A
    from core import modules as M
    from pos.branches import is_owner_level

    if not (request.user.is_superuser or is_owner_level(request.user)):
        raise PermissionDenied
    s = CompanySettings.get()
    if request.method == "POST":
        text = (request.POST.get("activity") or "").strip()
        if len(text) < 2:
            messages.error(request, "اكتب نشاطك")
            return redirect("shop_activity")
        p = A.lookup(request, text)
        p["base"] = s.business  # the hall / recipes / warehouses stay what was bought
        s.activity = p
        s.save(update_fields=["activity"])
        n = A.add_categories(p["categories"])
        messages.success(request, f"ضُبط البرنامج على «{p['name']}» ✔" + (f" — تمت الإضافة {n} تصنيف" if n else ""))
        if p["suggest"]:
            missing = [M.MODULES[m][0] for m in p["suggest"] if m not in M.enabled(s)]
            if missing:
                messages.info(request, "مفيد لنشاطك وغير مفتوح لديك: " + "، ".join(missing) + " — تواصل مع Trio")
        return redirect("shop_settings")
    act = A.current(s)
    return render(request, "ui/shop_activity.html", dict(act=A.view(act), act_value=act.get("text") or act.get("name"), act_library=[p["name"] for p in A.LIBRARY]))
