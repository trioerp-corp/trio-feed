"""Stock posting: perpetual inventory with company-wide moving average cost (constitution VIII)."""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from core.models import CompanySettings
from ledger import services as L
from ledger.models import Account, ZERO

from .models import Item, StockMove, StockMoveLine, StockQuant

INCOMING = {"receipt", "return", "adjust_in", "opening", "harvest"}
OUTGOING = {"issue", "adjust_out", "preturn", "expired"}


def _quant(item, wh):
    q, _ = StockQuant.objects.select_for_update().get_or_create(item=item, warehouse=wh)
    return q


def _is_admin_cc(cc):
    root = cc.root() if cc else None
    return bool(root and root.code == "2")


@transaction.atomic
def expired_account():
    acc = Account.objects.filter(code="3801010103").first()
    if acc:
        return acc
    return Account.objects.create(code="3801010103", name="بضاعة هالكة ومنتهية الصلاحية", parent=Account.objects.get(code="38010101"), level=6,
                                  is_postable=True, account_type="expense", normal_balance="debit", report="profit_loss",
                                  cash_flow_category="operating", dim1_policy="optional")


def cost_now(item):
    """What one unit costs right now: the moving average while there is stock, else the last price it was bought at (a shop that
    sells before recording the purchase must still book a cost of goods, not zero)."""
    if item.qty_on_hand > 0 and item.stock_value > 0:
        return item.stock_value / item.qty_on_hand
    last = (StockMoveLine.objects.filter(item=item, move__state="posted", move__kind__in=("receipt", "opening", "adjust_in", "production_in"),
                                         unit_cost__gt=0).order_by("-move__date", "-id").values_list("unit_cost", flat=True).first())
    return last or item.std_cost or ZERO


def true_up(item, gl=None, ref_cost=None, revalue=False):
    """Keeps an item's value honest and equal to the ledger: nothing in stock → no value; a balance and a value of opposite signs
    (a purchase cancelled after its goods were sold, a menu costed at the invoice price…) or a receipt that covers units already
    sold (revalue) → the balance is valued at the reference cost. The difference goes to cost of goods, in the same entry."""
    q, v = item.qty_on_hand, item.stock_value
    if q == 0:
        target = ZERO
    elif revalue or (q > 0) != (v > 0):
        target = L.q2(q * (ref_cost if ref_cost is not None else cost_now(item)))
    else:
        return ZERO
    adj = target - v
    if adj:
        item.stock_value = target
        if gl is not None:
            cogs = Account.objects.filter(code="3101010101").first() or item.category.farm_account or item.category.stock_account
            side, other = ("debit", "credit") if adj > 0 else ("credit", "debit")
            gl.append({"account": item.category.stock_account, side: abs(adj), "description": f"تسوية تكلفة {item.name}"})
            gl.append({"account": cogs, other: abs(adj), "description": f"تسوية تكلفة {item.name}"})
    return adj


def post_move(move: StockMove, user=None, allow_negative=False):
    move = StockMove.objects.select_for_update().get(pk=move.pk)
    if move.state != "draft":
        raise ValidationError("الحركة مرحّلة بالفعل")
    lines = list(move.lines.select_related("item__category", "dim1", "dim2"))
    if not lines:
        raise ValidationError("الحركة بدون أصناف")
    allow_neg = allow_negative or CompanySettings.get().allow_negative_stock
    gl = []
    credit_acc = move.credit_account
    for ln in lines:
        item = Item.objects.select_for_update().get(pk=ln.item_id)
        cat = item.category
        if item.variant_of_id is None and isinstance((item.extra or {}).get("grid"), dict):  # 1.25: stock lives on its sizes / colours
            raise ValidationError(f"«{item.name}» صنف بمقاسات وألوان — اختر المقاس واللون")
        if ln.qty <= 0:
            raise ValidationError(f"{item}: يجب أن تكون الكمية أكبر من صفر")
        if move.kind in INCOMING:
            if move.kind == "return":
                cost = cost_now(item) if ln.unit_cost is None else ln.unit_cost
            else:
                if ln.unit_cost is None:
                    raise ValidationError(f"{item}: سعر الوحدة مطلوب للوارد")
                cost = ln.unit_cost
            value = ln.value if (move.kind == "receipt" and ln.value) else L.q2(ln.qty * cost)  # a purchase line keeps its invoice money
            q = _quant(item, move.warehouse)
            q.qty += ln.qty
            q.save()
            was = item.qty_on_hand
            item.qty_on_hand += ln.qty
            item.stock_value += value
            ln.value = value
            ln.save(update_fields=["value"])
            gl.append(dict(account=cat.stock_account, debit=value, description=f"{item.name} {ln.qty} {item.unit}", quantity=ln.qty))
            true_up(item, gl, ref_cost=cost, revalue=was < 0 and cost > 0)  # units sold before they were received take this cost
            item.save(update_fields=["qty_on_hand", "stock_value"])
            if move.kind == "return":
                acc = ln.account or (cat.admin_account if _is_admin_cc(ln.dim1) else cat.farm_account)
                gl.append(dict(account=acc, credit=value, dim1=ln.dim1, dim2=ln.dim2, dim3=ln.dim3, description=f"مرتجع {item.name}"))
            elif move.kind == "harvest":
                gl.append(dict(account=Account.objects.get(code="1103010144"), credit=value, dim1=ln.dim1, description=f"حصاد {item.name}"))
            else:
                acc = credit_acc or (Account.objects.get(code="2301020101") if move.partner_id else Account.objects.get(code="1207010101"))
                if move.kind == "opening":
                    acc = credit_acc or Account.objects.get(code="1207010101")
                if move.kind == "adjust_in":
                    acc = credit_acc or Account.objects.get(code="4401010101")
                gl.append(dict(account=acc, credit=value, partner=move.partner if acc.is_partner_control else None,
                               dim1=ln.dim1 if acc.dim1_policy != "none" else None, description=f"{item.name} {ln.qty} {item.unit}"))
        elif move.kind in OUTGOING or move.kind == "transfer":
            q = _quant(item, move.warehouse)
            if q.qty < ln.qty and not allow_neg:
                raise ValidationError(f"{item}: الرصيد في {move.warehouse.name} ({q.qty}) لا يكفي لصرف {ln.qty}")
            # a café / restaurant order prices its ingredients at the last purchase invoice (set on the line by pos.services)
            # a supplier return of a known batch (1.24.1) goes back at that batch's own purchase cost, not the average
            cost = ln.unit_cost if (move.kind in ("issue", "preturn") and ln.unit_cost is not None) else cost_now(item)
            value = L.q2(ln.qty * cost)
            q.qty -= ln.qty
            q.save()
            ln.value = value
            ln.save(update_fields=["value"])
            if move.kind == "transfer":
                if not move.to_warehouse:
                    raise ValidationError("حدد المخزن المحوَّل إليه")
                q2 = _quant(item, move.to_warehouse)
                q2.qty += ln.qty
                q2.save()
                continue  # no GL effect, same company valuation
            item.qty_on_hand -= ln.qty
            item.stock_value -= value
            tail = []
            true_up(item, tail, ref_cost=cost if cost > 0 else None)
            item.save(update_fields=["qty_on_hand", "stock_value"])
            if move.kind == "issue":
                if ln.dim1 is None:
                    raise ValidationError(f"{item}: يجب تحديد القطاع / مركز التكلفة للصرف")
                if cat.needs_equipment and ln.dim2 is None:
                    raise ValidationError(f"{item}: صرف {cat.name} يتطلب تحديد المعدة")
                acc = ln.account or (cat.admin_account if _is_admin_cc(ln.dim1) else cat.farm_account)
                if acc is None:
                    raise ValidationError(f"تصنيف {cat.name} ليس له حساب صرف")
            elif move.kind == "preturn":
                if not move.partner_id:
                    raise ValidationError("مرتجع المشتريات يحتاج إلى المورد")
                acc = ln.account or Account.objects.get(code="2301020101")
            elif move.kind == "expired":
                acc = ln.account or expired_account()
            else:
                acc = ln.account or Account.objects.get(code="3801010102")  # أعباء وخسائر (عجز/توالف)
            gl.append(dict(account=acc, debit=value, dim1=ln.dim1, dim2=ln.dim2, dim3=ln.dim3, quantity=ln.qty,
                           partner=move.partner if acc.is_partner_control else None,
                           description=f"صرف {item.name} {ln.qty} {item.unit}"))
            gl.append(dict(account=cat.stock_account, credit=value, description=f"{item.name}"))
            gl.extend(tail)
        else:
            raise ValidationError("نوع حركة غير معروف")
    journal = "ST"
    if gl and any((g.get("debit") or 0) > 0 for g in gl):
        entry = L.create_entry(journal, move.date, f"{move.get_kind_display()}: {move.description or move.warehouse.name}", gl,
                               source=f"inventory.move:{move.pk}", legacy_ref=move.legacy_number, user=user)
        move.entry = entry
        move.number = entry.number
    else:
        move.number = L.next_number(__import__("ledger.models", fromlist=["Journal"]).Journal.objects.get(code=journal), move.date)
    move.state = "posted"
    move.save(update_fields=["entry", "number", "state"])
    return move
