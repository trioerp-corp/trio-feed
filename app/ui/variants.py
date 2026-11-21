"""«المقاسات والألوان» (1.25): one form for a product with sizes × colours (inventory/variants.py), its stock matrix and what
sold by size. Labels for a product's variants go to the labels page (?parent=)."""
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import redirect, render

from inventory import menu as MN
from inventory import variants as V
from inventory.models import Item, ItemCategory


def qn(d):
    d = Decimal(d or 0)
    return f"{d.normalize():f}" if d == d.to_integral() else f"{d.quantize(Decimal('0.001')).normalize():f}"


def _cats():
    return ItemCategory.objects.exclude(name__startswith="خامات").exclude(name__startswith="مستلزمات").exclude(name="إضافات").order_by("name")


def _form_cells(P, sizes, colors):
    cells = {}
    for s in sizes or [""]:
        for c in colors or [""]:
            k = f"{s}|{c}"
            cells[(s, c)] = {"p": P.get("p|" + k, ""), "b": P.get("b|" + k, ""), "q": P.get("q|" + k, "")}
    return cells


@login_required
def page(request):
    if not request.user.has_perm("inventory.view_item"):
        raise PermissionDenied
    if not V.enabled() and not Item.objects.filter(variant_of__isnull=True, extra__has_key="grid").exists():
        raise Http404("المقاسات والألوان لا تخص نشاط هذا المحل")  # 1.24.1: a pharmacy / supermarket never reaches it by URL
    P = request.POST
    if request.method == "POST":
        if not request.user.has_perm("inventory.change_item"):
            raise PermissionDenied
        it = Item.objects.filter(pk=P.get("item") or 0).first()
        try:
            cat = MN.category(P.get("category"), P.get("new_category"))
            sizes, colors = V.clean_list(P.getlist("size"), "المقاسات"), V.clean_list(P.getlist("color"), "الألوان")
            it, made = V.save(P.get("name"), cat, P.get("price"), P.get("cost"), sizes, colors, _form_cells(P, sizes, colors),
                              reorder=P.get("reorder"), item=it, unit=P.get("unit") or "قطعة", user=request.user)
            messages.success(request, f"تم حفظ «{it.name}»" + (f" — أُضيف {made} مقاس/لون بباركود لكل منها" if made else ""))
            return redirect(f"{request.path}?saved={it.pk}")
        except ValidationError as e:
            messages.error(request, e.messages[0])
            return render(request, "ui/variants.html", _ctx(request, it, posted=P))
    return render(request, "ui/variants.html", _ctx(request, Item.objects.filter(pk=request.GET.get("edit") or 0).first()))


def _ctx(request, edit, posted=None):
    if edit is not None and not V.is_parent(edit):
        edit = None
    cat, q = request.GET.get("cat") or "", request.GET.get("q", "").strip()
    rows = []
    for p in V.products(cat or None, q):
        vs = V.variants(p)
        prices = sorted({v.sale_price for v in vs if v.sale_price is not None})
        rows.append(dict(item=p, n=len(vs), qty=qn(sum((v.qty_on_hand for v in vs), Decimal(0))),
                         price=" – ".join(f"{x:,.2f}" for x in (prices[:1] + prices[-1:] if len(prices) > 1 else prices))))
    init = dict(name="", category="", price="", cost="", reorder="1", unit="قطعة", sizes=[], colors=[], cells={})
    if edit:
        vs = V.variants(edit)
        g = edit.extra["grid"]
        init.update(name=edit.name, category=edit.category_id, unit=edit.unit, sizes=g.get("sizes", []), colors=g.get("colors", []),
                    cost="" if edit.std_cost is None else qn(edit.std_cost), reorder=qn(vs[0].reorder_level) if vs and vs[0].reorder_level is not None else "0")
        prices = [v.sale_price for v in vs]
        init["price"] = f"{max(set(prices), key=prices.count):.2f}" if prices else ""
        init["cells"] = {f"{(v.extra or {}).get('size', '')}|{(v.extra or {}).get('color', '')}":
                         dict(p=f"{v.sale_price:.2f}", b=v.barcode, stock=qn(v.qty_on_hand), old=True) for v in vs}
    if posted is not None:
        init.update(name=posted.get("name", ""), category=posted.get("category", ""), price=posted.get("price", ""), cost=posted.get("cost", ""),
                    reorder=posted.get("reorder", ""), unit=posted.get("unit", "قطعة"), sizes=posted.getlist("size"), colors=posted.getlist("color"))
        for k, v in posted.items():
            if k[:2] in ("p|", "b|", "q|"):
                init["cells"].setdefault(k[2:], {})[k[0]] = v
    from core.activities import current

    units = list(dict.fromkeys(["قطعة"] + list(current().get("units") or [])))
    return dict(edit=edit, rows=rows, cats=_cats(), cat=cat, q=q, init=init, sets=V.SIZE_SETS, colors=V.COLORS, units=units,
                saved=Item.objects.filter(pk=request.GET.get("saved") or 0).first())


@login_required
def report(request):
    if not request.user.has_perm("inventory.view_item"):
        raise PermissionDenied
    if not V.enabled() and not Item.objects.filter(variant_of__isnull=True, extra__has_key="grid").exists():
        raise Http404("المقاسات والألوان لا تخص نشاط هذا المحل")  # 1.24.1: a pharmacy / supermarket never reaches it by URL
    from ui.shop import _range

    cat = request.GET.get("cat") or ""
    d1, d2 = _range(request, 30)
    mats, tq, tv = [], Decimal(0), Decimal(0)
    for p in V.products(cat or None, request.GET.get("q", "").strip()):
        m = V.matrix(p)
        m["rows"] = [dict(r, cells=[(qn(v.qty_on_hand) if v else "", v is not None and v.qty_on_hand <= 0) for v in r["cells"]], total=qn(r["total"]))
                     for r in m["rows"]]
        tq, tv = tq + m["qty"], tv + m["value"]
        m["col_tot"], m["qty"] = [qn(x) for x in m["col_tot"]], qn(m["qty"])
        mats.append(m)
    sold = V.sold_by_size(d1, d2, cat or None)
    for k in ("sizes", "colors", "items"):
        for r in sold[k]:
            r["q"] = qn(r["q"])
    return render(request, "ui/variants_report.html", dict(mats=mats, cats=_cats(), cat=cat, d1=d1, d2=d2, sold=sold, tq=qn(tq), tv=tv,
                                                           q=request.GET.get("q", "")))
