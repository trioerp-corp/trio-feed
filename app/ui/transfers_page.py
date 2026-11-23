"""1.25 «التحويلات بين الفروع»: ask another branch for goods, approve and ship, receive (all / part / with a difference), print the
transfer note. The rules live in pos/transfers.py."""
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render

from core.models import CompanySettings
from inventory.models import Item, StockQuant, Transfer
from pos import transfers as TR
from pos.branches import home_branch
from pos.models import Branch


def _num(v):
    v = str(v or "").strip().replace("٫", ".").replace(",", ".").translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    if not v:
        return None
    try:
        d = Decimal(v)
    except InvalidOperation:
        raise ValidationError(f"«{v}» ليس رقمًا")
    return d if d.is_finite() else None


def _guard(request):
    from core.modules import enabled

    if "branches" not in enabled() or Branch.objects.filter(active=True).count() < 2:
        raise PermissionDenied("التحويلات بين الفروع تحتاج فرعين على الأقل — افتح فرعًا من «الفروع»")
    if not TR._stock_ok(request.user):
        raise PermissionDenied("ليست لديك صلاحية المخزن")


def _errs(request, e):
    messages.error(request, "؛ ".join(getattr(e, "messages", None) or [str(e)]))


def _rows(P):
    return [(i, _num(q)) for i, q in zip(P.getlist("item"), P.getlist("qty")) if i and q]


@login_required
def page(request):
    _guard(request)
    mine = home_branch(request.user)
    branches = list(Branch.objects.filter(active=True).order_by("id"))
    if request.method == "POST":
        P = request.POST
        try:
            src = Branch.objects.filter(pk=P.get("from") or 0, active=True).first()
            dst = Branch.objects.filter(pk=P.get("to") or 0, active=True).first()
            if P.get("op") == "ship":
                t = TR.ship(request.user, src, dst, _rows(P), P.get("note", ""), P.get("driver", ""))
                messages.success(request, f"أُرسل التحويل {t.number} إلى «{dst.name}» — البضاعة الآن «في الطريق» حتى يستلمها الفرع")
            else:
                t = TR.request(request.user, src, dst, _rows(P), P.get("note", ""))
                messages.success(request, f"أُرسل الطلب {t.number} إلى «{src.name}» — ينتظر موافقتهم")
            return redirect("transfer", pk=t.pk)
        except ValidationError as e:
            _errs(request, e)
        return redirect("transfers")
    qs = TR.visible(request.user, Transfer.objects.select_related("from_warehouse", "to_warehouse", "requested_by").prefetch_related("lines"))
    st = request.GET.get("s", "")
    if st == "open":
        qs = qs.filter(state__in=["requested", "sent", "partial"])
    elif st in dict(Transfer.STATES):
        qs = qs.filter(state=st)
    names = {b.warehouse_id: b.name for b in Branch.objects.all()}
    rows = []
    for t in qs[:200]:
        t.src, t.dst = names.get(t.from_warehouse_id, t.from_warehouse.name), names.get(t.to_warehouse_id, t.to_warehouse.name)
        t.todo = (TR.can_send(request.user, t) and "موافقة وإرسال") or (TR.can_receive(request.user, t) and "استلام") or ""
        t.val = TR.value(t)
        rows.append(t)
    have = defaultdict(dict)
    for q in StockQuant.objects.filter(warehouse__in=[b.warehouse_id for b in branches]).exclude(qty=0):
        have[q.item_id][q.warehouse_id] = q.qty
    items = [(i, {str(w): str(q) for w, q in have.get(i.pk, {}).items()})
             for i in Item.objects.filter(active=True, item_type="stock").exclude(code="0").exclude(variant_of__isnull=True, extra__has_key="grid").order_by("name")]
    return render(request, "ui/transfers.html", dict(rows=rows, branches=branches, mine=mine, items=items, s=st, transit=TR.in_transit_value(),
                                                     whs={str(b.pk): str(b.warehouse_id) for b in branches}, waiting=TR.waiting(request.user)))


@login_required
def detail(request, pk):
    _guard(request)
    t = get_object_or_404(TR.visible(request.user, Transfer.objects.all()), pk=pk)
    lines = list(t.lines.select_related("item").order_by("id"))
    if request.method == "POST":
        P = request.POST
        try:
            qtys = {ln.pk: (_num(P.get(f"q{ln.pk}")) or Decimal(0)) for ln in lines if f"q{ln.pk}" in P}
            op = P.get("op")
            if op == "send":
                TR.send(request.user, t, qtys or None, P.get("driver", ""))
                messages.success(request, "تمت الموافقة وأُرسلت البضاعة — خرجت من مخزنكم وهي الآن «في الطريق»")
            elif op == "reject":
                TR.reject(request.user, t, P.get("reason", ""))
                messages.success(request, "رُفض الطلب وأُبلغ الفرع الطالب")
            elif op == "receive":
                TR.receive(request.user, t, qtys or None, close=P.get("close") != "0", diff=P.get("diff") or "loss", reply=P.get("reply", ""))
                t.refresh_from_db()
                messages.success(request, "تم الاستلام ✔ — دخلت البضاعة مخزن فرعكم" if t.state == "received" else
                                 "سُجّل المستلم — الباقي ما زال «في الطريق» ويمكن استلامه لاحقًا")
            elif op == "cancel":
                TR.cancel(request.user, t, P.get("reason", ""))
                messages.success(request, "أُلغي التحويل")
        except ValidationError as e:
            _errs(request, e)
        return redirect("transfer", pk=t.pk)
    src, dst = TR.branch_of(t.from_warehouse), TR.branch_of(t.to_warehouse)
    here = dict(StockQuant.objects.filter(warehouse=t.from_warehouse, item__in=[ln.item_id for ln in lines]).values_list("item_id", "qty"))
    for ln in lines:
        ln.src_has = here.get(ln.item_id, Decimal(0))
    return render(request, "ui/transfer.html", dict(t=t, lines=lines, src=src, dst=dst, can_send=TR.can_send(request.user, t),
                                                    can_receive=TR.can_receive(request.user, t), can_cancel=TR.can_cancel(request.user, t),
                                                    val=TR.value(t), moves=t.moves.order_by("id")))


@login_required
def note(request, pk):
    """The printable transfer note (إذن تحويل): goes with the driver, signed by the sender, the driver and the receiver."""
    _guard(request)
    t = get_object_or_404(TR.visible(request.user, Transfer.objects.all()), pk=pk)
    return render(request, "ui/transfer_print.html", dict(t=t, lines=t.lines.select_related("item").order_by("id"), src=TR.branch_of(t.from_warehouse),
                                                          dst=TR.branch_of(t.to_warehouse), s=CompanySettings.get(), val=TR.value(t)))
