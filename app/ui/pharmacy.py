"""«💊 مكتبة الأدوية والشريط»: add drugs from the Egyptian drug library, and sell any box item by the strip."""
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.shortcuts import redirect, render

from inventory import drugs as D
from inventory import units as U
from inventory.models import Item


def _dec(x):
    x = str(x or "").replace("٫", ".").replace(",", ".").strip()
    if not x:
        return None
    try:
        return Decimal(x)
    except InvalidOperation:
        raise ValidationError(f"رقم غير صحيح: {x}")


@login_required
def page(request):
    if not request.user.has_perm("inventory.add_item"):
        raise PermissionDenied
    q, q2 = (request.GET.get("q") or "").strip(), (request.GET.get("q2") or "").strip()
    if request.method == "POST":
        a = request.POST.get("action")
        try:
            if a == "add":
                ids = [int(x) for x in request.POST.getlist("id") if x.isdigit()]
                if not ids:
                    raise ValidationError("علّم على الأدوية التي تريد إضافتها")
                from inventory.reorder import parse as reorder_of

                boxes = reorder_of(request.POST.get("reorder"))
                made, refused = 0, []
                for r in D.get(ids):
                    try:
                        _, new = D.add(r, box_price=_dec(request.POST.get(f"p_{r['id']}")), strips=request.POST.get(f"n_{r['id']}") or None,
                                       reorder_boxes=boxes)
                        made += new
                    except ValidationError as e:  # e.g. no price: the others are still added, this one is asked for
                        refused += e.messages
                if made:
                    messages.success(request, f"تمت الإضافة {made} دواء إلى الأصناف ✔ — يمكن للكاشير بيعها بالعلبة والشريط")
                for m in refused:
                    messages.error(request, m)
            elif a == "strip":
                it = Item.objects.get(pk=request.POST["item"])
                n = int(request.POST.get("n") or 0)
                if n <= 1:
                    U.sell_by_box(it)
                    messages.success(request, f"«{it.name}» أصبح بالعلبة فقط")
                else:
                    U.sell_by_strip(it, n, box_price=_dec(request.POST.get("box")), strip_price=_dec(request.POST.get("strip")))
                    messages.success(request, f"«{it.name}» يُباع بالشريط ✔ ({n} شريط في العلبة)")
        except (ValidationError, ValueError, Item.DoesNotExist) as e:
            for m in getattr(e, "messages", [str(e)]):
                messages.error(request, m)
        return redirect(f"{request.path}?q={q}&q2={q2}")
    results = [D.view(r) for r in D.search(q)] if q else []
    items = Item.objects.filter(active=True, item_type="stock").exclude(code="0").order_by("name")
    if q2:
        items = items.filter(Q(name__icontains=q2) | Q(aliases__icontains=q2) | Q(code=q2) | Q(barcode=q2) | Q(units__barcode=q2)).distinct()
    else:
        items = items.filter(category__name="أدوية")
    rows = []
    for it in items[:80]:
        n, box = U.strip_info(it)
        rows.append(dict(it=it, n=n, box=box, guess=D.guess_strips(it.name)))
    return render(request, "ui/pharmacy.html", dict(q=q, q2=q2, results=results, rows=rows, total=len(D.library())))
