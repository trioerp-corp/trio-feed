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
SUP_ACC, VAT_ACC = "2301020101", "2301040301"


def vat_of(mv):
    """The input VAT booked with a purchase invoice (0 when none)."""
    from ledger.models import JournalLine

    return sum((l.debit for l in JournalLine.objects.filter(entry__source=f"inventory.purchase-vat:{mv.pk}", entry__state="posted",
                                                            account__code=VAT_ACC)), Z)


def lines_from_invoice(rows, *, discount=None, vat_pct=None, vat_registered=False):
    """A supplier's invoice as it is printed → stock lines in base units.
    rows: [dict(item, qty, factor, cost, bonus=0, public=None, disc_pct=None, produced, expiry)] — qty / bonus / cost per the unit
    bought (a box = factor strips); cost empty = public price − the pharmacist's discount %. Bonus units come free: the paid value
    is spread over all the units received. The invoice discount (an amount) is spread over the lines by value; the VAT % on the
    net goes into the costs unless the shop is VAT-registered (then it is returned to be booked as input VAT).
    → (lines for record(), vat amount for record())"""
    out, gross = [], Z
    for r in rows:
        f = Decimal(r.get("factor") or 1)
        if f <= 0:
            raise ValidationError("يجب أن يكون محتوى الوحدة أكبر من صفر")
        qty, bonus = Decimal(r["qty"]), Decimal(r.get("bonus") or 0)
        if qty <= 0:
            raise ValidationError("يجب أن تكون الكمية أكبر من صفر")
        if bonus < 0:
            raise ValidationError("لا يمكن أن يكون البونص بالسالب")
        cost, public, dp = r.get("cost"), r.get("public"), r.get("disc_pct")
        if dp is not None and not (0 <= dp < 100):
            raise ValidationError("نسبة الخصم من 0 إلى أقل من 100")
        if cost is None and public is not None:
            cost = public * (100 - (dp or 0)) / 100
        if cost is None or cost < 0:
            raise ValidationError(f"اكتب سعر الشراء لـ «{r['item'].name}»")
        value = qty * Decimal(cost)
        gross += value
        out.append(dict(item=r["item"], value=value, base=(qty + bonus) * f, produced=r.get("produced"), expiry=r.get("expiry")))
    discount = Decimal(discount or 0)
    if discount < 0 or (gross and discount > gross):
        raise ValidationError(f"خصم الفاتورة من صفر إلى إجمالي الأصناف ({gross:,.2f})")
    net = gross - discount
    vat = (net * Decimal(vat_pct or 0) / 100).quantize(Decimal("0.01"))
    if vat_pct is not None and not (0 <= vat_pct <= 100):
        raise ValidationError("نسبة الضريبة من 0 إلى 100")
    in_cost = Z if vat_registered else vat
    k = ((net + in_cost) / gross) if gross else Decimal(1)
    # exact money per line: the lines add up to the invoice to the piaster (largest remainder), so the supplier's balance equals
    # the printed invoice; the cost per base unit is only for the average cost
    payable, c = (net + in_cost).quantize(Decimal("0.01")), Decimal("0.01")
    vals = [(o["value"] * k).quantize(c, rounding="ROUND_DOWN") for o in out]
    left = int(((payable - sum(vals, Z)) / c).to_integral_value())
    order = sorted(range(len(out)), key=lambda i: -((out[i]["value"] * k) - vals[i]))
    for i in order[:max(left, 0)]:
        vals[i] += c
    lines = [dict(item=o["item"], qty=o["base"], unit_cost=(v / o["base"]), value=v, produced=o["produced"], expiry=o["expiry"])
             for o, v in zip(out, vals)]
    return lines, (vat if vat_registered else Z)


@transaction.atomic
def record(*, warehouse, supplier, date, lines, invoice="", pay="credit", paid=None, box=None, user=None, note="", vat=None):
    """lines: [dict(item=Item, qty=base-unit qty, unit_cost=cost per base unit, produced=None, expiry=None)].
    vat: the invoice's input VAT for a VAT-registered shop (Dr ضريبة القيمة المضافة / Cr the supplier, on its own entry); a shop
         that is not registered puts the tax in the line costs instead (see lines_from_invoice).
    pay: cash (all now from box) | credit (all on the supplier) | split (paid now, the rest on the supplier)
         | owner (the owner paid it from his own pocket: Dr الموردين / Cr جاري صاحب المحل — the drawer is not touched). → (move, total, message)"""
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
                                     value=Decimal(ln.get("value") or 0).quantize(Decimal("0.01")),  # exact line money (lines_from_invoice)
                                     produced=ln.get("produced"), expiry=ln.get("expiry"))
        n += 1
    if not n:
        raise ValidationError("أضف صنفًا واحدًا على الأقل")
    INV.post_move(mv, user=user)
    total = sum((l.value or Z for l in mv.lines.all()), Z)
    vat = Decimal(vat or 0).quantize(Decimal("0.01"))
    if vat < 0:
        raise ValidationError("لا يمكن أن تكون الضريبة بالسالب")
    if vat:
        from ledger import services as L

        L.create_entry("JV", date, f"ضريبة مدخلات فاتورة {invoice or mv.number} — {supplier.name}"[:300],
                       [dict(account=Account.objects.get(code=VAT_ACC), debit=vat),
                        dict(account=Account.objects.get(code=SUP_ACC), credit=vat, partner=supplier)], source=f"inventory.purchase-vat:{mv.pk}", user=user)
        total += vat
    if pay == "owner":
        from pos import money

        if total > 0:
            money.post("supplier", total, date, from_owner=True, partner=supplier, uid=f"PUR-{mv.pk}-OWNER",
                       note=f"فاتورة مشتريات {mv.legacy_number or mv.pk}", by=getattr(user, "username", "") or "", user=user)
        return mv, total, (f"تم تسجيل فاتورة {supplier.name} بـ {total:,.2f} — دفعها صاحب المحل من جيبه، "
                           "وأُضيف المبلغ إلى رصيده لدى المحل (يسترده من الخزينة لاحقًا)")
    paid = total if pay == "cash" else (paid or Z) if pay == "split" else Z
    if paid < 0 or paid > total:
        raise ValidationError(f"يجب أن يكون المدفوع نقدًا بين صفر وإجمالي الفاتورة ({total:,.2f})")
    msg = f"تم تسجيل فاتورة {supplier.name} بـ {total:,.2f} آجل على حساب المورد"
    if paid:
        if box is None:
            raise ValidationError("اختر الخزينة التي دُفع منها")
        from core.models import CompanySettings

        if not (box.allow_negative or CompanySettings.get().allow_negative_cash):
            bal = T.cashbox_balance(box, in_currency=True)
            if bal < paid:
                raise ValidationError(f"رصيد {box.name} الآن {bal:,.2f} ولا يكفي لدفع {paid:,.2f} — اختر «من صاحب المحل (من جيبه)» إن كنت دفعتها "
                                      "من مالك الخاص، أو «آجل على المورد»، أو «جزء نقدًا والباقي آجل» بالمبلغ الموجود")
        v = Voucher.objects.create(kind="payment", date=date, cashbox=box, amount=paid, currency="EGP", rate=1, partner=supplier,
                                   payee=supplier.name, description=f"سداد فاتورة مشتريات {mv.legacy_number} — {supplier.name}"[:300],
                                   legacy_number=f"PUR-{mv.pk}", created_by=user)
        VoucherLine.objects.create(voucher=v, account=Account.objects.get(code="2301020101"), partner=supplier, amount=paid)
        T.post_voucher(v, user=user)
        rest = total - paid
        msg = (f"تم تسجيل فاتورة {supplier.name} بـ {total:,.2f} — دُفع {paid:,.2f} نقدًا من {box.name}"
               + (f" والباقي {rest:,.2f} آجل على المورد" if rest else ""))
    return mv, total, msg
