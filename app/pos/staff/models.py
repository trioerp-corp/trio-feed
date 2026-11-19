"""Employees and their monthly pay (1.21). The money of a payslip goes to the books when the month is approved."""
from decimal import Decimal

from django.db import models

Z = Decimal(0)


class Employee(models.Model):
    name = models.CharField("الاسم", max_length=120)
    job = models.CharField("الوظيفة", max_length=80, blank=True)
    national_id = models.CharField("الرقم القومي", max_length=14, blank=True)
    phone = models.CharField("الموبايل", max_length=40, blank=True)
    hire_date = models.DateField("تاريخ التعيين", null=True, blank=True)
    end_date = models.DateField("تاريخ ترك الشغل", null=True, blank=True)
    salary = models.DecimalField("المرتب الأساسي الشهري", max_digits=12, decimal_places=2, default=0)
    allowances = models.DecimalField("بدلات ثابتة شهريًا", max_digits=12, decimal_places=2, default=0,
                                     help_text="مواصلات، وجبة… تدخل في المرتب الإجمالي")
    insured = models.BooleanField("مؤمّن عليه", default=True)
    insurable_wage = models.DecimalField("أجر الاشتراك التأميني", max_digits=12, decimal_places=2, null=True, blank=True,
                                         help_text="متاح = المرتب الإجمالي (بين الحد الأدنى والأقصى للسنة)")
    insurance_no = models.CharField("الرقم التأميني", max_length=30, blank=True)
    taxable = models.BooleanField("تُخصم منه ضريبة كسب العمل", default=True)
    pay_to = models.CharField("طريقة القبض", max_length=80, blank=True, help_text="نقدًا / محفظة 010… / حساب بنكي")
    partner = models.OneToOneField("ledger.Partner", null=True, blank=True, on_delete=models.PROTECT, related_name="employee_file")
    active = models.BooleanField("شغال", default=True)
    notes = models.CharField("ملاحظات", max_length=300, blank=True)

    class Meta:
        verbose_name = "موظف"
        verbose_name_plural = "الموظفين"
        ordering = ["-active", "name"]

    def __str__(self):
        return self.name

    @property
    def gross(self):
        return (self.salary or Z) + (self.allowances or Z)


class PayRun(models.Model):
    """One month's payroll. Draft → approved (entry in the books) → paid (net salaries out of the cash box)."""

    STATES = [("draft", "مسودة"), ("approved", "اعتُمد"), ("paid", "صُرف")]
    month = models.DateField("الشهر", unique=True, help_text="أول يوم في الشهر")
    state = models.CharField("الحالة", max_length=10, choices=STATES, default="draft")
    entry = models.ForeignKey("ledger.JournalEntry", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    pay_entry = models.ForeignKey("ledger.JournalEntry", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    by = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "مسير مرتبات"
        verbose_name_plural = "مسيرات المرتبات"
        ordering = ["-month"]

    def __str__(self):
        return f"مرتبات {self.month:%m/%Y}"


class Payslip(models.Model):
    run = models.ForeignKey(PayRun, on_delete=models.CASCADE, related_name="slips")
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="slips")
    salary = models.DecimalField("الأساسي", max_digits=12, decimal_places=2, default=0)
    allowances = models.DecimalField("البدلات", max_digits=12, decimal_places=2, default=0)
    bonus = models.DecimalField("مكافأة / إضافي", max_digits=12, decimal_places=2, default=0)
    absence = models.DecimalField("خصم غياب", max_digits=12, decimal_places=2, default=0)
    penalty = models.DecimalField("جزاءات", max_digits=12, decimal_places=2, default=0)
    advance = models.DecimalField("خصم سلفة", max_digits=12, decimal_places=2, default=0)
    gross = models.DecimalField("الإجمالي", max_digits=12, decimal_places=2, default=0)
    insurable = models.DecimalField("أجر الاشتراك", max_digits=12, decimal_places=2, default=0)
    emp_ins = models.DecimalField("تأمينات (حصة الموظف)", max_digits=12, decimal_places=2, default=0)
    er_ins = models.DecimalField("تأمينات (حصة صاحب العمل)", max_digits=12, decimal_places=2, default=0)
    tax = models.DecimalField("ضريبة كسب العمل", max_digits=12, decimal_places=2, default=0)
    martyrs = models.DecimalField("صندوق الشهداء", max_digits=12, decimal_places=2, default=0)
    net = models.DecimalField("الصافي", max_digits=12, decimal_places=2, default=0)

    class Meta:
        unique_together = [("run", "employee")]
        ordering = ["employee__name"]
