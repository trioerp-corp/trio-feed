from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

ZERO = Decimal("0.00")

ACCOUNT_TYPES = [
    ("asset", "أصول"),
    ("liability", "التزامات"),
    ("equity", "حقوق ملكية"),
    ("income", "إيرادات"),
    ("expense", "مصروفات"),
]
CASH_FLOW = [
    ("cash", "نقدية"),
    ("operating", "تشغيلي"),
    ("investing", "استثماري"),
    ("financing", "تمويلي"),
    ("non_cash", "غير نقدي"),
]
POLICY = [("required", "إجباري"), ("optional", "اختياري"), ("none", "غير مستخدم")]


class Account(models.Model):
    code = models.CharField("رقم الحساب", max_length=20, unique=True)
    name = models.CharField("اسم الحساب", max_length=200)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="children", verbose_name="الحساب الرئيسي")
    level = models.PositiveSmallIntegerField("المستوى", default=1)
    is_postable = models.BooleanField("فرعي (يقبل قيود)", default=False)
    account_type = models.CharField("النوع", max_length=10, choices=ACCOUNT_TYPES)
    normal_balance = models.CharField("الطبيعة", max_length=6, choices=[("debit", "مدين"), ("credit", "دائن")])
    report = models.CharField("التقرير", max_length=15, choices=[("balance_sheet", "الميزانية"), ("profit_loss", "الأرباح والخسائر")])
    cash_flow_category = models.CharField("تصنيف التدفقات النقدية", max_length=10, choices=CASH_FLOW, blank=True)
    currencies = models.CharField("العملات المسموحة", max_length=40, default="EGP", help_text="مفصولة بـ ; مثل EGP;USD")
    is_partner_control = models.BooleanField("يتطلب طرف (عميل/مورد/موظف)", default=False)
    dim1_policy = models.CharField("مركز التكلفة", max_length=8, choices=POLICY, default="none")
    dim2_policy = models.CharField("المعدة", max_length=8, choices=POLICY, default="none")
    dim3_policy = models.CharField("البعد الثالث", max_length=8, choices=POLICY, default="none")
    active = models.BooleanField("نشط", default=True)
    notes = models.TextField("ملاحظات", blank=True)

    class Meta:
        verbose_name = "حساب"
        verbose_name_plural = "شجرة الحسابات"
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} - {self.name}"

    @property
    def currency_list(self):
        return [c.strip() for c in self.currencies.split(";") if c.strip()]

    def descendants_q(self):
        return models.Q(account__code__startswith=self.code)

    def clean(self):
        if self.parent and not self.code.startswith(self.parent.code):
            raise ValidationError("يجب أن يبدأ كود الحساب بكود الحساب الرئيسي")
        if self.pk and self.is_postable and self.children.exists():
            raise ValidationError("حساب له فروع لا يمكن أن يكون فرعي")


class Dimension(models.Model):
    number = models.PositiveSmallIntegerField("رقم البعد", unique=True, choices=[(1, "1"), (2, "2"), (3, "3")])
    name = models.CharField("اسم البعد", max_length=80)
    active = models.BooleanField("مفعل", default=True)

    class Meta:
        verbose_name = "بعد تحليلي"
        verbose_name_plural = "الأبعاد التحليلية"
        ordering = ["number"]

    def __str__(self):
        return f"{self.number}: {self.name}"


class DimensionValue(models.Model):
    """A node in one analytic dimension tree (cost center, equipment, activity...)."""

    dimension = models.ForeignKey(Dimension, on_delete=models.PROTECT, related_name="values", verbose_name="البعد")
    code = models.CharField("الكود", max_length=30)
    name = models.CharField("الاسم", max_length=200)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="children", verbose_name="الأب")
    level = models.PositiveSmallIntegerField("المستوى", default=1)
    is_postable = models.BooleanField("يقبل تسجيل", default=True)
    active = models.BooleanField("نشط", default=True)
    is_project = models.BooleanField("مشروع", default=False)
    project_kind = models.CharField("نوع المشروع", max_length=10, blank=True, choices=[("permanent", "دائم"), ("temporary", "مؤقت")])
    project_status = models.CharField("حالة المشروع", max_length=6, default="open", choices=[("open", "مفتوح"), ("closed", "مقفول")])
    closed_at = models.DateField("تاريخ القفل", null=True, blank=True)
    legacy_code = models.CharField("الكود في السيستم القديم", max_length=30, blank=True, db_index=True)
    aliases = models.TextField("أسماء/كتابات أخرى", blank=True, help_text="كل كتابة في سطر أو مفصولة بـ /")
    default_cost_center = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="equipment_served",
        verbose_name="مركز التكلفة الافتراضي (للمعدات)", limit_choices_to={"dimension__number": 1},
    )
    needs_confirmation = models.BooleanField("يحتاج إلى تأكيد", default=False)

    class Meta:
        verbose_name = "قيمة بعد (مركز تكلفة / معدة)"
        verbose_name_plural = "مراكز التكلفة والمعدات"
        unique_together = [("dimension", "code")]
        ordering = ["dimension__number", "code"]
        indexes = [models.Index(fields=["dimension", "name"])]

    def __str__(self):
        return f"{self.code} - {self.name}"

    def root(self):
        node = self
        while node.parent_id:
            node = node.parent
        return node

    def project(self):
        node = self
        while node:
            if node.is_project:
                return node
            node = node.parent
        return None

    def clean(self):
        if self.parent and self.parent.dimension_id != self.dimension_id:
            raise ValidationError("يجب أن يكون الأب في البعد نفسه")


class Partner(models.Model):
    KINDS = [("customer", "عميل"), ("supplier", "مورد"), ("employee", "موظف"), ("contractor", "مقاول/عمالة"),
             ("related", "طرف ذو علاقة/شريك"), ("other", "أخرى")]
    name = models.CharField("الاسم", max_length=200, db_index=True)
    kind = models.CharField("النوع", max_length=12, choices=KINDS, default="other")
    aliases = models.TextField("كتابات أخرى للاسم", blank=True)
    tax_id = models.CharField("الرقم الضريبي", max_length=40, blank=True)
    phone = models.CharField("التليفون", max_length=40, blank=True)
    country = models.CharField("الدولة", max_length=60, blank=True)
    credit_limit = models.DecimalField("حد المديونية (للعميل الآجل)", max_digits=14, decimal_places=2, null=True, blank=True)
    active = models.BooleanField("نشط", default=True)
    merged_into = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, verbose_name="دُمج في")

    class Meta:
        verbose_name = "طرف"
        verbose_name_plural = "الأطراف (عملاء/موردين/موظفين)"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Journal(models.Model):
    """A document type with its own numbering sequence."""

    code = models.CharField("الكود", max_length=10, unique=True)
    name = models.CharField("الاسم", max_length=80)
    prefix = models.CharField("بادئة الترقيم", max_length=12)
    yearly_reset = models.BooleanField("الترقيم يبدأ من جديد كل سنة", default=True)
    padding = models.PositiveSmallIntegerField("عدد الخانات", default=5)
    start_after = models.PositiveIntegerField("يبدأ بعد رقم", default=0, help_text="لاستكمال ترقيم السيستم القديم")

    class Meta:
        verbose_name = "نوع مستند / يومية"
        verbose_name_plural = "أنواع المستندات والترقيم"
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} - {self.name}"


class Sequence(models.Model):
    journal = models.ForeignKey(Journal, on_delete=models.PROTECT)
    year = models.PositiveIntegerField(default=0)
    last = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = [("journal", "year")]


class Period(models.Model):
    year = models.PositiveIntegerField("السنة")
    month = models.PositiveSmallIntegerField("الشهر")
    status = models.CharField("الحالة", max_length=6, default="open", choices=[("open", "مفتوحة"), ("closed", "مقفولة")])
    closed_at = models.DateTimeField("وقت القفل", null=True, blank=True)
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, verbose_name="قفلها")

    class Meta:
        verbose_name = "فترة محاسبية"
        verbose_name_plural = "الفترات المحاسبية"
        unique_together = [("year", "month")]
        ordering = ["-year", "-month"]

    def __str__(self):
        return f"{self.year}-{self.month:02d} ({self.get_status_display()})"


class JournalEntry(models.Model):
    STATES = [("draft", "مسودة"), ("posted", "مرحّل"), ("reversed", "معكوس")]
    journal = models.ForeignKey(Journal, on_delete=models.PROTECT, verbose_name="النوع")
    number = models.CharField("الرقم", max_length=30, blank=True, db_index=True)
    date = models.DateField("التاريخ", db_index=True)
    description = models.CharField("البيان", max_length=300, blank=True)
    state = models.CharField("الحالة", max_length=8, choices=STATES, default="draft", db_index=True)
    reversal_of = models.OneToOneField("self", null=True, blank=True, on_delete=models.PROTECT, related_name="reversed_by", verbose_name="عكس للقيد")
    source = models.CharField("المصدر", max_length=60, blank=True, help_text="مثل treasury.voucher:15")
    legacy_ref = models.CharField("مرجع قديم", max_length=40, blank=True, db_index=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    posted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    posted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "قيد يومية"
        verbose_name_plural = "قيود اليومية"
        ordering = ["-date", "-id"]

    def __str__(self):
        return f"{self.number or 'مسودة'} {self.date}"

    @property
    def total_debit(self):
        return sum((l.debit for l in self.lines.all()), ZERO)

    @property
    def total_credit(self):
        return sum((l.credit for l in self.lines.all()), ZERO)

    def save(self, *args, **kwargs):
        if self.pk and not getattr(self, "_allow_posted_save", False):
            old = JournalEntry.objects.filter(pk=self.pk).values("state").first()
            if old and old["state"] != "draft":
                raise ValidationError("القيد المرحّل لا يُعدَّل — التصحيح بقيد عكسي")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.state != "draft":
            raise ValidationError("القيد المرحّل لا يُحذف — التصحيح بقيد عكسي")
        super().delete(*args, **kwargs)


class JournalLine(models.Model):
    entry = models.ForeignKey(JournalEntry, on_delete=models.CASCADE, related_name="lines")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, verbose_name="الحساب")
    partner = models.ForeignKey(Partner, null=True, blank=True, on_delete=models.PROTECT, verbose_name="الطرف")
    description = models.CharField("البيان", max_length=300, blank=True)
    debit = models.DecimalField("مدين", max_digits=18, decimal_places=2, default=ZERO)
    credit = models.DecimalField("دائن", max_digits=18, decimal_places=2, default=ZERO)
    currency = models.CharField("العملة", max_length=3, default="EGP")
    amount_currency = models.DecimalField("المبلغ بالعملة", max_digits=18, decimal_places=2, null=True, blank=True)
    rate = models.DecimalField("سعر الصرف", max_digits=14, decimal_places=6, default=Decimal("1"))
    dim1 = models.ForeignKey(DimensionValue, null=True, blank=True, on_delete=models.PROTECT, related_name="lines_dim1", verbose_name="مركز التكلفة")
    dim2 = models.ForeignKey(DimensionValue, null=True, blank=True, on_delete=models.PROTECT, related_name="lines_dim2", verbose_name="المعدة")
    dim3 = models.ForeignKey(DimensionValue, null=True, blank=True, on_delete=models.PROTECT, related_name="lines_dim3", verbose_name="البعد الثالث")
    quantity = models.DecimalField("الكمية", max_digits=18, decimal_places=3, null=True, blank=True)

    class Meta:
        verbose_name = "سطر قيد"
        verbose_name_plural = "سطور القيود"
        indexes = [models.Index(fields=["account", "entry"]), models.Index(fields=["dim1"]), models.Index(fields=["partner"])]

    def __str__(self):
        return f"{self.account.code} {self.debit or -self.credit}"

    @property
    def balance(self):
        return self.debit - self.credit

    def save(self, *args, **kwargs):
        if self.entry_id and not getattr(self, "_allow_posted_save", False):
            st = JournalEntry.objects.filter(pk=self.entry_id).values_list("state", flat=True).first()
            if st and st != "draft":
                raise ValidationError("سطور القيد المرحّل لا تُعدَّل")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.entry.state != "draft":
            raise ValidationError("سطور القيد المرحّل لا تُحذف")
        super().delete(*args, **kwargs)
