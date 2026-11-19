from decimal import Decimal

from django.conf import settings
from django.db import models

from ledger.models import ZERO, Account, DimensionValue, JournalEntry, Partner


class CashBox(models.Model):
    KINDS = [("cash", "خزينة نقدية"), ("wallet", "محفظة إلكترونية"), ("bank", "حساب بنكي")]
    name = models.CharField("الاسم", max_length=100)
    kind = models.CharField("النوع", max_length=6, choices=KINDS)
    account = models.OneToOneField(Account, on_delete=models.PROTECT, verbose_name="الحساب في الشجرة", limit_choices_to={"is_postable": True})
    currency = models.CharField("العملة", max_length=3, default="EGP")
    legacy_name = models.CharField("الاسم في شيت الخزينة", max_length=60, blank=True)
    allow_negative = models.BooleanField("يسمح بالرصيد السالب", default=False)
    active = models.BooleanField("نشط", default=True)
    order = models.PositiveSmallIntegerField("الترتيب", default=0)

    class Meta:
        verbose_name = "خزينة / بنك / محفظة"
        verbose_name_plural = "الخزائن والبنوك والمحافظ"
        ordering = ["order", "name"]

    def __str__(self):
        return self.name


class Voucher(models.Model):
    KINDS = [("receipt", "سند قبض"), ("payment", "سند صرف"), ("transfer", "تحويل داخلي")]
    STATES = [("draft", "مسودة"), ("posted", "مرحّل"), ("cancelled", "ملغي")]
    kind = models.CharField("النوع", max_length=8, choices=KINDS)
    number = models.CharField("الرقم", max_length=30, blank=True, db_index=True)
    legacy_number = models.CharField("رقم السند في الشيت القديم", max_length=30, blank=True, db_index=True)
    date = models.DateField("التاريخ", db_index=True)
    cashbox = models.ForeignKey(CashBox, on_delete=models.PROTECT, related_name="vouchers", verbose_name="الخزينة/البنك")
    to_cashbox = models.ForeignKey(CashBox, null=True, blank=True, on_delete=models.PROTECT, related_name="incoming_transfers", verbose_name="إلى (للتحويل)")
    currency = models.CharField("العملة", max_length=3, default="EGP")
    rate = models.DecimalField("سعر الصرف", max_digits=14, decimal_places=6, default=Decimal("1"))
    amount = models.DecimalField("المبلغ", max_digits=18, decimal_places=2)
    partner = models.ForeignKey(Partner, null=True, blank=True, on_delete=models.PROTECT, verbose_name="الطرف")
    payee = models.CharField("الاسم (المستلم/المسلم)", max_length=200, blank=True)
    description = models.CharField("البيان", max_length=300)
    approved_by_text = models.CharField("باعتماد", max_length=100, blank=True)
    state = models.CharField("الحالة", max_length=10, choices=STATES, default="draft", db_index=True)
    entry = models.OneToOneField(JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="voucher", verbose_name="القيد")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "سند خزينة"
        verbose_name_plural = "سندات الخزينة"
        ordering = ["-date", "-id"]

    def __str__(self):
        return f"{self.get_kind_display()} {self.number or self.legacy_number or 'مسودة'}"

    @property
    def lines_total(self):
        return sum((l.amount for l in self.lines.all()), ZERO)


class VoucherLine(models.Model):
    voucher = models.ForeignKey(Voucher, on_delete=models.CASCADE, related_name="lines")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, verbose_name="الحساب المقابل", limit_choices_to={"is_postable": True})
    partner = models.ForeignKey(Partner, null=True, blank=True, on_delete=models.PROTECT, verbose_name="الطرف")
    amount = models.DecimalField("المبلغ", max_digits=18, decimal_places=2)
    description = models.CharField("البيان", max_length=300, blank=True)
    dim1 = models.ForeignKey(DimensionValue, null=True, blank=True, on_delete=models.PROTECT, related_name="+", verbose_name="مركز التكلفة")
    dim2 = models.ForeignKey(DimensionValue, null=True, blank=True, on_delete=models.PROTECT, related_name="+", verbose_name="المعدة")
    dim3 = models.ForeignKey(DimensionValue, null=True, blank=True, on_delete=models.PROTECT, related_name="+", verbose_name="البعد الثالث")
    accrual = models.ForeignKey("accruals.Accrual", null=True, blank=True, on_delete=models.PROTECT, related_name="payments",
                                verbose_name="سداد استحقاق")

    class Meta:
        verbose_name = "سطر سند"
        verbose_name_plural = "سطور السند"


class DayClose(models.Model):
    date = models.DateField("اليوم", unique=True)
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, verbose_name="أقفله")
    closed_at = models.DateTimeField("وقت الإقفال", auto_now_add=True)
    notes = models.CharField("ملاحظات", max_length=300, blank=True)
    adjustment = models.OneToOneField(JournalEntry, null=True, blank=True, on_delete=models.PROTECT, verbose_name="قيد فروق الجرد")

    class Meta:
        verbose_name = "إقفال يوم"
        verbose_name_plural = "إقفال الأيام"
        ordering = ["-date"]

    def __str__(self):
        return f"إقفال {self.date}"


class DayCloseCount(models.Model):
    day = models.ForeignKey(DayClose, on_delete=models.CASCADE, related_name="counts")
    cashbox = models.ForeignKey(CashBox, on_delete=models.PROTECT, verbose_name="الخزينة")
    book_balance = models.DecimalField("رصيد الدفاتر", max_digits=18, decimal_places=2)
    counted = models.DecimalField("الجرد الفعلي", max_digits=18, decimal_places=2)

    @property
    def difference(self):
        return self.counted - self.book_balance


class TreasuryMapping(models.Model):
    """How a legacy treasury-sheet classification maps to the ledger (used by import and by quick entry)."""

    kind = models.CharField("نوع القيمة في الشيت", max_length=10, choices=[("tawjeeh", "توجيه"), ("jiha", "جهة")])
    legacy_value = models.CharField("القيمة في الشيت", max_length=100)
    context = models.CharField("في سياق (جهة/فئة)", max_length=20, blank=True, choices=[("", "أي"), ("farm", "زراعي"), ("admin", "إداري")])
    account = models.ForeignKey(Account, null=True, blank=True, on_delete=models.PROTECT, verbose_name="الحساب", limit_choices_to={"is_postable": True})
    cost_center = models.ForeignKey(DimensionValue, null=True, blank=True, on_delete=models.PROTECT, verbose_name="مركز التكلفة", related_name="+")
    note = models.CharField("ملاحظة", max_length=200, blank=True)

    class Meta:
        verbose_name = "ربط شيت الخزينة"
        verbose_name_plural = "ربط شيت الخزينة بالحسابات"
        unique_together = [("kind", "legacy_value", "context")]
        ordering = ["kind", "legacy_value"]

    def __str__(self):
        return f"{self.get_kind_display()}: {self.legacy_value} ({self.context or 'أي'})"
