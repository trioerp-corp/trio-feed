from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from ledger.models import Account

from . import services as INV
from .models import ProductionOrder, StockMove, StockMoveLine

Z = Decimal("0")


def wip_account():
    acc = Account.objects.filter(code="1202010190").first()
    if acc:
        return acc
    parent = Account.objects.get(code="12020101")
    return Account.objects.create(code="1202010190", name="إنتاج تحت التشغيل (المعمل)", parent=parent, level=6, is_postable=True,
                                  account_type="asset", normal_balance="debit", report="balance_sheet", cash_flow_category="operating")


def _cc(wh):
    p = wh.project
    return p if p.is_postable else p.children.filter(is_postable=True).order_by("code").first()


@transaction.atomic
def send_to_kitchen(po: ProductionOrder, user=None):
    po = ProductionOrder.objects.select_for_update().get(pk=po.pk)
    if po.state != "draft":
        raise ValidationError("صُرف الأمر بالفعل")
    ins = list(po.lines.filter(side="in"))
    if not ins or not po.lines.filter(side="out").exists():
        raise ValidationError("حدد الخامات والإنتاج المتوقع")
    mv = StockMove.objects.create(kind="issue", date=po.date_out, warehouse=po.from_warehouse, created_by=user,
                                  description=f"صرف خامات للمعمل — {po.name}")
    wip, cc = wip_account(), _cc(po.from_warehouse)
    for ln in ins:
        StockMoveLine.objects.create(move=mv, item=ln.item, qty=ln.qty, dim1=cc, account=wip)
    INV.post_move(mv, user=user)
    mv.refresh_from_db()
    po.cost = sum((l.value or Z for l in mv.lines.all()), Z)
    po.issue_move, po.state = mv, "in_kitchen"
    po.number = po.number or f"ت-{po.pk:04d}"
    po.save()
    return po


@transaction.atomic
def receive_output(po: ProductionOrder, date, actual=None, user=None):
    """actual: {line_id: qty} when the batch differs from the plan."""
    po = ProductionOrder.objects.select_for_update().get(pk=po.pk)
    if po.state != "in_kitchen":
        raise ValidationError("الأمر ليس في المعمل")
    outs = list(po.lines.filter(side="out").select_related("item"))
    for ln in outs:
        if actual and str(ln.pk) in actual:
            ln.qty = Decimal(str(actual[str(ln.pk)]))
            ln.save(update_fields=["qty"])
    outs = [l for l in outs if l.qty > 0]
    if not outs:
        raise ValidationError("لا يوجد إنتاج")
    shares = {}
    if any(l.share for l in outs):
        tot = sum((l.share or Z for l in outs), Z)
        shares = {l.pk: (l.share or Z) / tot for l in outs}
    else:
        values = {l.pk: l.qty * (l.item.sale_price or 1) for l in outs}
        tot = sum(values.values(), Z)
        shares = {k: v / tot for k, v in values.items()}
    mv = StockMove.objects.create(kind="receipt", date=date, warehouse=po.to_warehouse, credit_account=wip_account(), created_by=user,
                                  description=f"استلام إنتاج المعمل — {po.name}")
    left = po.cost
    for i, l in enumerate(outs):
        value = left if i == len(outs) - 1 else (po.cost * shares[l.pk]).quantize(Decimal("0.01"))
        left -= value
        StockMoveLine.objects.create(move=mv, item=l.item, qty=l.qty, unit_cost=(value / l.qty).quantize(Decimal("0.0001")))
    INV.post_move(mv, user=user)
    po.receipt_move, po.state, po.date_in = mv, "done", date
    po.save()
    return po


@transaction.atomic
def cancel(po: ProductionOrder, reason, user=None):
    """Undo a production order: the finished goods leave the store again and the raw materials come back (reversing entries)."""
    from pos.voids import reverse_stock

    po = ProductionOrder.objects.select_for_update().get(pk=po.pk)
    if po.state == "cancelled":
        raise ValidationError("أمر التصنيع ملغي بالفعل")
    if po.receipt_move_id and po.receipt_move.state == "posted":
        reverse_stock(po.receipt_move, f"إلغاء تصنيع {po.name}: {reason}", user)
    if po.issue_move_id and po.issue_move.state == "posted":
        reverse_stock(po.issue_move, f"إلغاء تصنيع {po.name}: {reason}", user)
    po.state = "cancelled"
    po.save(update_fields=["state"])
    return po
