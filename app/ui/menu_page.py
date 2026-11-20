"""«المنيو» (1.22): add a product with its price or its sizes and a description — no ingredients, stock or cost needed.
Ingredients and costing stay optional on the recipes page."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render

from inventory import menu as MN
from inventory.models import Item, ItemCategory, Recipe


def _cats():
    return ItemCategory.objects.exclude(name__startswith="خامات").exclude(name__startswith="مستلزمات").exclude(name="إضافات").order_by("name")


@login_required
def page(request):
    if not request.user.has_perm("inventory.change_item"):
        raise PermissionDenied
    P = request.POST
    if request.method == "POST":
        op = P.get("op")
        try:
            if op == "product":
                it = Item.objects.filter(pk=P.get("item") or 0).first()
                cat = MN.category(P.get("category"), P.get("new_category"))
                sizes = [(P.get(f"size_{i}"), P.get(f"sprice_{i}")) for i in range(MN.MAX_SIZES)] if P.get("has_sizes") == "1" else []
                it = MN.save_product(P.get("name"), cat, price=P.get("price"), sizes=sizes, desc=P.get("desc"), item=it)
                messages.success(request, f"تم حفظ «{it.name}» في المنيو ✔")
            elif op == "addon":
                it = Item.objects.filter(pk=P.get("item") or 0).first()
                it = MN.save_addon(P.get("name"), P.get("price"), P.getlist("cats"), item=it)
                messages.success(request, f"تم حفظ الإضافة «{it.name}» ✔")
            elif op == "stop":
                it = Item.objects.get(pk=P.get("item"))
                MN.stop(it)
                messages.success(request, f"أُزيل «{it.name}» من المنيو — مبيعاته السابقة محفوظة")
        except (ValidationError, Item.DoesNotExist) as e:
            messages.error(request, e.messages[0] if hasattr(e, "messages") else "الصنف غير موجود")
            return redirect(f"{request.path}?{request.GET.urlencode()}")
        return redirect(request.path)
    rows, addons = MN.products()
    rows = [r for r in rows if r["item"].code != "PLAY"]  # the hall's time charge is not a menu product
    recipes = dict(Recipe.objects.values_list("item_id", "pk"))
    edit = Item.objects.filter(pk=request.GET.get("edit") or 0, variant_of__isnull=True).first()
    edit_sizes = MN.sizes_of(edit) if edit else []
    slots = [dict(n=v.size_name, p=v.sale_price) for v in edit_sizes]
    slots += [dict(n=MN.SIZE_HINTS[i] if not edit_sizes and i < 3 else "", p="", hint=True) for i in range(len(slots), MN.MAX_SIZES)]
    groups = {}
    for r in rows:
        r["recipe"] = recipes.get(r["item"].pk)
        for v in r["sizes"]:
            v.recipe_id_ = recipes.get(v.pk)
        groups.setdefault(r["item"].category.name, []).append(r)
    return render(request, "ui/menu.html", dict(
        groups=groups, n=len(rows), addons=addons, cats=_cats(), edit=edit if edit and not edit.is_addon else None,
        edit_addon=edit if edit and edit.is_addon else None, slots=slots, edit_desc=(edit.extra or {}).get("desc", "") if edit else "",
        edit_cats=set(edit.addon_for.values_list("pk", flat=True)) if edit and edit.is_addon else set()))
