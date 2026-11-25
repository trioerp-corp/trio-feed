"""One search box for everything (1.21): an item, a customer / supplier, a patient, an invoice number, or a page of the
program («مصروف», «جرد», «حد الطلب») — for people who do not know where things live. Only what the user may open."""
from core import currency as _CUR  # 1.26: the shop's currency (display only)
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import render

from core import menu as MENU
from core.modules import enabled


def _norm(t):
    return (t or "").strip().replace("أ", "ا").replace("إ", "ا").replace("آ", "ا").replace("ة", "ه").replace("ى", "ي")


# words people type for a page, besides its menu name
HINTS = {
    "/ui/shop/money/": "مصروف مصاريف ايجار كهربا مرتب سلفه مسحوبات ايداع",
    "/ui/shop/count/": "جرد عد البضاعه تسويه",
    "/ui/shop/damage/": "تالف هالك كسر منتهي",
    "/ui/shop/close/": "تقفيل قفل اليوم ورديه درج عجز زياده",
    "/ui/shop/items/": "صنف اصناف سعر باركود حد الطلب",
    "/ui/shop/purchases/": "شراء فاتوره مشتريات مورد",
    "/ui/shop/reports/": "تقرير ارباح مبيعات اليوم الشهر",
    "/ui/start/": "تجهيز البدايه ضريبه رقم ضريبي محافظ",
    "/ui/data/": "تعديل حذف اضافه بيانات تصنيف خزنه مخزن جهاز",
}


def _pages(request, q):
    out = []
    for title, links in request.menu_cache:
        for l in links:
            hay = _norm(l["label"] + " " + title + " " + HINTS.get(l["url"], ""))
            if all(w in hay for w in _norm(q).split()):
                out.append(dict(title=l["label"], sub=title, url=l["url"], icon=l["icon"] or "📄"))
    return out[:8]


@login_required
def find(request):
    from core.context import company
    from pos.voids import is_head

    q = request.GET.get("q", "").strip()[:80]
    groups = []
    if q:
        ctx = company(request)
        request.menu_cache = ctx.get("menu") or []
        pages = _pages(request, q)
        if pages:
            groups.append(("صفحات البرنامج", pages))
        u = request.user
        mods = enabled()
        if u.has_perm("inventory.view_item"):
            from inventory.models import Item

            words = Q()
            for w in q.split():  # 1.25: «قميص أزرق L» finds «قميص — L / أزرق» (every word, in the name or the size / colour)
                words &= Q(name__icontains=w) | Q(extra__icontains=w) | Q(aliases__icontains=w)
            items = Item.objects.filter(Q(name__icontains=q) | Q(code=q) | Q(barcode=q) | Q(aliases__icontains=q) | words, active=True).exclude(code="0")[:12]
            groups.append(("الأصناف", [dict(title=i.name, sub=f"كود {i.code} · الرصيد {i.qty_on_hand.normalize():f} {i.unit}"
                                          + (f" · {_CUR.fmt(i.sale_price)}" if i.sale_price is not None else ""),
                                          url=f"/ui/shop/items/?q={i.code}", icon="📦") for i in items]))
        if u.has_perm("treasury.view_voucher") or u.is_superuser:
            from ledger.models import Partner
            from pos.models import Order

            kinds = dict(customer="عميل", supplier="مورد", employee="موظف")
            ps = Partner.objects.filter(Q(name__icontains=q) | Q(phone__icontains=q), kind__in=kinds, merged_into__isnull=True)[:12]
            acc = dict(customer="1203010101", supplier="2301020101", employee="1203030115")
            groups.append(("العملاء والموردين والموظفين", [dict(title=p.name, sub=kinds[p.kind] + (f" · {p.phone}" if p.phone else ""),
                                                               url=f"/ui/shop/statement/?partner={p.pk}&account={acc[p.kind]}", icon="👤") for p in ps]))
            if q.replace("-", "").isalnum() and any(ch.isdigit() for ch in q):
                os_ = Order.objects.filter(Q(uid=q) | Q(uid__endswith="-" + q.zfill(7)) | Q(uid__endswith=q)).order_by("-id")[:8]
                groups.append(("الفواتير", [dict(title=f"فاتورة {o.uid}", sub=f"{o.created_at_device:%d/%m/%Y %H:%M} · {_CUR.fmt(o.total)}"
                                                 + (" · ملغية" if o.cancelled else ""), url=f"/ui/shop/sales/{o.pk}/", icon="🧾") for o in os_]))
        if "clinic" in mods:
            from django.apps import apps

            Patient = apps.get_model("clinic", "Patient")
            cond = Q(name__icontains=q) | Q(phone__icontains=q)
            if q.isdigit():
                cond |= Q(number=int(q))
            pts = Patient.objects.filter(cond)[:12]
            groups.append(("المرضى", [dict(title=p.name, sub=f"ملف {p.number}" + (f" · {p.phone}" if p.phone else ""),
                                           url=f"/ui/clinic/patients/{p.pk}/", icon="🩺") for p in pts]))
        groups = [(t, rows) for t, rows in groups if rows]
        if len(groups) == 1 and len(groups[0][1]) == 1 and request.GET.get("go"):
            from django.shortcuts import redirect

            return redirect(groups[0][1][0]["url"])
    return render(request, "ui/find.html", dict(q=q, groups=groups, owner=is_head(request.user)))
