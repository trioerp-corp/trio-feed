"""/ui/start/stock/ — «رصيد البضاعة الموجودة عندك الآن» (1.25, inventory/opening.py). Owner only."""
import json
import secrets

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils import timezone

from core.models import CompanySettings
from inventory import opening as OP
from inventory.models import Warehouse


def _owner(request):
    from pos.voids import is_owner

    if not (is_owner(request.user) or request.user.is_superuser):
        raise PermissionDenied("إدخال رصيد البضاعة لصاحب المحل فقط")


def _wh(request):
    ws = list(Warehouse.objects.filter(active=True).order_by("pk"))
    pick = str(request.POST.get("wh") or request.GET.get("wh") or "")
    return next((w for w in ws if str(w.pk) == pick), ws[0] if ws else None), ws


@login_required
def page(request):
    _owner(request)
    s = CompanySettings.get()
    wh, whs = _wh(request)
    if request.method == "POST":
        nonce = str(request.POST.get("nonce") or "")[:40]
        with transaction.atomic():
            s = CompanySettings.objects.select_for_update().get(pk=s.pk)
            ob = dict(s.onboarding or {})
            used = list(ob.get("stock_nonce") or [])
            if not nonce or nonce in used:  # the same page sent twice (double click, browser back): never books the stock twice
                messages.info(request, "سُجّل هذا الرصيد من قبل ✔")
                return redirect("start_stock")
            try:
                lines = OP.lines_from(json.loads(request.POST.get("rows") or "[]"))
                mv = OP.post(wh, lines, user=request.user)
            except (ValidationError, ValueError) as e:
                msgs = e.messages if isinstance(e, ValidationError) else ["بيانات غير صحيحة"]
                messages.error(request, "؛ ".join(msgs))
                return render(request, "ui/start_stock.html", _ctx(s, wh, whs, request.POST.get("rows") or "[]"))
            ob["stock"] = timezone.now().isoformat(timespec="seconds")
            ob["stock_nonce"] = (used + [nonce])[-30:]
            ob["skip"] = [k for k in ob.get("skip", []) if k != "stock"]
            s.onboarding = ob
            s.save(update_fields=["onboarding"])
        sm = OP.summary(mv)
        messages.success(request, f"تم تسجيل رصيد {sm['n']} صنف بقيمة {sm['v']:,.2f} ج.م ✔ (قيد رقم {mv.number})")
        return redirect("start_stock")
    return render(request, "ui/start_stock.html", _ctx(s, wh, whs))


def _ctx(s, wh, whs, typed="[]"):
    from core.activities import APPAREL, current
    from pos.plus import is_pharmacy

    first = OP.done()
    act = current(s).get("key")
    return dict(s=s, wh=wh, whs=whs, data=OP.rows(wh) if wh else [], typed=typed, nonce=secrets.token_hex(12),
                first=OP.summary(first) if first else None, pharmacy=is_pharmacy(s), apparel=act in APPAREL,
                expiry=is_pharmacy(s) or s.business == "market")
