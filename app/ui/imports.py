"""Start a shop fast: every section can be filled from one Excel sheet.

Each kind gives a ready, formatted sheet (Arabic headers, drop-down lists, an instructions sheet with an example, and the data
already in the program so it can be edited in bulk), reads it back, shows a preview with every mistake by row number, and only
then saves everything in one go (all or nothing). Uploading the same file again updates instead of duplicating.
"""
import datetime as dt
import io
import re
from decimal import Decimal, InvalidOperation

import openpyxl
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.datavalidation import DataValidation

from inventory.models import Item, ItemCategory, ItemUnit, Recipe, RecipeLine, StockMove, StockMoveLine, Warehouse
from ledger import services as L
from ledger.models import Account, JournalLine, Partner

Z = Decimal(0)
OPENING = "1207010101"  # opening balances / capital (not profit)


# ------------------------------------------------------------------ helpers
def norm(s):
    s = re.sub(r"[إأآ]", "ا", str(s or "")).replace("ة", "ه").replace("ى", "ي").replace("ـ", "").replace("*", "")
    return re.sub(r"\s+", " ", s).strip().lower()


def num(v, field=""):
    if v in (None, ""):
        return None
    if isinstance(v, (int, float, Decimal)):
        return Decimal(str(v))
    s = str(v).strip().replace(",", "").replace("٬", "").translate(str.maketrans("٠١٢٣٤٥٦٧٨٩٫", "0123456789."))
    if s == "":
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        raise ValueError(f"«{v}» ليس رقمًا{(' في ' + field) if field else ''}")


def date_of(v):
    if v in (None, ""):
        return None
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    s = str(v).strip().translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    for f in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%d/%m/%y"):
        try:
            return dt.datetime.strptime(s, f).date()
        except ValueError:
            pass
    raise ValueError(f"«{v}» ليس تاريخًا (اكتبه 31/12/2026)")


def text(v):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    s = str(v).strip()
    return s[:-2] if re.fullmatch(r"\d+\.0", s) else s


def find_item(code, name):
    it = None
    if code:
        it = Item.objects.filter(code=code).first() or Item.objects.filter(barcode=code).first()
    if not it and name:
        it = Item.objects.filter(name=name).first()
        if not it:
            n = norm(name)
            it = next((i for i in Item.objects.only("id", "name") if norm(i.name) == n), None)
    return it


def category(name, create):
    c = ItemCategory.objects.filter(name=name).first()
    if c or not create:
        return c
    model = ItemCategory.objects.exclude(name__startswith="مستلزمات").filter(stock_account__code__startswith="1202").first()
    stock = model.stock_account if model else Account.objects.get(code="1202010101")
    cogs = model.farm_account if model else Account.objects.get(code="3101010101")
    return ItemCategory.objects.create(name=name[:80], stock_account=stock, farm_account=cogs, admin_account=cogs, needs_equipment=False)


def next_code(start=1001):
    used = set(Item.objects.values_list("code", flat=True))
    n = start
    while str(n) in used:
        n += 1
    return n


def opening_entry(date, lines, ref):
    if not lines:
        return None
    total = sum((ln.get("debit", Z) - ln.get("credit", Z) for ln in lines), Z)
    if total:
        lines.append(dict(account=Account.objects.get(code=OPENING), debit=-total if total < 0 else Z, credit=total if total > 0 else Z,
                          description="رصيد أول المدة"))
    return L.create_entry("OB", date, ref, lines, legacy_ref="استيراد إكسل")


# ------------------------------------------------------------------ the kinds
# columns: (header, key, required, example, note, width, choices)
def _cats():
    return [c.name for c in ItemCategory.objects.order_by("name")]


def _whs():
    return [w.name for w in Warehouse.objects.filter(active=True)]


def _act_fields():
    try:
        from core.activities import current

        return current()["fields"]
    except Exception:  # noqa: BLE001
        return []


def _spec(kind):
    """The sheet of a kind — the items sheet also gets the activity's own columns (part number, size…)."""
    spec = KINDS[kind]
    if kind == "items":
        spec = dict(spec, cols=list(spec["cols"]) + [(f["n"], "x_" + f["k"], False, "", "حسب نشاطك", 18, None) for f in _act_fields()])
    return spec


def items_rows():
    def cost(i):
        c = i.std_cost if i.std_cost is not None else (i.avg_cost if i.qty_on_hand else None)
        return c.quantize(Decimal("0.01")) if c is not None else None

    xf = _act_fields()
    return [[i.code, i.name, i.category.name, i.unit, i.barcode, cost(i), i.sale_price, i.reorder_level] + ([None, None] + [(i.extra or {}).get(f["k"], "") for f in xf] if xf else [])
            for i in Item.objects.filter(active=True, item_type="stock").exclude(code="0").select_related("category").order_by("category__name", "name")]


def run_items(rows, commit, opts):
    errs, made, upd = [], 0, 0
    codes_in_file = {}
    for r in rows:
        try:
            name = text(r.get("name"))
            if not name:
                raise ValueError("اسم الصنف فارغ")
            catname = text(r.get("category")) or "أخرى"
            code = text(r.get("code"))
            if code and code in codes_in_file:
                raise ValueError(f"الكود {code} مكرر في الملف (سطر {codes_in_file[code]})")
            if code:
                codes_in_file[code] = r["rown"]
            cost, price, reorder, qty = num(r.get("cost"), "سعر الشراء"), num(r.get("price"), "سعر البيع"), num(r.get("reorder"), "حد الطلب"), num(r.get("qty"), "الكمية")
            if price is not None and price < 0 or cost is not None and cost < 0:
                raise ValueError("لا يمكن أن يكون السعر سالبًا")
            if qty and qty > 0 and cost is None:
                raise ValueError("كتبت كمية حالية — يلزم سعر الشراء لحساب قيمة البضاعة")
            exp = date_of(r.get("expiry"))
            bc = text(r.get("barcode"))
            if bc:
                other = Item.objects.filter(barcode=bc).exclude(code=code or "\0").first()
                if other and (not code or other.code != code) and norm(other.name) != norm(name):
                    raise ValueError(f"الباركود {bc} متسجل على «{other.name}»")
            it = Item.objects.filter(code=code).first() if code else find_item("", name)
            if reorder is None and (not it or (it.item_type == "stock" and it.reorder_level is None)):
                raise ValueError("اكتب «حد الطلب» للصنف (أقل كمية تريد إعادة الشراء عندها) — 0 إن لم ترد تنبيهًا")
            if reorder is not None and reorder < 0:
                raise ValueError("لا يمكن أن يكون حد الطلب سالبًا")
            if qty and it and StockMoveLine.objects.filter(item=it).exists():
                raise ValueError("لهذا الصنف رصيد وحركات — اترك «الكمية الحالية» فارغة وعدّل الرصيد من الجرد")
            if commit:
                cat = category(catname, True)
                if not it:
                    it = Item(code=code or str(next_code()), item_type="stock")
                    made += 1
                else:
                    upd += 1
                it.name, it.category, it.unit = name[:150], cat, text(r.get("unit")) or it.unit or "قطعة"
                if bc:
                    it.barcode = bc
                if price is not None:
                    it.sale_price = price
                if reorder is not None:
                    it.reorder_level = reorder
                if cost is not None:
                    it.std_cost = cost
                xs = {k[2:]: text(v)[:120] for k, v in r.items() if k.startswith("x_") and text(v)}
                if xs:
                    it.extra = {**(it.extra or {}), **xs}
                it.save()
                if qty and qty > 0:
                    opts.setdefault("stock", []).append((it, qty, cost, exp))
            else:
                if it:
                    upd += 1
                else:
                    made += 1
        except ValueError as e:
            errs.append((r["rown"], str(e)))
    if commit and opts.get("stock"):
        wh = Warehouse.objects.filter(name=opts.get("warehouse")).first() or Warehouse.objects.filter(active=True).first()
        mv = StockMove.objects.create(kind="opening", date=opts["date"], warehouse=wh, description="رصيد أول المدة — استيراد إكسل")
        for it, qty, cost, exp in opts["stock"]:
            StockMoveLine.objects.create(move=mv, item=it, qty=qty, unit_cost=cost, expiry=exp)
        from inventory import services as INV

        INV.post_move(mv, allow_negative=True)
    return errs, f"{made} صنف جديد و{upd} صنف مُحدَّث" + (f"، ورصيد أول المدة لـ {len(opts.get('stock', []))} صنف" if commit and opts.get("stock") else "")


def run_opening_stock(rows, commit, opts):
    errs, lines = [], []
    for r in rows:
        try:
            it = find_item(text(r.get("code")), text(r.get("name")))
            if not it:
                raise ValueError("الصنف غير موجود — أضفه أولًا من ملف الأصناف")
            qty, cost = num(r.get("qty"), "الكمية"), num(r.get("cost"), "سعر الشراء")
            if not qty or qty <= 0:
                raise ValueError("يجب أن تكون الكمية أكبر من صفر")
            if cost is None:
                cost = it.std_cost if it.std_cost is not None else (it.avg_cost if it.qty_on_hand else None)
            if cost is None:
                raise ValueError("اكتب سعر الشراء")
            lines.append((it, qty, cost, date_of(r.get("produced")), date_of(r.get("expiry"))))
        except ValueError as e:
            errs.append((r["rown"], str(e)))
    if commit and lines and not errs:
        wh = Warehouse.objects.filter(name=opts.get("warehouse")).first() or Warehouse.objects.filter(active=True).first()
        mv = StockMove.objects.create(kind="opening", date=opts["date"], warehouse=wh, description="رصيد أول المدة — استيراد إكسل")
        for it, qty, cost, prod, exp in lines:
            StockMoveLine.objects.create(move=mv, item=it, qty=qty, unit_cost=cost, produced=prod, expiry=exp)
        from inventory import services as INV

        INV.post_move(mv, allow_negative=True)
    value = sum((q * c for _, q, c, _, _ in lines), Z)
    return errs, f"{len(lines)} صنف بقيمة {value:,.2f}"


def _partners(kind, code, label, owes_us):
    def rows_now():
        return [[p.name, p.phone, p.credit_limit if kind == "customer" else None, None] for p in Partner.objects.filter(kind=kind, active=True).order_by("name")]

    def run(rows, commit, opts):
        errs, made, upd, lines, seen = [], 0, 0, [], {}
        acc = Account.objects.get(code=code)
        for r in rows:
            try:
                name = text(r.get("name"))
                if not name:
                    raise ValueError("الاسم فارغ")
                if norm(name) in seen:
                    raise ValueError(f"الاسم مكرر في الملف (سطر {seen[norm(name)]})")
                seen[norm(name)] = r["rown"]
                bal, limit = num(r.get("balance"), "الرصيد"), num(r.get("limit"), "حد المديونية")
                p = Partner.objects.filter(name=name, kind=kind).first()
                if bal and p and JournalLine.objects.filter(account=acc, partner=p).exists():
                    raise ValueError("له حركات في البرنامج — يُعدَّل الرصيد من السندات، اترك خانة الرصيد فارغة")
                if commit:
                    if not p:
                        p = Partner.objects.create(name=name[:200], kind=kind)
                        made += 1
                    else:
                        upd += 1
                    if text(r.get("phone")):
                        p.phone = text(r.get("phone"))[:40]
                    if kind == "customer" and limit is not None:
                        p.credit_limit = limit
                    p.save()
                    if bal:
                        d, c = (bal, Z) if bal > 0 else (Z, -bal)
                        if not owes_us:
                            d, c = c, d
                        lines.append(dict(account=acc, partner=p, debit=d, credit=c, description=f"رصيد أول المدة — {name}"))
                else:
                    made, upd = (made, upd + 1) if p else (made + 1, upd)
            except ValueError as e:
                errs.append((r["rown"], str(e)))
        if commit and lines:
            opening_entry(opts["date"], lines, f"أرصدة أول المدة — {label}")
        return errs, f"{made} {label} جديد و{upd} تم التحديث" + (f"، وأرصدة لـ {len(lines)}" if commit and lines else "")
    return rows_now, run


def run_units(rows, commit, opts):
    errs, n = [], 0
    for r in rows:
        try:
            it = find_item(text(r.get("code")), text(r.get("item")))
            if not it:
                raise ValueError("الصنف غير موجود")
            unit, factor = text(r.get("unit")), num(r.get("factor"), "فيها كام")
            if not unit or not factor or factor <= 0:
                raise ValueError("اكتب اسم الوحدة وكم تحتوي من الوحدة الأساسية")
            bc = text(r.get("barcode"))
            if commit:
                u = ItemUnit.objects.filter(item=it, name=unit).first() or ItemUnit(item=it, name=unit[:40])
                u.factor, u.barcode, u.sale_price = factor, bc[:40], num(r.get("price"), "سعر البيع")
                u.save()
            n += 1
        except ValueError as e:
            errs.append((r["rown"], str(e)))
    return errs, f"{n} وحدة"


def run_recipes(rows, commit, opts):
    """One row per ingredient; the drink / dish name repeats on its rows."""
    errs, recipes = [], {}
    for r in rows:
        try:
            dish = text(r.get("dish"))
            if not dish:
                raise ValueError("اسم المشروب / الأكلة فارغ")
            ing = find_item(text(r.get("ing_code")), text(r.get("ingredient")))
            if not ing:
                raise ValueError(f"الخامة «{text(r.get('ingredient'))}» غير موجودة — أضفها في ملف الأصناف (تصنيف خامات)")
            qty = num(r.get("qty"), "الكمية")
            if not qty or qty <= 0:
                raise ValueError("يجب أن تكون كمية الخامة أكبر من صفر")
            g = recipes.setdefault(dish, dict(cat=text(r.get("category")), margin=None, lines=[]))
            m = num(r.get("margin"), "الهامش")
            if m is not None:
                g["margin"] = m
            g["lines"].append((ing, qty, num(r.get("waste"), "الهالك") or Z))
        except ValueError as e:
            errs.append((r["rown"], str(e)))
    if commit and not errs:
        from inventory.recipes import costing

        for dish, g in recipes.items():
            it = Item.objects.filter(name=dish).first()
            if not it:
                it = Item.objects.create(code=str(next_code(3001)), name=dish[:150], category=category(g["cat"] or "مشروبات ساخنة", True), unit="واحدة",
                                         item_type="recipe")
            elif it.item_type != "recipe":
                it.item_type = "recipe"
                it.save(update_fields=["item_type"])
            rc, _ = Recipe.objects.get_or_create(item=it)
            if g["margin"] is not None:
                rc.target_margin = g["margin"]
                rc.save()
            rc.lines.all().delete()
            for ing, qty, waste in g["lines"]:
                RecipeLine.objects.create(recipe=rc, ingredient=ing, qty=qty, waste_pct=waste)
            if it.sale_price in (None, Z):
                it.sale_price = costing(rc)["suggested"]
                it.save(update_fields=["sale_price"])
    return errs, f"{len(recipes)} وصفة"



# ------------------------------------------------------------------ the menu (1.22): products with sizes + add-ons
MENU_SIZES = (("s1", "صغير"), ("s2", "وسط"), ("s3", "كبير"), ("s4", "عائلي"))
ADDON_WORDS = {norm(w) for w in ("إضافة", "اضافه", "إضافات", "addon")}


def _split_cats(v):
    return [c.strip() for c in re.split(r"[،,؛;]", text(v)) if c.strip()]


def menu_rows():
    from inventory import menu as MN

    rows, addons = MN.products()
    out = []
    for r in rows:
        it, by = r["item"], {v.size_name: v.sale_price for v in r["sizes"]}
        out.append([it.name, "منتج", it.category.name, None if r["sizes"] else it.sale_price] + [by.get(n) for _, n in MENU_SIZES] + [r["desc"]])
    for a in addons:
        out.append([a.name, "إضافة", "، ".join(c.name for c in a.addon_for.all()), a.sale_price, None, None, None, None, ""])
    return out


def run_menu(rows, commit, opts):
    """One row per product (its price, or a price under each size) or per add-on (النوع = إضافة).
    Uploading again updates the product with the same name; sizes named outside the four columns are kept."""
    from inventory import menu as MN

    errs, plan, seen = [], [], set()
    for r in rows:
        try:
            name = text(r.get("name"))[:120]
            if not name:
                raise ValueError("اسم الصنف فارغ")
            addon = norm(r.get("kind")) in ADDON_WORDS
            if (name, addon) in seen:
                raise ValueError(f"«{name}» مكرر في الملف")
            seen.add((name, addon))
            price = num(r.get("price"), "السعر")
            sizes = [(n, num(r.get(k), n)) for k, n in MENU_SIZES if num(r.get(k), n) is not None]
            for what, p in [("السعر", price)] + sizes:
                if p is not None and p <= 0:
                    raise ValueError(f"{what} يجب أن يكون أكبر من صفر")
            if addon:
                if sizes:
                    raise ValueError("الإضافة لها سعر واحد — اكتبه في عمود «السعر»")
                if price is None:
                    raise ValueError("اكتب سعر الإضافة")
                plan.append(dict(addon=True, name=name, price=price, cats=_split_cats(r.get("category")), rown=r["rown"]))
                continue
            cat = text(r.get("category"))
            if not cat:
                raise ValueError("اكتب التصنيف (سندوتشات، مشروبات…)")
            if len(sizes) == 1 and price is None:
                price, sizes = sizes[0][1], []
            if sizes and price is not None:
                raise ValueError("اكتب السعر أو أسعار الأحجام، وليس الاثنين")
            if not sizes and price is None:
                raise ValueError("اكتب السعر، أو سعر كل حجم")
            plan.append(dict(addon=False, name=name, cat=cat, price=price, sizes=sizes, desc=text(r.get("desc"))[:200], rown=r["rown"]))
        except ValueError as e:
            errs.append((r["rown"], str(e)))
    cats_known = {norm(c.name) for c in ItemCategory.objects.all()} | {norm(p["cat"]) for p in plan if not p["addon"]}
    for p in plan:
        if p["addon"]:
            bad = [c for c in p["cats"] if norm(c) not in cats_known]
            if bad:
                errs.append((p["rown"], f"التصنيف «{bad[0]}» غير موجود — اكتبه كما هو في عمود التصنيف للمنتجات"))
    np = sum(1 for p in plan if not p["addon"])
    summary = f"{np} صنف في المنيو و{len(plan) - np} إضافة"
    if not commit or errs:
        return errs, summary
    std = {n for _, n in MENU_SIZES}
    for p in [x for x in plan if not x["addon"]] + [x for x in plan if x["addon"]]:
        try:
            if p["addon"]:
                it = Item.objects.filter(name=p["name"], is_addon=True).first()
                ids = [c.pk for c in ItemCategory.objects.all() if norm(c.name) in {norm(x) for x in p["cats"]}]
                MN.save_addon(p["name"], p["price"], ids, item=it)
            else:
                it = Item.objects.filter(name=p["name"], variant_of__isnull=True, is_addon=False).order_by("-active", "pk").first()
                sizes = list(p["sizes"])
                if it and sizes:
                    sizes += [(v.size_name, v.sale_price) for v in MN.sizes_of(it) if v.size_name not in std]
                MN.save_product(p["name"], MN.category(new_name=p["cat"]), price=p["price"], sizes=sizes, desc=p["desc"], item=it)
        except ValidationError as e:
            errs.append((p["rown"], e.messages[0]))
            break
    return errs, summary

cust_now, run_customers = _partners("customer", "1203010101", "عميل", True)
sup_now, run_suppliers = _partners("supplier", "2301020101", "مورد", False)
emp_now, run_employees = _partners("employee", "1203030115", "موظف", True)

KINDS = {
    "items": dict(title="الأصناف والأسعار", icon="📦", back="shop_items", perm="inventory.add_item", run=run_items, rows=items_rows, opening=True,
                  desc="كل صنف في سطر. الكود اختياري — وإن تركته فارغًا يرمّزه البرنامج تلقائيًا. ويُنشأ التصنيف الجديد تلقائيًا. "
                       "إن كتبت «الكمية الحالية» تُسجَّل رصيد أول المدة. ويُنزَّل الملف وفيه أصنافك الحالية: عدّل الأسعار وارفعه مرة أخرى.",
                  cols=[("الكود", "code", False, "1001", "اختياري — فارغ = تلقائي", 11, None),
                        ("اسم الصنف", "name", True, "سكر 1 كيلو", "", 30, None),
                        ("التصنيف", "category", True, "بقالة وتموين", "من القائمة أو اكتب تصنيفًا جديدًا", 20, _cats),
                        ("الوحدة", "unit", False, "قطعة", "قطعة / كيلو / علبة…", 10, None),
                        ("الباركود", "barcode", False, "6221000000011", "امسحه بالقارئ في الخانة", 18, None),
                        ("سعر الشراء", "cost", False, "32", "تكلفة الوحدة", 12, None),
                        ("سعر البيع", "price", False, "38", "", 12, None),
                        ("حد الطلب", "reorder", True, "10", "ينبّهك عندما يصل الرصيد إليه — 0 = من غير تنبيه", 11, None),
                        ("الكمية الحالية", "qty", False, "50", "اختياري: رصيد أول المدة", 13, None),
                        ("تاريخ الانتهاء", "expiry", False, "31/12/2026", "اختياري", 14, None)]),
    "opening": dict(title="رصيد البضاعة أول المدة", icon="🏷", back="shop_count", perm="inventory.add_stockmove", run=run_opening_stock, opening=True,
                    desc="الكمية الموجودة في المحل يوم بدء التشغيل بسعر الشراء. الصنف بالكود أو بالاسم. ويمكنك كتابة تاريخ الإنتاج والانتهاء.",
                    cols=[("الكود", "code", False, "1001", "أو الباركود", 12, None), ("اسم الصنف", "name", False, "سكر 1 كيلو", "إن لم يوجد كود", 30, None),
                          ("الكمية", "qty", True, "80", "", 10, None), ("سعر الشراء", "cost", False, "32", "متاح = من ملف الأصناف", 12, None),
                          ("تاريخ الإنتاج", "produced", False, "01/10/2026", "اختياري", 14, None),
                          ("تاريخ الانتهاء", "expiry", False, "01/04/2027", "اختياري", 14, None)]),
    "customers": dict(title="العملاء الآجل", icon="👥", back="shop_partners", perm="ledger.add_partner", run=run_customers, rows=cust_now, opening=True,
                      desc="العميل الذي يشتري بالآجل. «الرصيد المستحق عليه» = المستحق عليه للمحل اليوم (يُسجَّل رصيد أول المدة).",
                      cols=[("الاسم", "name", True, "أم محمد", "", 26, None), ("التليفون", "phone", False, "01001234567", "", 16, None),
                            ("حد المديونية", "limit", False, "2000", "متاح = مفتوح", 13, None), ("الرصيد المستحق عليه", "balance", False, "350", "", 13, None)]),
    "suppliers": dict(title="الموردين", icon="🚚", back="shop_partners", perm="ledger.add_partner", run=run_suppliers, rows=sup_now, opening=True,
                      desc="«الرصيد المستحق له» = ما لم يدفعه المحل للمورد حتى اليوم.",
                      cols=[("الاسم", "name", True, "شركة النيل للتوريدات", "", 28, None), ("التليفون", "phone", False, "0223456789", "", 16, None),
                            ("-", "limit", False, "", "", 4, None), ("الرصيد المستحق له", "balance", False, "5000", "", 13, None)]),
    "employees": dict(title="الموظفين", icon="🧑‍💼", back="shop_money", perm="ledger.add_partner", run=run_employees, rows=emp_now, opening=True,
                      desc="الموظفون الذين تُصرف لهم أجور وسلف. «سلفة عليه» = السلف التي لم تُخصم بعد.",
                      cols=[("الاسم", "name", True, "محمود السيد", "", 26, None), ("التليفون", "phone", False, "01112223334", "", 16, None),
                            ("-", "limit", False, "", "", 4, None), ("سلفة عليه", "balance", False, "500", "", 13, None)]),
    "units": dict(title="الوحدات (كرتونة / علبة)", icon="📦", back="shop_units", perm="inventory.add_item", run=run_units, module="units",
                  desc="الوحدة الأكبر للصنف: كرتونة بها 12 مثلًا، بباركودها وسعرها.",
                  cols=[("كود الصنف", "code", False, "1008", "أو الباركود", 12, None), ("اسم الصنف", "item", False, "مياه 1.5 لتر", "إن لم يوجد كود", 26, None),
                        ("الوحدة", "unit", True, "كرتونة", "", 12, None), ("كم تحتوي", "factor", True, "6", "من الوحدة الأساسية", 11, None),
                        ("باركود الوحدة", "barcode", False, "6221000000080", "", 18, None), ("سعر بيع الوحدة", "price", False, "45", "", 13, None)]),
    "menu": dict(title="المنيو (الأصناف والأحجام والإضافات)", icon="🍽", back="shop_menu", perm="inventory.change_item", run=run_menu, rows=menu_rows,
                 module="recipes",
                 desc="كل صنف في سطر: اكتب «السعر»، أو سعر كل حجم في أعمدة صغير / وسط / كبير / عائلي (ما تحتاجه منها فقط) فيبقى صنفًا واحدًا "
                      "ويسأل الكاشير عن الحجم. الإضافة: اكتب «إضافة» في عمود النوع وسعرها، وفي التصنيف ما تظهر معه (فارغ = كل الأصناف). "
                      "المكونات اختيارية وتظهر للزبون في منيو QR. ويُنزَّل الملف وفيه منيوك الحالي: عدّل وارفعه مرة أخرى.",
                 cols=[("اسم الصنف", "name", True, "شاورما فراخ", "", 24, None),
                       ("النوع", "kind", False, "منتج", "منتج / إضافة — فارغ = منتج", 10, lambda: ["منتج", "إضافة"]),
                       ("التصنيف", "category", False, "سندوتشات", "للإضافة: التصنيفات التي تظهر معها مفصولة بـ «،»", 18, _cats),
                       ("السعر", "price", False, "", "للصنف بلا أحجام، أو للإضافة", 10, None),
                       ("صغير", "s1", False, "55", "", 9, None), ("وسط", "s2", False, "", "", 9, None),
                       ("كبير", "s3", False, "85", "", 9, None), ("عائلي", "s4", False, "", "", 9, None),
                       ("المكونات / الوصف", "desc", False, "فراخ، ثومية، مخلل، بطاطس", "اختياري — يراها الزبون في منيو QR", 34, None)]),
    "recipes": dict(title="الوصفات (المشروبات والأكل)", icon="☕", back="shop_recipes", perm="inventory.add_item", run=run_recipes, module="recipes",
                    desc="كل خامة في سطر، ويتكرر اسم المشروب على سطور خاماته. يجب أن تكون الخامات مسجّلة كأصناف (بسعر شرائها). ويُحسب السعر المقترح تلقائيًا.",
                    cols=[("المشروب / الأكلة", "dish", True, "قهوة تركي", "", 22, None), ("التصنيف", "category", False, "مشروبات ساخنة", "", 18, _cats),
                          ("كود الخامة", "ing_code", False, "2001", "أو اسمها", 12, None), ("الخامة", "ingredient", True, "بن", "", 18, None),
                          ("الكمية", "qty", True, "18", "بوحدة الخامة (جرام…)", 10, None), ("هالك %", "waste", False, "0", "", 9, None),
                          ("هامش الربح %", "margin", False, "65", "مرة واحدة لكل مشروب", 13, None)]),
}


def visible(request):
    from core.modules import enabled

    mods = enabled()
    return {k: v for k, v in KINDS.items() if (not v.get("module") or v["module"] in mods) and request.user.has_perm(v["perm"])}


# ------------------------------------------------------------------ the sheet
TEAL, GOLD = "0F5257", "F2A541"


def workbook(kind):
    spec = _spec(kind)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "البيانات"
    ws.sheet_view.rightToLeft = True
    thin = Side(style="thin", color="C9D9D7")
    ws.append([h + (" *" if req else "") for h, _, req, *_ in spec["cols"]])
    for i, (h, key, req, ex, note, width, choices) in enumerate(spec["cols"], 1):
        c = ws.cell(row=1, column=i)
        c.font = Font(bold=True, color="FFFFFF", size=12)
        c.fill = PatternFill("solid", fgColor=GOLD if req else TEAL)
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = Border(top=thin, bottom=thin, left=thin, right=thin)
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = width + 4
        if choices:
            vals = choices()
            if vals:
                lst = wb.create_sheet("قوائم") if "قوائم" not in wb.sheetnames else wb["قوائم"]
                col = lst.max_column + 1 if lst.max_row > 1 or lst["A1"].value else 1
                for k, v in enumerate(vals, 1):
                    lst.cell(row=k, column=col, value=v)
                L_ = openpyxl.utils.get_column_letter(col)
                dv = DataValidation(type="list", formula1=f"='قوائم'!${L_}$1:${L_}${len(vals)}", allow_blank=True, showErrorMessage=False)
                ws.add_data_validation(dv)
                dv.add(f"{openpyxl.utils.get_column_letter(i)}2:{openpyxl.utils.get_column_letter(i)}5000")
    ws.row_dimensions[1].height = 34
    ws.freeze_panes = "A2"
    if spec.get("rows"):
        for r in spec["rows"]():
            ws.append([float(v) if isinstance(v, Decimal) else v for v in r])
    for row in ws.iter_rows(min_row=2, max_row=max(ws.max_row, 2)):
        for c in row:
            c.alignment = Alignment(horizontal="right")
    if "قوائم" in wb.sheetnames:
        wb["قوائم"].sheet_state = "hidden"
    # instructions + example
    ins = wb.create_sheet("طريقة الملء", 0)
    ins.sheet_view.rightToLeft = True
    ins.column_dimensions["A"].width = 24
    ins.column_dimensions["B"].width = 22
    ins.column_dimensions["C"].width = 60
    ins["A1"] = f"Trio — شيت {spec['title']}"
    ins["A1"].font = Font(bold=True, size=16, color=TEAL)
    ins["A2"] = spec["desc"]
    ins["A2"].alignment = Alignment(wrap_text=True, vertical="top")
    ins.merge_cells("A2:C2")
    ins.row_dimensions[2].height = 60
    ins["A4"], ins["B4"], ins["C4"] = "العمود", "مثال", "ملاحظة"
    for c in ("A4", "B4", "C4"):
        ins[c].font = Font(bold=True, color="FFFFFF")
        ins[c].fill = PatternFill("solid", fgColor=TEAL)
    for k, (h, key, req, ex, note, width, choices) in enumerate(spec["cols"], 5):
        if h == "-":
            continue
        ins.cell(row=k, column=1, value=h + (" (إجباري)" if req else ""))
        ins.cell(row=k, column=2, value=ex)
        ins.cell(row=k, column=3, value=note)
        if req:
            ins.cell(row=k, column=1).font = Font(bold=True, color="9A5B07")
    last = 6 + len(spec["cols"])
    ins.cell(row=last, column=1, value="خطوات:").font = Font(bold=True)
    for k, t in enumerate(["1) املأ ورقة «البيانات» — كل سطر عنصر واحد (العناوين ملوّنة: الذهبي إجباري).",
                           "2) احفظ الملف وارفعه من الشاشة نفسها في البرنامج.",
                           "3) يعرض لك البرنامج مراجعة وأي خطأ برقم السطر — صحّحه وارفعه مرة أخرى، أو اضغط «حفظ الكل».",
                           "رفع الملف نفسه مرة أخرى يحدّث البيانات ولا يكررها."], last + 1):
        ins.cell(row=k, column=1, value=t)
        ins.merge_cells(start_row=k, start_column=1, end_row=k, end_column=3)
    wb.active = 1
    return wb


def read(f, kind):
    spec = _spec(kind)
    name = f.name.lower()
    if not name.endswith((".xlsx", ".xlsm")):
        raise ValidationError("ارفع ملف إكسل (xlsx) — الملف نفسه الذي نزّلته من البرنامج")
    wb = openpyxl.load_workbook(io.BytesIO(f.read()), data_only=True)
    ws = wb["البيانات"] if "البيانات" in wb.sheetnames else wb.worksheets[-1]
    want = {norm(h): key for h, key, *_ in spec["cols"] if h != "-"}
    header_row, idx = None, {}
    for r in range(1, min(ws.max_row, 15) + 1):
        found = {c: want[norm(ws.cell(row=r, column=c).value)] for c in range(1, ws.max_column + 1) if norm(ws.cell(row=r, column=c).value) in want}
        if len(found) >= max(1, sum(1 for _, _, req, *_ in spec["cols"] if req)):
            header_row, idx = r, found
            break
    if not header_row:
        raise ValidationError("لم أجد عناوين الأعمدة — استخدم الملف الذي يُنزَّل من البرنامج (ورقة «البيانات»)")
    rows = []
    for r in range(header_row + 1, ws.max_row + 1):
        d = {key: ws.cell(row=r, column=c).value for c, key in idx.items()}
        if all(v in (None, "") for v in d.values()):
            continue
        d["rown"] = r
        rows.append(d)
    if not rows:
        raise ValidationError("الملف فارغ — املأه وارفعه مرة أخرى")
    return rows


# ------------------------------------------------------------------ views
@login_required
def hub(request):
    kinds = visible(request)
    if not kinds:
        raise PermissionDenied
    counts = dict(items=Item.objects.filter(item_type="stock").exclude(code="0").count(), customers=Partner.objects.filter(kind="customer").count(),
                  suppliers=Partner.objects.filter(kind="supplier").count(), employees=Partner.objects.filter(kind="employee").count(),
                  units=ItemUnit.objects.count(), recipes=Recipe.objects.count(),
                  menu=Item.objects.filter(active=True, variant_of__isnull=True, is_addon=False, item_type="recipe").count(),
                  opening=StockMove.objects.filter(kind="opening", state="posted").count())
    return render(request, "ui/import_hub.html", dict(kinds=[(k, v, counts.get(k, 0)) for k, v in kinds.items()]))


@login_required
def sheet(request, kind):
    if kind not in visible(request):
        raise PermissionDenied
    buf = io.BytesIO()
    workbook(kind).save(buf)
    resp = HttpResponse(buf.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    from urllib.parse import quote

    fn = f"Trio - {KINDS[kind]['title']}.xlsx"
    resp["Content-Disposition"] = f"attachment; filename=\"acc-{kind}.xlsx\"; filename*=UTF-8''{quote(fn)}"
    return resp


@login_required
def upload(request, kind):
    kinds = visible(request)
    if kind not in kinds:
        raise PermissionDenied
    spec = _spec(kind)
    ctx = dict(kind=kind, spec=spec, whs=_whs(), date=timezone.localdate().isoformat(), cols=[c for c in spec["cols"] if c[0] != "-"])
    key = f"import_{kind}"
    if request.method == "POST" and request.FILES.get("file"):
        try:
            rows = read(request.FILES["file"], kind)
        except ValidationError as e:
            messages.error(request, e.messages[0])
            return redirect("import_upload", kind=kind)
        except Exception as e:  # noqa: BLE001 — a broken file
            messages.error(request, f"الملف غير مقروء ({type(e).__name__}) — نزّل الملف من البرنامج واملأه")
            return redirect("import_upload", kind=kind)
        opts = dict(date=dt.date.fromisoformat(request.POST.get("date") or ctx["date"]), warehouse=request.POST.get("warehouse", ""))
        errs, summary = spec["run"](rows, False, opts)
        clean = [{k: (v.isoformat() if isinstance(v, (dt.date, dt.datetime)) else (str(v) if isinstance(v, Decimal) else v)) for k, v in r.items()} for r in rows]
        request.session[key] = dict(rows=clean, date=opts["date"].isoformat(), warehouse=opts["warehouse"])
        bad = {r for r, _ in errs}
        ctx.update(preview=True, rows=rows[:300], total=len(rows), errs=errs, bad=bad, summary=summary,
                   keys=[c[1] for c in ctx["cols"]], ok=len(rows) - len(bad))
        return render(request, "ui/import_upload.html", ctx)
    if request.method == "POST" and request.POST.get("confirm"):
        data = request.session.pop(key, None)
        if not data:
            messages.error(request, "انتهت الجلسة — ارفع الملف مرة أخرى")
            return redirect("import_upload", kind=kind)
        rows = data["rows"]
        opts = dict(date=dt.date.fromisoformat(data["date"]), warehouse=data["warehouse"])
        try:
            with transaction.atomic():
                errs, summary = spec["run"](rows, True, opts)
                if errs:
                    raise ValidationError(f"سطر {errs[0][0]}: {errs[0][1]}")
        except ValidationError as e:
            messages.error(request, "لم يُحفظ شيء — " + e.messages[0])
            return redirect("import_upload", kind=kind)
        messages.success(request, f"تم الحفظ ✔ — {summary}")
        return redirect(spec["back"])
    return render(request, "ui/import_upload.html", ctx)
