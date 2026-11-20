"""Clinic: patients and their file, bookings / the waiting queue, visits (diagnosis, prescription, services and consumables),
doctors paid by a percentage. A paid visit becomes a normal sales invoice (cash drawer, day close, reports, mobile)."""
from decimal import Decimal

from django.conf import settings
from django.db import models


class Doctor(models.Model):
    name = models.CharField("اسم الطبيب", max_length=100)
    specialty = models.CharField("التخصص", max_length=100, blank=True)
    phone = models.CharField("التليفون", max_length=40, blank=True)
    share_pct = models.DecimalField("نسبة الطبيب %", max_digits=5, decimal_places=2, default=0,
                                    help_text="من قيمة الكشف والخدمات بعد الخصم — 0 = الطبيب صاحب العيادة أو براتب ثابت")
    partner = models.OneToOneField("ledger.Partner", null=True, blank=True, on_delete=models.PROTECT, related_name="doctor")
    user = models.OneToOneField(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="clinic_doctor",
                                verbose_name="حساب الدخول", help_text="المستخدم الذي يفتح «شاشة الطبيب» الخاصة به")
    hours = models.CharField("مواعيد العيادة", max_length=200, blank=True, help_text="تُطبع على الروشتة، مثلًا: السبت إلى الخميس 5 – 10 مساءً")
    order = models.PositiveSmallIntegerField("الترتيب", default=0)
    active = models.BooleanField("شغال", default=True)

    class Meta:
        verbose_name = "طبيب"
        verbose_name_plural = "الأطباء"
        ordering = ["order", "id"]

    def __str__(self):
        return f"د. {self.name}"


class Service(models.Model):
    """A visit type / procedure (كشف، استشارة، حشو، جلسة…): sold as a service item; its consumables are the item's recipe."""

    item = models.OneToOneField("inventory.Item", on_delete=models.CASCADE, related_name="clinic_service")
    doctor = models.ForeignKey(Doctor, null=True, blank=True, on_delete=models.SET_NULL, related_name="services", verbose_name="الطبيب",
                               help_text="متاح = أي طبيب")
    share_pct = models.DecimalField("نسبة الطبيب % لهذه الخدمة", max_digits=5, decimal_places=2, null=True, blank=True,
                                    help_text="متاح = نسبة الطبيب العادية")
    minutes = models.PositiveSmallIntegerField("المدة (دقيقة)", default=15)

    class Meta:
        verbose_name = "خدمة"
        verbose_name_plural = "الخدمات والكشوفات"

    def __str__(self):
        return self.item.name


class InsuranceCompany(models.Model):
    """A medical insurance company / syndicate / employer contract: it pays its share of the patient's bill later (its account
    is a customer in the books — its statement and claims come from there)."""

    name = models.CharField("اسم الشركة / الجهة", max_length=120)
    coverage_pct = models.DecimalField("نسبة التحمل %", max_digits=5, decimal_places=2, default=Decimal("80"),
                                       help_text="النسبة المئوية التي تدفعها الشركة من الحساب — ويدفع المريض الباقي")
    max_per_visit = models.DecimalField("أقصى مبلغ للزيارة", max_digits=12, decimal_places=2, null=True, blank=True,
                                        help_text="متاح = من غير حد")
    phone = models.CharField("التليفون", max_length=40, blank=True)
    contact = models.CharField("المسؤول", max_length=100, blank=True)
    address = models.CharField("العنوان", max_length=200, blank=True)
    tax_id = models.CharField("الرقم الضريبي", max_length=40, blank=True)
    contract_no = models.CharField("رقم التعاقد", max_length=60, blank=True)
    notes = models.CharField("ملاحظات (ما يغطيه وما لا يغطيه)", max_length=300, blank=True)
    partner = models.OneToOneField("ledger.Partner", null=True, blank=True, on_delete=models.PROTECT, related_name="insurance_company")
    active = models.BooleanField("شغالة", default=True)

    class Meta:
        verbose_name = "شركة تأمين طبي"
        verbose_name_plural = "شركات التأمين الطبي"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Patient(models.Model):
    GENDERS = [("m", "ذكر"), ("f", "أنثى")]
    number = models.PositiveIntegerField("رقم الملف", unique=True)
    name = models.CharField("اسم المريض", max_length=120, db_index=True)
    phone = models.CharField("الموبايل", max_length=40, blank=True, db_index=True)
    gender = models.CharField("النوع", max_length=1, choices=GENDERS, blank=True)
    birth_year = models.PositiveSmallIntegerField("سنة الميلاد", null=True, blank=True)
    address = models.CharField("العنوان", max_length=200, blank=True)
    medical_notes = models.TextField("أمراض مزمنة / حساسية / ملاحظات", blank=True)
    partner = models.OneToOneField("ledger.Partner", null=True, blank=True, on_delete=models.PROTECT, related_name="patient",
                                   help_text="يُنشأ تلقائيًا عند أول دفع آجل")
    insurance = models.ForeignKey(InsuranceCompany, null=True, blank=True, on_delete=models.PROTECT, related_name="patients",
                                  verbose_name="شركة التأمين")
    insurance_no = models.CharField("الرقم التأميني", max_length=60, blank=True)
    insurance_holder = models.CharField("المؤمن عليه / جهة العمل", max_length=120, blank=True)
    insurance_expiry = models.DateField("البطاقة صالحة حتى", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "مريض"
        verbose_name_plural = "المرضى"
        ordering = ["-number"]

    def __str__(self):
        return f"{self.name} ({self.number})"

    @property
    def age(self):
        if not self.birth_year:
            return None
        from django.utils import timezone

        return timezone.localdate().year - self.birth_year


class Visit(models.Model):
    STATES = [("open", "مفتوحة"), ("paid", "حُوسبت"), ("cancelled", "ملغية")]
    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name="visits", verbose_name="المريض")
    doctor = models.ForeignKey(Doctor, null=True, blank=True, on_delete=models.PROTECT, related_name="visits", verbose_name="الطبيب")
    opened_at = models.DateTimeField("الوقت")
    complaint = models.TextField("الشكوى", blank=True)
    diagnosis = models.TextField("التشخيص", blank=True)
    prescription = models.TextField("الروشتة / العلاج", blank=True)
    notes = models.TextField("ملاحظات الطبيب", blank=True)
    next_visit = models.DateField("ميعاد المتابعة", null=True, blank=True)
    weight = models.DecimalField("الوزن (كجم)", max_digits=5, decimal_places=1, null=True, blank=True)
    state = models.CharField("الحالة", max_length=10, choices=STATES, default="open")
    discount = models.DecimalField("خصم", max_digits=12, decimal_places=2, default=0)
    total = models.DecimalField("الإجمالي", max_digits=12, decimal_places=2, default=0)
    doctor_share = models.DecimalField("نسبة الطبيب", max_digits=12, decimal_places=2, default=0)
    order = models.ForeignKey("pos.Order", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    share_entry = models.ForeignKey("ledger.JournalEntry", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    edits = models.PositiveSmallIntegerField(default=0)
    by = models.CharField("سجّلها", max_length=100, blank=True)
    paid_by = models.CharField("حاسب", max_length=100, blank=True)
    note = models.CharField("سبب الإلغاء / التعديل", max_length=300, blank=True)

    class Meta:
        verbose_name = "زيارة"
        verbose_name_plural = "الزيارات"
        ordering = ["-opened_at"]

    def __str__(self):
        return f"زيارة {self.pk} — {self.patient.name}"

    @property
    def subtotal(self):
        return sum((ln.amount for ln in self.lines.all()), Decimal(0))

    @property
    def unbilled(self):
        return sum((ln.amount for ln in self.lines.all() if not ln.payment_id), Decimal(0))

    @property
    def live_payments(self):
        return [p for p in self.payments.all() if not p.cancelled]


class VisitPayment(models.Model):
    """One bill of a visit (the booking receipt, then any extra service added by the doctor): a sales invoice + the doctor's share."""

    visit = models.ForeignKey(Visit, on_delete=models.CASCADE, related_name="payments")
    order = models.ForeignKey("pos.Order", on_delete=models.PROTECT, related_name="+")
    share_entry = models.ForeignKey("ledger.JournalEntry", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    share = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    kind = models.CharField(max_length=10, choices=[("visit", "حساب الزيارة"), ("booking", "حجز مدفوع"), ("extra", "حساب إضافي")], default="visit")
    insurance = models.ForeignKey(InsuranceCompany, null=True, blank=True, on_delete=models.PROTECT, related_name="payments")
    insurance_amount = models.DecimalField("على شركة التأمين", max_digits=12, decimal_places=2, default=0)
    cancelled = models.BooleanField(default=False)
    by = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]


class VisitLine(models.Model):
    visit = models.ForeignKey(Visit, on_delete=models.CASCADE, related_name="lines")
    payment = models.ForeignKey(VisitPayment, null=True, blank=True, on_delete=models.SET_NULL, related_name="lines")
    item = models.ForeignKey("inventory.Item", on_delete=models.PROTECT, related_name="+", verbose_name="الخدمة / الصنف")
    qty = models.DecimalField("الكمية", max_digits=10, decimal_places=3, default=Decimal(1))
    price = models.DecimalField("السعر", max_digits=12, decimal_places=2)
    discount = models.DecimalField("خصم", max_digits=12, decimal_places=2, default=0)

    class Meta:
        ordering = ["id"]

    @property
    def amount(self):
        return self.qty * self.price - self.discount


class Appointment(models.Model):
    STATES = [("booked", "محجوز"), ("waiting", "وصل — ينتظر"), ("inside", "عند الطبيب"), ("done", "انتهى"),
              ("cancelled", "أُلغي"), ("noshow", "لم يحضر")]
    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name="appointments", verbose_name="المريض")
    doctor = models.ForeignKey(Doctor, null=True, blank=True, on_delete=models.PROTECT, related_name="appointments", verbose_name="الطبيب")
    service = models.ForeignKey(Service, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", verbose_name="الخدمة")
    date = models.DateField("اليوم", db_index=True)
    time = models.TimeField("الساعة", null=True, blank=True)
    queue_no = models.PositiveSmallIntegerField("الدور", default=0)
    state = models.CharField("الحالة", max_length=10, choices=STATES, default="booked")
    note = models.CharField("ملاحظة", max_length=200, blank=True)
    visit = models.OneToOneField(Visit, null=True, blank=True, on_delete=models.SET_NULL, related_name="appointment")
    created_by = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "حجز"
        verbose_name_plural = "الحجوزات"
        ordering = ["date", "queue_no", "time", "id"]

    def __str__(self):
        return f"{self.patient.name} {self.date:%d/%m} #{self.queue_no}"


class PatientFile(models.Model):
    """A lab result, an x-ray, a report… attached to the patient's file. Kept inside the database so it is in every backup."""

    KINDS = [("lab", "تحليل"), ("xray", "أشعة"), ("rx", "روشتة"), ("report", "تقرير"), ("other", "أخرى")]
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="files", verbose_name="المريض")
    visit = models.ForeignKey(Visit, null=True, blank=True, on_delete=models.SET_NULL, related_name="files", verbose_name="الزيارة")
    kind = models.CharField("النوع", max_length=8, choices=KINDS, default="lab")
    title = models.CharField("الوصف", max_length=150, blank=True)
    taken_on = models.DateField("تاريخ التحليل / الأشعة", null=True, blank=True)
    filename = models.CharField(max_length=200)
    content_type = models.CharField(max_length=80)
    size = models.PositiveIntegerField(default=0)
    data = models.BinaryField()
    by = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "ملف مريض"
        verbose_name_plural = "ملفات المرضى (تحاليل وأشعة)"
        ordering = ["-taken_on", "-created_at"]

    def __str__(self):
        return self.title or self.filename

    @property
    def is_image(self):
        return self.content_type.startswith("image/")
