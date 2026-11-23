"""Cancelling movements (owner only) and settling a supplier's balance to zero.

Nothing is deleted: every cancel posts the exact opposite entry (and puts the goods back), so the history stays readable.
"""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from core import audit
from inventory.models import Item, StockMove, StockQuant
from inventory import services as INV
from inventory.services import INCOMING, OUTGOING
from ledger import services as L
from ledger.models import Account, JournalLine

from .models import CashMove, ChangeRequest, Order

Z = Decimal("0")


def is_owner(user):
    """May approve voids / edits: the owner, or anyone given «اعتماد الإلغاء والتعديل» (a branch manager — for his branch)."""
    if not (user and user.is_authenticated):
        return False
    if user.is_superuser or user.groups.filter(name="مدير المحل").exists():
        return True
    from .branches import can

    return can(user, "approve")


def is_head(user):
    """The whole shop's owner pages (setup, payroll, taxes, prices / offers, master data): the owner or a head-office user who may
    approve — never a user tied to one branch (a branch manager approves his branch's voids, but does not run the business)."""
    return bool(user and getattr(user, "is_authenticated", False) and (user.is_superuser or (is_owner(user) and not _my_branch(user))))


def can_edit(user):
    """Cancel / edit invoices: the owner, or a cashier when the owner switched «يمكن للكاشير إلغاء الفواتير وتعديلها» on."""
    if is_owner(user):
        return True
    from core.models import CompanySettings

    return bool(user and user.is_authenticated and CompanySettings.get().cashier_can_edit and user.groups.filter(name="كاشير").exists())


def _need_owner(user):
    if not is_owner(user):
        raise ValidationError("الإلغاء والتصفير من حساب صاحب المحل فقط — يرسل الكاشير طلبًا")


def _my_branch(user):
    if not user or user == "system" or not getattr(user, "is_authenticated", False):
        return None
    from .branches import home_branch

    return home_branch(user)


def _need_branch(user, branch=None, warehouse=None):
    """A branch manager (or a branch cashier) cancels only what belongs to his own branch; the owner cancels everything."""
    mine = _my_branch(user)
    if mine is None:
        return
    if (branch is not None and branch.pk == mine.pk) or (warehouse is not None and warehouse.pk == mine.warehouse_id):
        return
    raise ValidationError("هذه حركة فرع آخر — الإلغاء والتعديل لحركات فرعك فقط")


def _cash_move_branch_ok(user, mv):
    mine = _my_branch(user)
    if mine is None:
        return
    if mv.shift_id and mv.shift.device.branch_id == mine.pk:
        return
    if mv.entry_id and mv.entry.lines.filter(account_id=mine.cashbox.account_id).exists():
        return
    raise ValidationError("هذه حركة فرع آخر — الإلغاء والتعديل لحركات فرعك فقط")


def _need_editor(user):
    if user == "system":
        return
    if not can_edit(user):
        raise ValidationError("الإلغاء والتعديل من حساب صاحب المحل — أو فعّل «يمكن للكاشير إلغاء الفواتير وتعديلها» من الإعدادات")


def _q(item, wh):
    q, _ = StockQuant.objects.get_or_create(item=item, warehouse=wh)
    return q


def reverse_stock(move: StockMove, reason, user=None):
    move = StockMove.objects.select_for_update().get(pk=move.pk)
    if move.state != "posted":
        raise ValidationError("الحركة غير مرحّلة أو أُلغيت من قبل")
    fix, seen = [], set()
    for ln in move.lines.select_related("item__category"):
        item = Item.objects.select_for_update().get(pk=ln.item_id)
        value = ln.value or Z
        if move.kind == "transfer":
            q = _q(item, move.warehouse)
            q.qty += ln.qty
            q.save()
            q2 = _q(item, move.to_warehouse)
            q2.qty -= ln.qty
            q2.save()
            continue
        sign = -1 if move.kind in INCOMING else 1 if move.kind in OUTGOING else 0
        q = _q(item, move.warehouse)
        q.qty += sign * ln.qty
        q.save()
        item.qty_on_hand += sign * ln.qty
        item.stock_value += sign * value
        if move.entry_id and item.pk not in seen:  # the entry's own cost true-ups are reversed with it
            seen.add(item.pk)
            for jl in move.entry.lines.filter(account=item.category.stock_account, description=f"تسوية تكلفة {item.name}"):
                item.stock_value -= jl.debit - jl.credit
        INV.true_up(item, fix)
        item.save(update_fields=["qty_on_hand", "stock_value"])
    if move.entry_id and move.entry.state == "posted":
        L.reverse(move.entry, date=timezone.localdate(), reason=reason, user=user)
    if fix:
        L.create_entry("ST", timezone.localdate(), f"تسوية تكلفة بعد إلغاء {move.number or move.pk}", fix, source=f"inventory.move:{move.pk}:fix", user=user)
    move.state = "cancelled"
    move.save(update_fields=["state"])
    audit.log("cancel", move, summary=f"إلغاء {move}: {reason}")
    return move


@transaction.atomic
def void_order(order: Order, reason, user=None):
    _need_editor(user)
    user = None if user == "system" else user
    order = Order.objects.select_for_update().get(pk=order.pk)
    _need_branch(user, branch=order.device.branch if order.device_id else None)
    if order.cancelled:
        raise ValidationError("الفاتورة ملغية بالفعل")
    from gaming.bookings import on_order_void as hall_void

    hall_void(order, editing=str(reason).startswith("تعديل:"))
    if order.entry_id and order.entry.state == "posted":
        L.reverse(order.entry, date=timezone.localdate(), reason=reason, user=user)
    if order.stock_move_id:
        reverse_stock(order.stock_move, reason, user)
    order.cancelled = True
    order.flags = (f"ملغية: {reason}؛ " + order.flags)[:300]
    order.save(update_fields=["cancelled", "flags"])
    from .delivery import on_order_void
    from .models import DeliveryOrder

    if not str(reason).startswith("تعديل:"):  # a real cancel of a bill the rider already paid in: the collection goes back too
        d = DeliveryOrder.objects.filter(order=order, state="paid", collect_move__isnull=False, collect_move__cancelled=False).first()
        if d:
            void_cash_move(d.collect_move, f"إلغاء الفاتورة {order.uid}: {reason}", user=user or "system")
    on_order_void(order)
    from core.alerts import on_void  # 1.25: the owner's WhatsApp

    on_void(order, reason, editing=str(reason).startswith("تعديل:"))
    return order


@transaction.atomic
def edit_order(order: Order, lines, reason, user=None, pay="", customer_id=None):
    """Change an invoice (quantities, prices, items; a line dropped = that item cancelled): the old one is cancelled with a
    reversing entry and stock back, and a corrected one is posted in its place (same time, same shift, number «…-E1»).
    lines: [{"item_code", "qty", "price", "discount"}]; pay: cash|card|credit (default: how the old one was paid)."""
    _need_editor(user)
    from .services import ingest_order

    order = Order.objects.select_for_update().get(pk=order.pk)
    if order.cancelled:
        raise ValidationError("الفاتورة ملغية بالفعل")
    lines = [dict(item_code=str(x["item_code"]), qty=str(Decimal(str(x["qty"]))), price=str(Decimal(str(x["price"]))),
                  discount=str(Decimal(str(x.get("discount") or 0)))) for x in lines if Decimal(str(x.get("qty") or 0)) > 0]
    if not lines:
        raise ValidationError("الفاتورة فارغة — لإلغائها كاملة استخدم «إلغاء الفاتورة»")
    who = "system" if user == "system" else user
    from .models import DeliveryOrder as _DO

    if _DO.objects.filter(order=order, collect_move__isnull=False, collect_move__cancelled=False).exists():
        raise ValidationError("هذه الفاتورة حُصّلت من عامل التوصيل — ألغِها وسجّل بيعًا جديدًا بدل التعديل")
    void_order(order, f"تعديل: {reason}", who)
    n = 1
    while Order.objects.filter(uid=f"{order.uid}-E{n}").exists():
        n += 1
    from core.models import CompanySettings
    from inventory.models import Item as It

    s = CompanySettings.get()
    total = Decimal(0)
    for ln in lines:
        it = It.objects.get(code=ln["item_code"])
        t = Decimal(ln["qty"]) * Decimal(ln["price"]) - Decimal(ln["discount"])
        if not s.pos_prices_include_vat:
            rate = it.vat_rate if it.vat_rate is not None else s.default_vat_rate
            t += (t * rate / 100).quantize(Decimal("0.01"))
        total += t.quantize(Decimal("0.01"))
    pay = pay if pay in ("cash", "card", "credit", "rider", "insta") else ""
    pay = pay or ("credit" if order.paid_credit else "rider" if order.paid_rider else "insta" if order.paid_insta and not order.paid_cash
                  else "card" if order.paid_card and not order.paid_cash else "cash")
    data = dict(order.raw or {}, uid=f"{order.uid}-E{n}", lines=lines, paid_cash="0", paid_card="0", paid_credit="0", paid_rider="0", paid_insta="0",
                created_at=order.created_at_device.isoformat(), shift_uid=order.shift.uid if order.shift_id else None,
                is_return=order.is_return, shift_seq=order.shift_seq)
    data["paid_" + pay] = str(total)
    if pay == "credit":
        data["customer_id"] = customer_id or order.customer_id
    new, _ = ingest_order(order.device, data)
    from .models import DeliveryOrder

    old_d = DeliveryOrder.objects.filter(order=order, collect_move__isnull=False).first()
    if old_d:  # the rider already handed the old bill's money in: the corrected bill counts as collected by the same paper
        DeliveryOrder.objects.filter(order=new).update(state="paid", collected_at=old_d.collected_at, collect_move=old_d.collect_move)
    new.flags = (f"تعديل للفاتورة {order.uid}: {reason}؛ " + new.flags)[:300]
    new.save(update_fields=["flags"])
    audit.log("edit", new, summary=f"تعديل {order.uid} ← {new.uid}: {reason}")
    return new


@transaction.atomic
def void_cash_move(mv: CashMove, reason, user=None):
    _need_editor(user)
    user = None if user == "system" else user
    mv = CashMove.objects.select_for_update().get(pk=mv.pk)
    _cash_move_branch_ok(user, mv)
    if mv.cancelled:
        raise ValidationError("الحركة ملغية بالفعل")
    if mv.entry_id and mv.entry.state == "posted":
        L.reverse(mv.entry, date=timezone.localdate(), reason=reason, user=user)
    mv.cancelled = True
    mv.note = (f"ملغية: {reason} — " + mv.note)[:200]
    mv.save(update_fields=["cancelled", "note"])
    from .delivery import on_move_void

    on_move_void(mv)
    audit.log("cancel", mv, summary=f"إلغاء {mv.get_kind_display()} {mv.amount}: {reason}")
    return mv


@transaction.atomic
def void_stock_move(move: StockMove, reason, user=None):
    """Purchase invoice / return / expired / transfer. A cash purchase also cancels its payment."""
    _need_owner(user)
    _need_branch(user, warehouse=move.warehouse)
    reverse_stock(move, reason, user)
    from treasury import services as T
    from treasury.models import Voucher

    for v in Voucher.objects.filter(legacy_number=f"PUR-{move.pk}", state="posted"):
        T.cancel_voucher(v, reason, user=user)
    from ledger.models import JournalEntry

    for e in JournalEntry.objects.filter(source=f"inventory.purchase-vat:{move.pk}", state="posted"):  # the invoice's input VAT
        L.reverse(e, date=timezone.localdate(), reason=reason, user=user)
    for cm in CashMove.objects.filter(uid=f"PUR-{move.pk}-OWNER", cancelled=False):  # paid by the owner from his pocket
        void_cash_move(cm, reason, user=user)
    return move


def find_target(target):
    """A cashier's request names an invoice or a drawer movement by its number."""
    return Order.objects.filter(uid=target).first() or CashMove.objects.filter(uid=target).first()


@transaction.atomic
def decide(req: ChangeRequest, approve, user, note=""):
    if user != "system":
        _need_owner(user)
    req = ChangeRequest.objects.select_for_update().get(pk=req.pk)
    if req.state != "pending":
        raise ValidationError("تم البت في هذا الطلب من قبل")
    if approve:
        obj = find_target(req.target)
        if obj is None:
            raise ValidationError(f"لم أجد الحركة {req.target} — ربما لم تصل بعد من جهاز الكاشير")
        reason = f"طلب {req.get_kind_display()} من {req.requested_by}: {req.reason}"
        (void_order if isinstance(obj, Order) else void_cash_move)(obj, reason, user)
    req.state = "approved" if approve else "rejected"
    req.decided_by = "الكاشير (مسموحله)" if user == "system" else user.get_username()
    req.decided_at = timezone.now()
    req.decision_note = (note or ("أُلغيت — يسجّلها الكاشير من جديد بشكل صحيح" if approve and req.kind == "edit" else ""))[:300]
    req.save()
    return req


def partner_balance(partner, code):
    r = JournalLine.objects.filter(account__code=code, partner=partner, entry__state__in=["posted", "reversed"]).aggregate(
        d=Sum("debit"), c=Sum("credit"))
    return (r["d"] or Z) - (r["c"] or Z)


@transaction.atomic
def zero_partner(partner, code, reason, user=None, date=None):
    """Settle a supplier's (or customer's) balance to zero: what we no longer owe is a gain, what we write off is a loss."""
    _need_owner(user)
    if _my_branch(user):  # a balance belongs to the whole business, not to one branch
        raise ValidationError("تصفير الحسابات لصاحب النشاط فقط")
    bal = partner_balance(partner, code)
    if not bal:
        raise ValidationError("الرصيد صفر بالفعل")
    acc = Account.objects.get(code=code)
    gain = Account.objects.get(code="4401010101")
    loss = Account.objects.filter(code="3202010125").first() or Account.objects.get(code="3801010102")
    desc = f"تصفير حساب {partner.name}: {reason}"
    if bal < 0:   # credit balance: we owed him → owing cancelled = other income
        lines = [dict(account=acc, debit=-bal, partner=partner), dict(account=gain, credit=-bal)]
    else:         # debit balance: he owes us → written off
        lines = [dict(account=loss, debit=bal), dict(account=acc, credit=bal, partner=partner)]
    entry = L.create_entry("JV", date or timezone.localdate(), desc, [dict(ln, description=desc) for ln in lines],
                           source=f"pos.zero:{partner.pk}", user=user)
    audit.log("zero", partner, summary=f"{desc} ({bal:,.2f})")
    return entry
