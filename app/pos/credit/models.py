"""1.25: when a credit customer must pay (one due date per credit sale, or an instalment plan), the reminders sent to him,
and the money the delivery platforms (Talabat, Elmenus) transfer to the shop."""
from django.db import models


class CustomerTerms(models.Model):
    """A credit customer's own payment period (empty = the shop's default) and the last reminder sent to him."""

    partner = models.OneToOneField("ledger.Partner", on_delete=models.CASCADE, related_name="credit_terms", verbose_name="العميل")
    days = models.PositiveSmallIntegerField("مدة السداد (يوم)", null=True, blank=True, help_text="فارغ = المدة العامة للمحل")
    reminded_at = models.DateTimeField("آخر تذكير", null=True, blank=True)
    reminded_by = models.CharField("ذكّره", max_length=100, blank=True)

    class Meta:
        verbose_name = "شروط سداد عميل"
        verbose_name_plural = "شروط سداد العملاء"


class Plan(models.Model):
    """An instalment plan: one credit sale (order) — or the customer's whole balance on that day (order empty) — split in
    `count` instalments. What was paid is counted against the earliest instalments first (FIFO)."""

    EVERY = [("month", "شهريًا"), ("2weeks", "كل أسبوعين"), ("week", "أسبوعيًا")]
    partner = models.ForeignKey("ledger.Partner", on_delete=models.PROTECT, related_name="credit_plans", verbose_name="العميل")
    order = models.ForeignKey("pos.Order", null=True, blank=True, on_delete=models.SET_NULL, related_name="+", verbose_name="الفاتورة")
    entry = models.ForeignKey("ledger.JournalEntry", null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
                              verbose_name="القيد المقسَّط")
    ref = models.CharField("المرجع", max_length=80, blank=True)
    amount = models.DecimalField("المبلغ المقسَّط", max_digits=14, decimal_places=2)
    count = models.PositiveSmallIntegerField("عدد الأقساط")
    every = models.CharField("الدورية", max_length=6, choices=EVERY, default="month")
    first_due = models.DateField("أول قسط")
    covers_until = models.DateField("يشمل المديونية حتى", null=True, blank=True)
    note = models.CharField("ملاحظة", max_length=200, blank=True)
    by = models.CharField("سجّلها", max_length=100, blank=True)
    cancelled = models.BooleanField("ملغاة", default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "خطة تقسيط"
        verbose_name_plural = "خطط التقسيط"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.partner} — {self.count} أقساط"


class Instalment(models.Model):
    plan = models.ForeignKey(Plan, on_delete=models.CASCADE, related_name="rows")
    no = models.PositiveSmallIntegerField("رقم القسط")
    due = models.DateField("تاريخ الاستحقاق", db_index=True)
    amount = models.DecimalField("القيمة", max_digits=14, decimal_places=2)

    class Meta:
        verbose_name = "قسط"
        verbose_name_plural = "الأقساط"
        ordering = ["plan", "no"]


class PlatformPayout(models.Model):
    """Money a delivery platform transferred to the shop (its receivable goes down); `other` = what it kept besides its
    commission (extra fees, fines, refunds to its customers)."""

    platform = models.CharField("المنصة", max_length=20)
    date = models.DateField("التاريخ")
    amount = models.DecimalField("المبلغ المحوَّل", max_digits=14, decimal_places=2)
    other = models.DecimalField("خصومات أخرى من المنصة", max_digits=14, decimal_places=2, default=0)
    box = models.ForeignKey("treasury.CashBox", on_delete=models.PROTECT, related_name="+", verbose_name="دخل في")
    entry = models.ForeignKey("ledger.JournalEntry", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    note = models.CharField("بيان", max_length=200, blank=True)
    by = models.CharField("سجّلها", max_length=100, blank=True)
    cancelled = models.BooleanField("ملغاة", default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "تحويل من منصة توصيل"
        verbose_name_plural = "تحويلات منصات التوصيل"
        ordering = ["-date", "-id"]
