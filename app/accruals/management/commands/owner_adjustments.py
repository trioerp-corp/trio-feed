"""Corrections the owner decided on (28/10/2026). Each runs once (looked up by its journal source key).

1. The rest of September's salaries (306,491) was paid in October on «عماله» 1103010128 → reclass onto the September
   salaries accrual, which closes it.
2. The August leftover in 2301050102 at 30/9 (a debit of 217,920: August paid more than accrued) → cleared against
   salaries «اجور ومرتبات» 1103010127 so the accrued-salaries account is clean.
"""
import datetime as dt
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Sum

from accruals import services as S
from accruals.models import Accrual
from ledger import services as L
from ledger.models import Account, DimensionValue, JournalEntry, JournalLine


class Command(BaseCommand):
    help = "تسويات قرّرها المالك (مرة واحدة)"

    @transaction.atomic
    def handle(self, **o):
        site = DimensionValue.objects.get(dimension__number=1, code="11")
        sal = Accrual.objects.filter(source="opening", liability_account__code="2301050102").first()
        if sal and sal.state == "open" and sal.remaining > 0:
            S.settle_by_reclass(sal, sal.remaining, Account.objects.get(code="1103010128"), dt.date(2026, 10, 28), dim1=site,
                                note="صُرف باقي مرتبات سبتمبر 2026 على «عماله» في أكتوبر (تأكيد المالك 28/10)")
            self.stdout.write("تم إقفال استحقاق مرتبات سبتمبر")
        if not JournalEntry.objects.filter(source="owner.aug_salaries_diff").exists():
            liab = Account.objects.get(code="2301050102")
            r = JournalLine.objects.filter(account=liab, entry__state__in=["posted", "reversed"], entry__date__lte="2026-09-30").aggregate(d=Sum("debit"), c=Sum("credit"))
            leftover = (r["c"] or 0) - (r["d"] or 0) - (sal.amount if sal else 0)  # what is left after September's accrual
            if leftover:
                exp = Account.objects.get(code="1103010127")
                d, c = (Decimal(0), -leftover) if leftover < 0 else (leftover, Decimal(0))
                desc = "تصفية فرق أجور ومرتبات مستحقة من شهر أغسطس 2026 على حساب الأجور (تأكيد المالك 28/10)"
                L.create_entry("JV", dt.date(2026, 10, 1), desc, [
                    dict(account=liab, debit=d, credit=c, description=desc),
                    dict(account=exp, debit=c, credit=d, dim1=site, description=desc),
                ], source="owner.aug_salaries_diff")
                self.stdout.write(f"فرق أغسطس اتصفّر: {leftover:,.2f}")
