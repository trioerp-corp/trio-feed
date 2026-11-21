import io
import datetime as dt
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from core.models import CompanySettings
from inventory import services as INV
from inventory.models import Item, StockMove, Warehouse
from ledger import services as L
from ledger.models import Account, DimensionValue, Journal, JournalEntry, Partner
from treasury import services as T
from treasury.models import CashBox, Voucher

from .forms import EntryForm, EntryLineFormSet, MoveForm, MoveLineFormSet, VoucherForm, VoucherLineFormSet


def _errs(request, e):
    for m in getattr(e, "messages", [str(e)]):
        messages.error(request, m)


# ------------------------------------------------------------------ home
@login_required
def home(request):
    from . import shop

    return shop.home(request)


# ------------------------------------------------------------------ lookup
@login_required
def lookup(request, kind):
    q = (request.GET.get("q") or "").strip()
    out = []
    from .shop import can_see_cash

    u = request.user
    # the search boxes live on the voucher / entry / stock-move forms: a cashier-only account gets nothing from them
    if not (can_see_cash(u) or u.has_perm("inventory.add_stockmove") or u.has_perm("ledger.add_journalentry")
            or u.has_perm("treasury.add_voucher")):
        return JsonResponse({"results": []})
    if kind == "account":
        qs = Account.objects.filter(is_postable=True, active=True)
        if q:
            qs = qs.filter(Q(code__startswith=q) | Q(name__icontains=q))
        for a in qs.order_by("code")[:25]:
            needs = [f"dim{n}" for n in (1, 2) if getattr(a, f"dim{n}_policy") == "required"] + (["partner"] if a.is_partner_control else [])
            out.append(dict(id=a.pk, label=f"{a.code} {a.name}", hint="", needs=needs))
    elif kind in ("cc", "equipment", "activity"):
        n = {"cc": 1, "equipment": 2, "activity": 3}[kind]
        qs = DimensionValue.objects.filter(dimension__number=n, is_postable=True, active=True).select_related("parent")
        if q:
            qs = qs.filter(Q(code__startswith=q) | Q(name__icontains=q) | Q(legacy_code__startswith=q) | Q(aliases__icontains=q))
        for v in qs.order_by("level", "code")[:25]:
            out.append(dict(id=v.pk, label=f"{v.code} {v.name}" if n == 1 else v.name, hint=v.parent.name if v.parent_id else ""))
    elif kind == "partner":
        qs = Partner.objects.filter(active=True, merged_into__isnull=True)
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(aliases__icontains=q))
        out = [dict(id=p.pk, label=p.name, hint=p.get_kind_display()) for p in qs.order_by("name")[:25]]
    elif kind == "item":
        qs = Item.objects.filter(active=True)
        if q:
            qs = qs.filter(Q(code__startswith=q) | Q(name__icontains=q) | Q(barcode=q) | Q(aliases__icontains=q))
        out = [dict(id=i.pk, label=f"{i.name} ({i.code})", hint=f"رصيد {i.qty_on_hand:g} {i.unit}") for i in qs.order_by("name")[:25]]
    elif kind == "accrual":
        from accruals.models import Accrual

        qs = Accrual.objects.filter(state="open").select_related("partner")
        if q:
            qs = qs.filter(Q(number__icontains=q) | Q(description__icontains=q) | Q(partner__name__icontains=q))
        out = [dict(id=a.pk, label=f"{a.number} — {a.description}", hint=f"باقي {a.remaining:,.2f}") for a in qs.order_by("date")[:25]]
    return JsonResponse({"results": out})


# ------------------------------------------------------------------ generic list helper
def _list(request, qs, filters, search_fields, template, extra=None, per_page=50):
    for key, field in filters.items():
        v = request.GET.get(key)
        if v:
            qs = qs.filter(**{field: v})
    d1, d2 = request.GET.get("from"), request.GET.get("to")
    if d1:
        qs = qs.filter(date__gte=d1)
    if d2:
        qs = qs.filter(date__lte=d2)
    q = (request.GET.get("q") or "").strip()
    if q:
        cond = Q()
        for f in search_fields:
            cond |= Q(**{f"{f}__icontains": q})
        qs = qs.filter(cond).distinct()
    page = Paginator(qs, per_page).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    ctx = dict(page=page, qs=qs, params=params.urlencode(), g=request.GET)
    ctx.update(extra or {})
    return render(request, template, ctx)


# ------------------------------------------------------------------ treasury
def _cash_guard(request):
    from .shop import can_see_cash

    if not can_see_cash(request.user):
        from django.core.exceptions import PermissionDenied

        raise PermissionDenied("الخزينة للمدير بس")


@login_required
def voucher_list(request):
    _cash_guard(request)
    qs = Voucher.objects.select_related("cashbox", "to_cashbox", "partner").order_by("-date", "-id")
    resp = _list(request, qs, {"kind": "kind", "state": "state", "cashbox": "cashbox_id"},
                 ["number", "legacy_number", "description", "payee", "partner__name"], "ui/voucher_list.html",
                 dict(cashboxes=CashBox.objects.filter(active=True), kinds=Voucher.KINDS, states=Voucher.STATES))
    return resp


@login_required
@permission_required("treasury.add_voucher", raise_exception=True)
def voucher_form(request, pk=None):
    _cash_guard(request)
    v = get_object_or_404(Voucher, pk=pk) if pk else Voucher(kind=request.GET.get("kind", "payment"), date=timezone.localdate(),
                                                              cashbox=CashBox.objects.filter(active=True).first())
    if v.pk and v.state != "draft":
        messages.error(request, "السند مرحّل — التعديل بالإلغاء (قيد عكسي) وإنشاء سند جديد")
        return redirect("voucher_detail", pk=v.pk)
    form = VoucherForm(request.POST or None, instance=v)
    initial = None
    if not v.pk and request.GET.get("accrual"):
        from accruals.models import Accrual

        a = Accrual.objects.filter(pk=request.GET["accrual"], state="open").first()
        if a:
            initial = [dict(accrual=a.pk, account=a.liability_account_id, partner=a.partner_id, amount=a.remaining,
                            description=f"سداد {a.description}"[:300])]
            form.initial.update(description=f"سداد {a.description}"[:300], payee=a.partner.name if a.partner_id else "")
    lines = VoucherLineFormSet(request.POST or None, instance=v, prefix="lines", initial=initial)
    if request.method == "POST" and form.is_valid() and (form.cleaned_data["kind"] == "transfer" or lines.is_valid()):
        with transaction.atomic():
            obj = form.save(commit=False)
            if not obj.created_by_id:
                obj.created_by = request.user
            if obj.kind != "transfer":
                total = sum((f.cleaned_data.get("amount") or 0 for f in lines.forms if f.cleaned_data and not f.cleaned_data.get("DELETE")), Decimal(0))
                obj.amount = obj.amount or total
            obj.save()
            if obj.kind != "transfer":
                lines.instance = obj
                lines.save()
        if "post" in request.POST:
            try:
                T.post_voucher(obj, user=request.user)
                messages.success(request, f"تم ترحيل السند {Voucher.objects.get(pk=obj.pk).number}")
            except ValidationError as e:
                _errs(request, e)
        else:
            messages.success(request, "تم حفظ السند كمسودة")
        return redirect("voucher_detail", pk=obj.pk)
    return render(request, "ui/voucher_form.html", dict(form=form, lines=lines, v=v))


@login_required
def voucher_detail(request, pk):
    _cash_guard(request)
    v = get_object_or_404(Voucher.objects.select_related("cashbox", "to_cashbox", "partner", "entry"), pk=pk)
    if request.method == "POST":
        try:
            if "post" in request.POST and request.user.has_perm("treasury.change_voucher"):
                T.post_voucher(v, user=request.user)
                messages.success(request, "تم الترحيل")
            elif "cancel" in request.POST and request.user.has_perm("treasury.change_voucher"):
                T.cancel_voucher(v, request.POST.get("reason") or "إلغاء", user=request.user)
                messages.success(request, "أُلغي السند بقيد عكسي")
            elif "delete" in request.POST and v.state == "draft":
                v.delete()
                messages.success(request, "حُذفت المسودة")
                return redirect("voucher_list")
            else:
                messages.error(request, "ليست لديك صلاحية لهذا الإجراء")
        except ValidationError as e:
            _errs(request, e)
        return redirect("voucher_detail", pk=pk)
    return render(request, "ui/voucher_detail.html", dict(v=v, lines=v.lines.select_related("account", "partner", "dim1", "dim2", "dim3"),
                                                          entry_lines=v.entry.lines.select_related("account", "dim1", "partner") if v.entry_id else []))


# ------------------------------------------------------------------ inventory
@login_required
def move_list(request):
    qs = StockMove.objects.select_related("warehouse", "partner").order_by("-date", "-id")
    return _list(request, qs, {"kind": "kind", "state": "state", "warehouse": "warehouse_id"},
                 ["number", "legacy_number", "description", "receiver", "lines__item__name"], "ui/move_list.html",
                 dict(warehouses=Warehouse.objects.filter(active=True), kinds=[k for k in StockMove.KINDS if k[0] != "harvest"], states=StockMove.STATES))


@login_required
@permission_required("inventory.add_stockmove", raise_exception=True)
def move_form(request, pk=None):
    m = get_object_or_404(StockMove, pk=pk) if pk else StockMove(kind=request.GET.get("kind", "issue"), date=timezone.localdate(),
                                                                  warehouse=Warehouse.objects.filter(active=True).first())
    if m.pk and m.state != "draft":
        return redirect("move_detail", pk=m.pk)
    form = MoveForm(request.POST or None, instance=m)
    lines = MoveLineFormSet(request.POST or None, instance=m, prefix="lines")
    if request.method == "POST" and form.is_valid() and lines.is_valid():
        with transaction.atomic():
            obj = form.save(commit=False)
            obj.created_by = obj.created_by or request.user
            obj.save()
            lines.instance = obj
            lines.save()
        if "post" in request.POST:
            try:
                INV.post_move(obj, user=request.user)
                messages.success(request, "تم الترحيل")
            except ValidationError as e:
                _errs(request, e)
        return redirect("move_detail", pk=obj.pk)
    return render(request, "ui/move_form.html", dict(form=form, lines=lines, m=m))


@login_required
def move_detail(request, pk):
    m = get_object_or_404(StockMove.objects.select_related("warehouse", "partner", "entry"), pk=pk)
    if request.method == "POST" and "post" in request.POST:
        try:
            INV.post_move(m, user=request.user)
            messages.success(request, "تم الترحيل")
        except ValidationError as e:
            _errs(request, e)
        return redirect("move_detail", pk=pk)
    return render(request, "ui/move_detail.html", dict(m=m, lines=m.lines.select_related("item", "dim1", "dim2", "account")))


# ------------------------------------------------------------------ journal entries
@login_required
def entry_list(request):
    qs = JournalEntry.objects.select_related("journal").annotate(total=Sum("lines__debit")).order_by("-date", "-id")
    return _list(request, qs, {"journal": "journal__code", "state": "state"}, ["number", "description", "legacy_ref"], "ui/entry_list.html",
                 dict(journals=Journal.objects.all(), states=JournalEntry.STATES))


@login_required
@permission_required("ledger.add_journalentry", raise_exception=True)
def entry_form(request, pk=None):
    e = get_object_or_404(JournalEntry, pk=pk) if pk else JournalEntry(journal=Journal.objects.get(code="JV"), date=timezone.localdate())
    if e.pk and e.state != "draft":
        return redirect("entry_detail", pk=e.pk)
    form = EntryForm(request.POST or None, instance=e)
    lines = EntryLineFormSet(request.POST or None, instance=e, prefix="lines")
    if request.method == "POST" and form.is_valid() and lines.is_valid():
        with transaction.atomic():
            obj = form.save(commit=False)
            obj.created_by = obj.created_by or request.user
            obj.save()
            lines.instance = obj
            lines.save()
        if "post" in request.POST:
            try:
                L.post(obj, user=request.user)
                messages.success(request, "تم الترحيل")
            except ValidationError as ex:
                _errs(request, ex)
        return redirect("entry_detail", pk=obj.pk)
    return render(request, "ui/entry_form.html", dict(form=form, lines=lines, e=e))


@login_required
def entry_detail(request, pk):
    e = get_object_or_404(JournalEntry.objects.select_related("journal", "reversal_of"), pk=pk)
    if request.method == "POST":
        try:
            if "post" in request.POST:
                L.post(e, user=request.user)
            elif "reverse" in request.POST and request.user.has_perm("ledger.change_journalentry"):
                rev = L.reverse(e, reason=request.POST.get("reason") or "", user=request.user)
                messages.success(request, f"عُكس بالقيد {rev.number}")
        except ValidationError as ex:
            _errs(request, ex)
        return redirect("entry_detail", pk=pk)
    return render(request, "ui/entry_detail.html", dict(e=e, lines=e.lines.select_related("account", "partner", "dim1", "dim2", "dim3")))


# ------------------------------------------------------------------ stock count (جرد) → adjustment
@login_required
def stock_count(request):
    from inventory import count as CNT
    from inventory.models import Warehouse

    whs = Warehouse.objects.filter(active=True)
    wh = whs.filter(pk=request.POST.get("warehouse") or request.GET.get("warehouse") or 0).first() or whs.first()
    if request.GET.get("template") and wh:
        from django.http import HttpResponse

        buf = io.BytesIO()
        CNT.template_workbook(wh).save(buf)
        resp = HttpResponse(buf.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        resp["Content-Disposition"] = f'attachment; filename="count-{wh.code}.xlsx"'
        return resp
    ctx = dict(whs=whs, wh=wh, date=request.POST.get("date") or timezone.localdate().isoformat(), opening=bool(request.POST.get("opening")))
    if request.method == "POST" and "confirm" in request.POST:
        rows = request.session.pop("stock_count", None)
        if not rows:
            messages.error(request, "انتهت الجلسة — ارفع الملف مرة أخرى")
            return redirect("stock_count")
        if not request.user.has_perm("inventory.change_stockmove"):
            messages.error(request, "ليست لديك صلاحية ترحيل حركات المخازن")
            return redirect("stock_count")
        try:
            wh = Warehouse.objects.get(pk=rows["warehouse"])
            moves = CNT.post_adjustment(wh, dt.date.fromisoformat(rows["date"]), rows["rows"], user=request.user, opening=rows.get("opening", False))
            messages.success(request, "تمت التسوية: " + "، ".join(f"{m.get_kind_display()} {m.number}" for m in moves) if moves else "لا توجد فروق")
            return redirect("shop_count")
        except ValidationError as e:
            _errs(request, e)
            return redirect("stock_count")
    if request.method == "POST" and request.FILES.get("file"):
        try:
            rows, missing = CNT.parse(request.FILES["file"], wh)
            diffs = [r for r in rows if r["diff"]]
            request.session["stock_count"] = dict(warehouse=wh.pk, date=ctx["date"], opening=bool(request.POST.get("opening")),
                                                  rows=[{k: (str(v) if isinstance(v, Decimal) else v) for k, v in r.items()} for r in diffs])
            ctx.update(rows=rows, diffs=diffs, missing=missing, plus=sum(1 for r in diffs if r["diff"] > 0), minus=sum(1 for r in diffs if r["diff"] < 0))
        except ValidationError as e:
            _errs(request, e)
    return render(request, "ui/stock_count.html", ctx)


@login_required
def ask(request):
    """«؟ اسأل»: Trio's AI (Claude via the Worker) when on, else the short guide inside the program."""
    import json as _json

    from django.http import JsonResponse

    from core import ai, help as H
    from core import modules as M
    from core.activities import current
    from core.models import CompanySettings
    from pos.branches import is_owner_level

    if request.method != "POST":
        return JsonResponse({"error": "POST"}, status=405)
    try:
        body = _json.loads(request.body or b"{}")
    except ValueError:
        body = {}
    q = str(body.get("q", "")).strip()[:500]
    if len(q) < 2:
        return JsonResponse({"error": "اكتب سؤالك"}, status=400)
    s = CompanySettings.get()
    mods = M.enabled(s)
    owner = request.user.is_superuser or is_owner_level(request.user)
    pages = H.pages(mods, owner)
    names = {p["url"]: p["name"] for p in pages}
    res, src = None, "local"
    if ai.enabled():
        topics = H.search(q, mods, 6) or H.search(" ".join(q.split()[:3]), mods, 6)
        res = ai.ask(q, dict(pages=pages, topics=[dict(t=t["t"] + ("" if t["ok"] else " (غير مفتوحة لديه)"), a=t["a"]) for t in topics],
                             mods=H.names_of(mods), role="صاحب المحل" if owner else "موظف", page=str(body.get("page", ""))[:80],
                             activity=current(s).get("name", "")))
        if res and not res.get("error"):
            src = "ai"
        elif res and res.get("error"):
            return JsonResponse({"answer": res["error"], "links": [], "src": "ai"})
    if src == "local":
        res = H.local_answer(q, mods)
    links = [dict(url=u, name=names.get(u, u)) for u in res.get("links", []) if u in names]
    return JsonResponse({"answer": res.get("answer", ""), "links": links, "src": src, "left": res.get("left")})
