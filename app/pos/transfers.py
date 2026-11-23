"""1.25 goods between branches, with approval.

    the receiving branch asks («طلب بضاعة») ─► the sending branch approves and ships («موافقة وإرسال», or refuses)
    ─► the goods sit in the hidden store «بضاعة في الطريق» ─► the receiving branch counts what arrived («استلام»):
    all of it, part now and the rest later, or part and closes it — the shortfall is either a loss charged to the sending branch
    (account 3801010102 on its cost centre) or goes back to the sending branch's shelf.

The owner can also ship straight away without a request. Stock is valued company-wide (one moving average, one stock account per
category), so moving goods between stores never touches the ledger — the ledger only sees a loss. Every step is a normal posted
stock move, so the count, the stock pages and «حد الطلب» of each branch stay right.
"""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

Z = Decimal(0)
TRANSIT_CODE = "TRANSIT"
LOSS_ACCOUNT = "3801010102"  # أعباء وخسائر (عجز / توالف)


def transit():
    """The store that holds goods on the road (hidden: never offered in a store list, never sold from)."""
    from inventory.models import Warehouse

    from .models import Branch

    wh = Warehouse.objects.filter(code=TRANSIT_CODE).first()
    if wh:
        return wh
    main = Branch.objects.order_by("id").first()
    proj = main.warehouse.project if main else __import__("ledger.models", fromlist=["x"]).DimensionValue.objects.filter(dimension__number=1).order_by("level", "id").first()
    return Warehouse.objects.create(code=TRANSIT_CODE, name="بضاعة في الطريق بين الفروع", project=proj, active=False)


def branch_of(wh):
    from .models import Branch

    return Branch.objects.filter(warehouse=wh).first()


# ------------------------------------------------------------------ who may do what
def _mine(user):
    from .branches import home_branch

    return home_branch(user)


def _head(user):
    from .voids import is_head

    return is_head(user)


def _stock_ok(user):
    from .branches import can

    return _head(user) or can(user, "stock") or can(user, "purchases")


def can_request(user, to_branch):
    mine = _mine(user)
    return _stock_ok(user) and (mine is None or mine.pk == to_branch.pk)


def can_send(user, t):
    mine = _mine(user)
    return t.state == "requested" and _stock_ok(user) and (mine is None or mine.warehouse_id == t.from_warehouse_id)


def can_receive(user, t):
    mine = _mine(user)
    return t.state in ("sent", "partial") and _stock_ok(user) and (mine is None or mine.warehouse_id == t.to_warehouse_id)


def can_cancel(user, t):
    if t.state == "requested":
        mine = _mine(user)
        return _head(user) or (mine is not None and mine.warehouse_id == t.to_warehouse_id) or t.requested_by_id == getattr(user, "pk", None)
    return t.state == "sent" and _head(user)  # goods on the road come back to the sender — the owner's call


def visible(user, qs):
    """A branch sees the transfers in and out of its own store; the owner sees them all."""
    from django.db.models import Q

    mine = _mine(user)
    return qs if mine is None else qs.filter(Q(from_warehouse=mine.warehouse) | Q(to_warehouse=mine.warehouse))


# ------------------------------------------------------------------ the steps
def _lines(lines):
    from inventory.models import Item

    out = {}
    for item, qty in lines:
        it = item if isinstance(item, Item) else Item.objects.filter(pk=item).first()
        q = Decimal(str(qty or 0))
        if not it or q <= 0:
            continue
        if it.item_type != "stock":
            raise ValidationError(f"«{it.name}» ليس صنفًا مخزنيًا — لا يُحوَّل")
        out[it.pk] = (it, out.get(it.pk, (it, Z))[1] + q)
    if not out:
        raise ValidationError("أضف صنفًا واحدًا على الأقل بكمية أكبر من صفر")
    return list(out.values())


def _pair(from_branch, to_branch):
    if not from_branch or not to_branch:
        raise ValidationError("اختر الفرعين")
    if from_branch.pk == to_branch.pk:
        raise ValidationError("لا يكون التحويل من الفرع إلى نفسه")
    if not (from_branch.active and to_branch.active):
        raise ValidationError("أحد الفرعين موقوف")


@transaction.atomic
def request(user, from_branch, to_branch, lines, note=""):
    """The receiving branch asks for goods."""
    from inventory.models import Transfer, TransferLine

    _pair(from_branch, to_branch)
    if not can_request(user, to_branch):
        raise ValidationError("الطلب يكون لفرعك فقط")
    t = Transfer.objects.create(from_warehouse=from_branch.warehouse, to_warehouse=to_branch.warehouse, note=(note or "").strip()[:300],
                                requested_by=user if getattr(user, "pk", None) else None)
    t.number = f"TR-{t.pk:04d}"
    t.save(update_fields=["number"])
    for it, q in _lines(lines):
        TransferLine.objects.create(transfer=t, item=it, qty_req=q)
    from core.alerts import on_transfer

    on_transfer(t, "request")
    return t


def _move(kind, wh, to, lines, desc, user, dim1=None):
    """lines: [(item, qty)] → a posted stock move."""
    from inventory import services as INV
    from inventory.models import StockMove, StockMoveLine

    mv = StockMove.objects.create(kind=kind, date=timezone.localdate(), warehouse=wh, to_warehouse=to, description=desc[:300],
                                  created_by=user if getattr(user, "pk", None) else None)
    for it, q in lines:
        StockMoveLine.objects.create(move=mv, item=it, qty=q, dim1=dim1, account=__import__("ledger.models", fromlist=["x"]).Account.objects.get(code=LOSS_ACCOUNT) if kind == "adjust_out" else None)
    INV.post_move(mv, user=user if getattr(user, "pk", None) else None)
    return mv


@transaction.atomic
def send(user, t, qtys=None, driver="", direct=False):
    """The sending branch approves and ships (qtys: {line pk: qty}; empty = what was asked). Goods leave its shelf now."""
    from inventory import services as INV
    from inventory.models import Transfer

    t = Transfer.objects.select_for_update().get(pk=t.pk)
    if t.state != "requested":
        raise ValidationError("هذا التحويل ليس بانتظار الموافقة")
    if not direct and not can_send(user, t):
        raise ValidationError("الموافقة والإرسال من الفرع المرسل أو صاحب النشاط")
    out = []
    for ln in t.lines.select_related("item"):
        q = ln.qty_req if qtys is None or ln.pk not in qtys else Decimal(str(qtys[ln.pk] or 0))
        if q < 0:
            raise ValidationError("الكمية لا تكون سالبة")
        ln.qty_sent = q
        ln.unit_cost = INV.cost_now(ln.item)
        ln.save(update_fields=["qty_sent", "unit_cost"])
        if q > 0:
            out.append((ln.item, q))
    if not out:
        raise ValidationError("لا توجد كمية مرسلة — إن لم تتوفر البضاعة اضغط «رفض»")
    to_b = branch_of(t.to_warehouse)
    mv = _move("transfer", t.from_warehouse, transit(), out, f"تحويل {t.number} إلى {to_b.name if to_b else t.to_warehouse.name}", user)
    t.moves.add(mv)
    t.state, t.sent_by, t.sent_at, t.driver = "sent", user if getattr(user, "pk", None) else None, timezone.now(), (driver or "").strip()[:100]
    t.save(update_fields=["state", "sent_by", "sent_at", "driver"])
    return t


@transaction.atomic
def ship(user, from_branch, to_branch, lines, note="", driver=""):
    """The owner (or the sending branch for its own goods) ships without a request."""
    mine = _mine(user)
    if not _stock_ok(user) or (mine is not None and mine.pk != from_branch.pk):
        raise ValidationError("الإرسال المباشر من فرعك فقط")
    _pair(from_branch, to_branch)
    from inventory.models import Transfer, TransferLine

    t = Transfer.objects.create(from_warehouse=from_branch.warehouse, to_warehouse=to_branch.warehouse, note=(note or "").strip()[:300],
                                requested_by=user if getattr(user, "pk", None) else None)
    t.number = f"TR-{t.pk:04d}"
    t.save(update_fields=["number"])
    for it, q in _lines(lines):
        TransferLine.objects.create(transfer=t, item=it, qty_req=q)
    return send(user, t, driver=driver, direct=True)


@transaction.atomic
def reject(user, t, reason=""):
    from inventory.models import Transfer

    t = Transfer.objects.select_for_update().get(pk=t.pk)
    if not can_send(user, t):
        raise ValidationError("الرفض من الفرع المرسل أو صاحب النشاط")
    if not (reason or "").strip():
        raise ValidationError("اكتب سبب الرفض")
    t.state, t.reply, t.sent_by, t.sent_at = "rejected", reason.strip()[:300], user, timezone.now()
    t.save(update_fields=["state", "reply", "sent_by", "sent_at"])
    return t


@transaction.atomic
def receive(user, t, qtys=None, close=True, diff="loss", reply=""):
    """The receiving branch counts what arrived ({line pk: qty}; empty = everything still on the road).
    close=False keeps the rest on the road (it may come later); close=True settles the rest now: «loss» = a loss charged to the
    sending branch, «back» = it returns to the sending branch's shelf."""
    from inventory.models import Transfer

    t = Transfer.objects.select_for_update().get(pk=t.pk)
    if not can_receive(user, t):
        raise ValidationError("الاستلام من الفرع المستلم أو صاحب النشاط")
    if diff not in ("loss", "back"):
        raise ValidationError("اختر ما يحدث للفرق")
    src = branch_of(t.from_warehouse)
    got, lost, back = [], [], []
    lines = list(t.lines.select_related("item"))
    for ln in lines:
        left = ln.pending
        q = left if qtys is None or ln.pk not in qtys else Decimal(str(qtys[ln.pk] or 0))
        if q < 0:
            raise ValidationError("الكمية لا تكون سالبة")
        if q > left:
            raise ValidationError(f"«{ln.item.name}»: المستلم ({q:g}) أكثر من المرسل الباقي ({left:g}) — الزيادة تُسجّل بالجرد")
        if q:
            got.append((ln.item, q))
            ln.qty_recv += q
        rest = left - q
        if close and rest > 0:
            (lost if diff == "loss" else back).append((ln.item, rest))
            if diff == "loss":
                ln.qty_lost += rest
            else:
                ln.qty_back += rest
        ln.save(update_fields=["qty_recv", "qty_lost", "qty_back"])
    if not (got or lost or back):
        raise ValidationError("لم تُسجّل أي كمية مستلمة")
    tw = transit()
    if got:
        t.moves.add(_move("transfer", tw, t.to_warehouse, got, f"استلام تحويل {t.number} من {src.name if src else t.from_warehouse.name}", user))
    if lost:
        t.moves.add(_move("adjust_out", tw, None, lost, f"عجز تحويل {t.number} على {src.name if src else t.from_warehouse.name}", user,
                          dim1=src.cost_center if src else None))
    if back:
        t.moves.add(_move("transfer", tw, t.from_warehouse, back, f"رجوع فرق تحويل {t.number} إلى {src.name if src else ''}", user))
    done = all(ln.pending <= 0 for ln in lines)
    t.state = "received" if done else "partial"
    t.received_by, t.received_at = user if getattr(user, "pk", None) else None, timezone.now()
    if reply:
        t.reply = reply.strip()[:300]
    t.save(update_fields=["state", "received_by", "received_at", "reply"])
    if lost:
        from core.alerts import on_transfer

        on_transfer(t, "loss")
    return t


@transaction.atomic
def cancel(user, t, reason=""):
    from inventory.models import Transfer

    t = Transfer.objects.select_for_update().get(pk=t.pk)
    if not can_cancel(user, t):
        raise ValidationError("لا يمكن إلغاء هذا التحويل" if t.state not in ("requested", "sent") else "الإلغاء لصاحب النشاط")
    if t.state == "sent":  # nothing received yet: everything goes back to the sender's shelf
        back = [(ln.item, ln.pending) for ln in t.lines.select_related("item") if ln.pending > 0]
        if back:
            t.moves.add(_move("transfer", transit(), t.from_warehouse, back, f"إلغاء تحويل {t.number}", user))
        for ln in t.lines.all():
            ln.qty_back += ln.pending
            ln.save(update_fields=["qty_back"])
    t.state, t.reply = "cancelled", (reason or "").strip()[:300] or t.reply
    t.save(update_fields=["state", "reply"])
    return t


# ------------------------------------------------------------------ numbers for the screens
def in_transit_value():
    from inventory.models import StockQuant, Warehouse

    wh = Warehouse.objects.filter(code=TRANSIT_CODE).first()
    if not wh:
        return Z
    return sum((q.qty * q.item.avg_cost for q in StockQuant.objects.filter(warehouse=wh).exclude(qty=0).select_related("item")), Z).quantize(Decimal("0.01"))


def waiting(user):
    """How many transfers wait for this user: requests to approve (sender) + goods to receive (receiver)."""
    from django.db.models import Q

    from inventory.models import Transfer

    mine = _mine(user)
    if mine is None:
        if not _head(user):
            return 0
        return Transfer.objects.filter(state__in=["requested", "sent", "partial"]).count()
    if not _stock_ok(user):
        return 0
    return Transfer.objects.filter(Q(state="requested", from_warehouse=mine.warehouse) | Q(state__in=["sent", "partial"], to_warehouse=mine.warehouse)).count()


def value(t):
    return sum((ln.qty_sent * ln.unit_cost for ln in t.lines.all()), Z).quantize(Decimal("0.01"))
