"""«الترابيزات» (1.23): the tables of a café / restaurant, their QR codes, customer ordering from the QR, and the open bills."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import redirect, render

from core.models import CompanySettings
from pos import tables as T
from pos.models import DiningTable, TableTab


def _allowed(request):
    from pos.voids import is_owner

    s = CompanySettings.get()
    if s.business not in T.TABLE_BIZ:
        raise Http404("الترابيزات للكافيهات والمطاعم")
    if not (request.user.is_superuser or is_owner(request.user)):
        raise PermissionDenied
    return s


def _qr_svg(data):
    from ui.connect import _qr_svg

    return _qr_svg(data)


@login_required
def page(request):
    s = _allowed(request)
    P = request.POST
    who = request.user.get_full_name() or request.user.get_username()
    if request.method == "POST":
        op = P.get("op")
        try:
            if op == "cfg":
                T.set_cfg(tables=P.get("tables") == "1", qr_order=P.get("qr_order") == "1", qr_confirm=P.get("qr_confirm") == "1")
                messages.success(request, "تم الحفظ ✔" + (" — يصل الطلب من QR خلال دقائق بعد تحديث المنيو" if P.get("qr_order") == "1" else ""))
                _push()
            elif op == "add":
                prefix = (P.get("prefix") or "ترابيزة").strip()[:30]
                n = int(P.get("count") or 1)
                if not 1 <= n <= 100:
                    raise ValidationError("العدد من 1 إلى 100")
                start = DiningTable.objects.filter(name__startswith=prefix + " ").count() + 1
                last = DiningTable.objects.order_by("-order").values_list("order", flat=True).first() or 0
                for i in range(n):
                    DiningTable.objects.create(name=f"{prefix} {start + i}"[:40], area=(P.get("area") or "").strip()[:40],
                                               seats=int(P.get("seats")) if (P.get("seats") or "").isdigit() else None, order=last + i + 1)
                messages.success(request, f"أُضيفت {n} ترابيزة ✔")
                _push()
            elif op == "void":
                tab = TableTab.objects.get(pk=P.get("tab"))
                T.void(tab, who, P.get("reason", ""))
                messages.success(request, f"أُلغي حساب {tab.table.name}")
            elif op == "rm":
                tab = TableTab.objects.get(pk=P.get("tab"))
                T.remove(tab, P.get("line"), who, may_cancel_sent=True, qty=P.get("qty") or None)
                messages.success(request, "تم إلغاء الصنف من الحساب")
        except (ValidationError, ValueError, TableTab.DoesNotExist) as e:
            messages.error(request, e.messages[0] if hasattr(e, "messages") else "بيانات غير صحيحة")
        return redirect(request.path)
    from core import menu_pub

    tables = list(DiningTable.objects.all())
    tabs = list(TableTab.objects.filter(state="open").select_related("table"))
    for t in tabs:
        t.total = T.tab_total(t)
    return render(request, "ui/tables.html", dict(s=s, cfg=T.cfg(s), raw=s.menu_cfg or {}, tables=tables, tabs=tabs, missing=T.missing_sales(),
                                                  menu_link=menu_pub.link(s), kitchen=s.get_kitchen_ticket_display()))


@login_required
def qr_sheet(request):
    """A page of QR cards to print and put on the tables."""
    s = _allowed(request)
    from core import menu_pub

    if not menu_pub.link(s):
        messages.error(request, "البرنامج لم يتصل بخادم Trio بعد — تأكد من الإنترنت ثم حاول مرة أخرى")
        return redirect("shop_tables")
    ids = [int(x) for x in request.GET.getlist("t") if x.isdigit()]
    tables = DiningTable.objects.filter(active=True, **({"pk__in": ids} if ids else {}))
    cards = [dict(t=t, qr=_qr_svg(menu_pub.table_link(t, s))) for t in tables]
    return render(request, "ui/tables_qr.html", dict(s=s, cards=cards, order=T.cfg(s)["qr_order"]))


def _push():
    try:
        from core import menu_pub

        menu_pub.push(force=True)
    except Exception:  # noqa: BLE001 — offline: it goes with the next round
        pass
