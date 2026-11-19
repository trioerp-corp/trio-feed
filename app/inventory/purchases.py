"""A purchase invoice — the same posting from the «فاتورة مشتريات» page and from the owner's phone."""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from ledger.models import Account
from treasury import services as T
from treasury.models import Voucher, VoucherLine

from . import services as INV
from .models import StockMove, StockMoveLine

Z = Decimal("0")


@transaction.atomic
def record(*, warehouse, supplier, date, lines, invoice="", pay="credit", paid=None, box=None, user=None, note=""):
    """lines: [dict(item=Item, qty=base-unit qty, unit_cost=cost per base unit, produced=None, expiry=None)].
    pay: cash (all now from box) | credit (all on the supplier) | split (paid now, the rest on the supplier). → (move, total, message)"""
    mv = StockMove.objects.create(kind="receipt", date=date, warehouse=warehouse, partner=supplier, legacy_number=(invoice or "")[:30],
                                  created_by=user, description=f"فاتورة مشتريات {invoice or ''} — {supplier.name}{' — ' + note if note else ''}"[:300])
    n = 0
    for ln in lines:
        qty, cost = ln["qty"], ln["unit_cost"]
        if qty is None or qty <= 0:
            raise ValidationError("يجب أن تكون الكمية أكبر من صفر")
        if cost is None or cost < 0:
            raise ValidationError("اكتب سعر الشراء لكل صنف")
        if ln.get("produced") and ln.get("expiry") and ln["expiry"] <= ln["produced"]:
            raise ValidationError("يجب أن يكون تاريخ الانتهاء بعد تاريخ الإنتاج")
        StockMoveLine.objects.create(move=mv, item=ln["item"], qty=qty, unit_cost=Decimal(cost).quantize(Decimal("0.0001")),
                                     produced=ln.get("produced"), expiry=ln.get("expiry"))
        n += 1
    if not n:
        raise ValidationError("أضف صنفًا واحدًا على الأقل")
    INV.post_move(mv, user=user)
    total = sum((l.value or Z for l in mv.lines.all()), Z)
    paid = total if pay == "cash" else (paid or Z) if pay == "split" else Z
    if paid < 0 or paid > total:
        raise ValidationError(f"يجب أن يكون المدفوع نقدًا بين صفر وإجمالي الفاتورة ({total:,.2f})")
    msg = f"تم تسجيل فاتورة {supplier.name} بـ {total:,.2f} آجل على حساب المورد"
    if paid:
        if box is None:
            raise ValidationError("اختر الخزينة التي دُفع منها")
        v = Voucher.objects.create(kind="payment", date=date, cashbox=box, amount=paid, currency="EGP", rate=1, partner=supplier,
                                   payee=supplier.name, description=f"سداد فاتورة مشتريات {mv.legacy_number} — {supplier.name}"[:300],
                                   legacy_number=f"PUR-{mv.pk}", created_by=user)
        VoucherLine.objects.create(voucher=v, account=Account.objects.get(code="2301020101"), partner=supplier, amount=paid)
        T.post_voucher(v, user=user)
        rest = total - paid
        msg = (f"تم تسجيل فاتورة {supplier.name} بـ {total:,.2f} — دُفع {paid:,.2f} نقدًا من {box.name}"
               + (f" والباقي {rest:,.2f} آجل على المورد" if rest else ""))
    return mv, total, msg
