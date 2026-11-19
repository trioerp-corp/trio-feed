"""Open accruals that already sit in the opening trial balance, so next month's treasury payments close them.

Usage: python manage.py opening_accruals --date 2026-09-30 --salaries 1816392 --salaries-ref 475
- Salaries: the old books accrued the month's salaries (journal 475 «استحقاق اجور ومرتبات شهر سبتمبر 2026»).
- Other accrued expenses (consultants, rent …): one opening accrual per account with its 30/9 balance.
"""
import datetime as dt
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db.models import Sum

from accruals.models import Accrual
from ledger.models import Account, JournalLine


class Command(BaseCommand):
    help = "الاستحقاقات المفتوحة من الميزان الافتتاحي"

    def add_arguments(self, p):
        p.add_argument("--date", default="2026-09-30")
        p.add_argument("--salaries", default="1816392")
        p.add_argument("--salaries-ref", default="475")

    def handle(self, date, salaries, salaries_ref, **o):
        date = dt.date.fromisoformat(date)
        month = date.replace(day=1)
        made = 0
        if Decimal(salaries) and not Accrual.objects.filter(source="opening", liability_account__code="2301050102").exists():
            Accrual.objects.create(number=f"قديم-{salaries_ref}", date=date, month=month, liability_account=Account.objects.get(code="2301050102"),
                                   description=f"استحقاق أجور ومرتبات شهر {month:%m/%Y} (قيد {salaries_ref} في الدفاتر القديمة)",
                                   amount=Decimal(salaries), source="opening", state="open",
                                   notes="جزء من الميزان الافتتاحي — يُربط به صرف المرتبات من الخزينة ويقفله")
            made += 1
        for code in ("2301050101", "2301050103", "2301050104"):
            acc = Account.objects.get(code=code)
            r = JournalLine.objects.filter(account=acc, entry__state__in=["posted", "reversed"], entry__date__lte=date).aggregate(d=Sum("debit"), c=Sum("credit"))
            bal = (r["c"] or 0) - (r["d"] or 0)
            if bal > 0 and not Accrual.objects.filter(source="opening", liability_account=acc).exists():
                Accrual.objects.create(number="افتتاحي", date=date, month=month, liability_account=acc, amount=bal, source="opening", state="open",
                                       description=f"{acc.name} — رصيد {date:%d/%m/%Y}", notes="من الميزان الافتتاحي")
                made += 1
        self.stdout.write(f"استحقاقات افتتاحية: {made}")
