"""Accruals: an expense that belongs to this month but is paid later (salaries, consultants, rent, fixed services).

Posting an accrual: Dr expense (by cost center) / Cr liability. Paying it: a treasury payment line that picks the accrual
debits the same liability; the accrual is closed when nothing remains.
"""
import datetime as dt
from decimal import Decimal

from django.db import models
from django.db.models import Sum

from ledger.models import Account, DimensionValue, JournalEntry, Partner

Z = Decimal("0")


class RecurringAccrual(models.Model):
    """A fixed monthly obligation (consultant, rent, internet …) — generates one accrual per month."""

    name = models.CharField("الاسم", max_length=150)
    partner = models.ForeignKey(Partner, null=True, blank=True, on_delete=models.PROTECT, verbose_name="المستحق له")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+", verbose_name="حساب المصروف",
                                limit_choices_to={"is_postable": True})
    dim1 = models.ForeignKey(DimensionValue, null=True, blank=True, on_delete=models.PROTECT, related_name="+", verbose_name="مركز التكلفة",
                             limit_choices_to={"dimension__number": 1, "is_postable": True})
    amount = models.DecimalField("المبلغ الشهري", max_digits=14, decimal_places=2)
    liability_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+", verbose_name="حساب الالتزام",
                                          limit_choices_to={"is_postable": True, "code__startswith": "23"})
    start = models.DateField("من شهر", default=dt.date(2026, 10, 1))
    end = models.DateField("حتى شهر (فارغ = مستمر)", null=True, blank=True)
    active = models.BooleanField("نشط", default=True)

    class Meta:
        verbose_name = "استحقاق ثابت شهري"
        verbose_name_plural = "الاستحقاقات الثابتة الشهرية"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Accrual(models.Model):
    SOURCES = [("manual", "يدوي"), ("recurring", "ثابت شهري"), ("payroll", "مرتبات"), ("opening", "رصيد افتتاحي")]
    STATES = [("draft", "مسودة"), ("open", "مفتوح (عليه التزام)"), ("closed", "سُدّد"), ("cancelled", "ملغي")]
    number = models.CharField("الرقم", max_length=30, blank=True, db_index=True)
    date = models.DateField("تاريخ الاستحقاق", db_index=True)
    month = models.DateField("عن شهر", null=True, blank=True)
    partner = models.ForeignKey(Partner, null=True, blank=True, on_delete=models.PROTECT, verbose_name="المستحق له")
    description = models.CharField("البيان", max_length=300)
    liability_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+", verbose_name="حساب الالتزام",
                                          limit_choices_to={"is_postable": True, "code__startswith": "23"})
    amount = models.DecimalField("المبلغ", max_digits=16, decimal_places=2, default=0)
    source = models.CharField("المصدر", max_length=10, choices=SOURCES, default="manual")
    template = models.ForeignKey(RecurringAccrual, null=True, blank=True, on_delete=models.SET_NULL, related_name="accruals")
    entry = models.ForeignKey(JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="+", verbose_name="القيد")
    state = models.CharField("الحالة", max_length=10, choices=STATES, default="draft")
    notes = models.TextField("ملاحظات", blank=True)

    class Meta:
        verbose_name = "استحقاق"
        verbose_name_plural = "الاستحقاقات"
        ordering = ["-date", "-id"]
        constraints = [models.UniqueConstraint(fields=["template", "month"], name="uniq_recurring_month")]

    def __str__(self):
        who = f" — {self.partner}" if self.partner_id else ""
        return f"{self.number or 'مسودة'} {self.description}{who}"

    @property
    def paid(self):
        v = self.payments.filter(voucher__state="posted").aggregate(s=Sum("amount"))["s"] or Z
        r = self.settlements.filter(entry__state="posted").aggregate(s=Sum("amount"))["s"] or Z
        return v + r

    @property
    def remaining(self):
        return self.amount - self.paid


class AccrualLine(models.Model):
    accrual = models.ForeignKey(Accrual, on_delete=models.CASCADE, related_name="lines")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+", verbose_name="حساب المصروف",
                                limit_choices_to={"is_postable": True})
    dim1 = models.ForeignKey(DimensionValue, null=True, blank=True, on_delete=models.PROTECT, related_name="+", verbose_name="مركز التكلفة")
    dim2 = models.ForeignKey(DimensionValue, null=True, blank=True, on_delete=models.PROTECT, related_name="+", verbose_name="المعدة")
    amount = models.DecimalField("المبلغ", max_digits=16, decimal_places=2)
    description = models.CharField("بيان", max_length=300, blank=True)

    class Meta:
        verbose_name = "سطر استحقاق"
        verbose_name_plural = "سطور الاستحقاق"


class AccrualSettlement(models.Model):
    """A payment already booked on an expense account, moved onto the accrual by a reclass entry (Dr liability / Cr expense)."""

    accrual = models.ForeignKey(Accrual, on_delete=models.PROTECT, related_name="settlements")
    date = models.DateField("التاريخ")
    amount = models.DecimalField("المبلغ", max_digits=16, decimal_places=2)
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+", verbose_name="الحساب الذي صُرف عليه")
    entry = models.ForeignKey(JournalEntry, on_delete=models.PROTECT, related_name="+", verbose_name="قيد الربط")
    note = models.CharField("ملاحظة", max_length=300, blank=True)

    class Meta:
        verbose_name = "ربط صرف بالاستحقاق"
        verbose_name_plural = "ربط صرف بالاستحقاقات"
