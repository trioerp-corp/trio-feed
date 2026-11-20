"""Gaming hall: PlayStation devices / billiard tables rented by the hour, with food & drinks on the same bill."""
from decimal import Decimal

from django.conf import settings
from django.db import models


class Station(models.Model):
    KINDS = [("ps5", "بلايستيشن 5"), ("ps4", "بلايستيشن 4"), ("billiard", "ترابيزة بلياردو"), ("pingpong", "بينج بونج"),
             ("vr", "VR"), ("pc", "كمبيوتر"), ("other", "أخرى")]
    name = models.CharField("الاسم", max_length=40, help_text="مثلًا: جهاز 1، ترابيزة 2، VIP")
    kind = models.CharField("النوع", max_length=10, choices=KINDS, default="ps5")
    rate = models.DecimalField("سعر الساعة (فردي)", max_digits=10, decimal_places=2)
    rate_multi = models.DecimalField("سعر الساعة (مالتي / زوجي)", max_digits=10, decimal_places=2, null=True, blank=True,
                                     help_text="اتركه فارغًا إذا كان السعر واحدًا")
    order = models.PositiveSmallIntegerField("الترتيب", default=0)
    active = models.BooleanField("شغال", default=True)

    class Meta:
        verbose_name = "جهاز / ترابيزة"
        verbose_name_plural = "الأجهزة والترابيزات"
        ordering = ["order", "id"]

    def __str__(self):
        return self.name

    def rate_for(self, mode):
        return self.rate_multi if mode == "multi" and self.rate_multi else self.rate


class PlaySession(models.Model):
    STATES = [("open", "شغال"), ("closed", "حُوسب"), ("void", "ملغي")]
    station = models.ForeignKey(Station, null=True, blank=True, on_delete=models.PROTECT, related_name="sessions", verbose_name="الجهاز",
                                help_text="فارغ = طلب تيك أواي / بوفيه بدون جهاز")
    customer = models.CharField("اسم العميل", max_length=80, blank=True)
    opened_at = models.DateTimeField("بدأ")
    closed_at = models.DateTimeField("أُغلق", null=True, blank=True)
    planned_minutes = models.PositiveIntegerField("وقت محجوز (دقيقة)", null=True, blank=True, help_text="تنبّه الشاشة عند انتهائه")
    state = models.CharField("الحالة", max_length=6, choices=STATES, default="open")
    opened_by = models.CharField("فتحه", max_length=100, blank=True)
    closed_by = models.CharField("قفله", max_length=100, blank=True)
    minutes = models.PositiveIntegerField("دقائق اللعب الفعلية", default=0)
    billed_minutes = models.PositiveIntegerField("الدقائق المحسوبة", default=0)
    time_amount = models.DecimalField("حساب الوقت", max_digits=12, decimal_places=2, default=0)
    items_amount = models.DecimalField("الطلبات", max_digits=12, decimal_places=2, default=0)
    discount = models.DecimalField("خصم", max_digits=12, decimal_places=2, default=0)
    total = models.DecimalField("الإجمالي", max_digits=12, decimal_places=2, default=0)
    order = models.OneToOneField("pos.Order", null=True, blank=True, on_delete=models.PROTECT, related_name="play_session")
    note = models.CharField("ملاحظة", max_length=200, blank=True)
    daily_no = models.PositiveSmallIntegerField("رقم الطلب اليوم", null=True, blank=True)
    discount_by = models.CharField("وافق على الخصم", max_length=100, blank=True)
    hall_customer = models.ForeignKey("HallCustomer", null=True, blank=True, on_delete=models.PROTECT, related_name="sessions",
                                      verbose_name="عميل الباقة")
    prepaid_minutes = models.PositiveIntegerField("دقائق من رصيد الباقة", default=0)
    prepaid_value = models.DecimalField("قيمة الدقائق من الباقة", max_digits=12, decimal_places=2, default=0)

    class Meta:
        verbose_name = "جلسة لعب"
        verbose_name_plural = "جلسات اللعب"
        ordering = ["-opened_at"]

    def __str__(self):
        return f"{self.station or 'تيك أواي'} {self.opened_at:%d/%m %H:%M}"


class PlaySegment(models.Model):
    """A piece of a session at one rate: switching single ↔ multi or moving to another device starts a new segment."""

    session = models.ForeignKey(PlaySession, on_delete=models.CASCADE, related_name="segments")
    station = models.ForeignKey(Station, on_delete=models.PROTECT, related_name="+")
    mode = models.CharField(max_length=6, choices=[("single", "فردي"), ("multi", "مالتي")], default="single")
    rate = models.DecimalField(max_digits=10, decimal_places=2)
    start = models.DateTimeField()
    end = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["start", "id"]


class PlayItem(models.Model):
    session = models.ForeignKey(PlaySession, on_delete=models.CASCADE, related_name="items")
    item = models.ForeignKey("inventory.Item", on_delete=models.PROTECT, related_name="+")
    qty = models.DecimalField(max_digits=10, decimal_places=3, default=Decimal(1))
    price = models.DecimalField(max_digits=10, decimal_places=2)
    added_at = models.DateTimeField(auto_now_add=True)
    by = models.CharField(max_length=100, blank=True)

    class Meta:
        ordering = ["added_at", "id"]


class HallSettings(models.Model):
    """The owner's rules for the hall worker (one row)."""

    max_discount_pct = models.DecimalField("أقصى خصم للعامل بدون صاحب المحل (%)", max_digits=5, decimal_places=2, default=Decimal("10"),
                                           help_text="خصم أكبر من هذه النسبة من الحساب يحتاج إلى اسم صاحب المحل وكلمة سره")

    class Meta:
        verbose_name = "إعدادات الصالة"
        verbose_name_plural = "إعدادات الصالة"

    @classmethod
    def get(cls):
        return cls.objects.order_by("pk").first() or cls.objects.create()


class Reservation(models.Model):
    """A booking of a device (or «any PS5») for a time and a duration: shown on the hall screen, warns a walk-in that would overlap."""

    STATES = [("booked", "محجوز"), ("arrived", "حضر"), ("noshow", "لم يحضر"), ("cancelled", "أُلغي")]
    station = models.ForeignKey(Station, null=True, blank=True, on_delete=models.PROTECT, related_name="reservations", verbose_name="الجهاز",
                                help_text="فارغ = أي جهاز من النوع المختار")
    kind = models.CharField("النوع", max_length=10, choices=Station.KINDS, default="ps5")
    customer = models.CharField("اسم العميل", max_length=80)
    phone = models.CharField("الموبايل", max_length=40, blank=True)
    start = models.DateTimeField("الموعد", db_index=True)
    minutes = models.PositiveIntegerField("المدة (دقيقة)", default=60)
    state = models.CharField("الحالة", max_length=10, choices=STATES, default="booked")
    session = models.OneToOneField(PlaySession, null=True, blank=True, on_delete=models.SET_NULL, related_name="reservation")
    note = models.CharField("ملاحظة", max_length=200, blank=True)
    created_by = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "حجز جهاز"
        verbose_name_plural = "حجوزات الأجهزة"
        ordering = ["start", "id"]

    def __str__(self):
        return f"{self.customer} {self.start:%d/%m %H:%M}"

    @property
    def end(self):
        from datetime import timedelta

        return self.start + timedelta(minutes=self.minutes)

    @property
    def where(self):
        return self.station.name if self.station_id else "أي " + dict(Station.KINDS).get(self.kind, self.kind)


class Package(models.Model):
    """Prepaid hours («10 ساعات بـ 300 ج»): sold at the hall as a normal sale of its service item; gives the customer minutes."""

    name = models.CharField("اسم الباقة", max_length=60)
    minutes = models.PositiveIntegerField("عدد الدقائق")
    price = models.DecimalField("السعر", max_digits=10, decimal_places=2)
    kind = models.CharField("على أجهزة", max_length=10, choices=Station.KINDS, blank=True, help_text="فارغ = أي جهاز")
    item = models.OneToOneField("inventory.Item", on_delete=models.PROTECT, related_name="play_package")
    active = models.BooleanField("متاحة", default=True)

    class Meta:
        verbose_name = "باقة ساعات"
        verbose_name_plural = "باقات الساعات"
        ordering = ["kind", "minutes", "id"]

    def __str__(self):
        return self.name

    @property
    def hours(self):
        return round(self.minutes / 60, 2) if self.minutes % 60 else self.minutes // 60

    @property
    def kind_name(self):
        return dict(Station.KINDS).get(self.kind, "أي جهاز") if self.kind else "أي جهاز"


class HallCustomer(models.Model):
    """A regular of the hall, known by his mobile number: holds his prepaid minutes."""

    name = models.CharField("الاسم", max_length=80)
    phone = models.CharField("الموبايل", max_length=40, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "عميل الصالة"
        verbose_name_plural = "عملاء الصالة"
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.phone})"


class MinuteMove(models.Model):
    """+ minutes bought with a package, − minutes used on a session (kind "" = any device)."""

    customer = models.ForeignKey(HallCustomer, on_delete=models.PROTECT, related_name="moves")
    kind = models.CharField(max_length=10, blank=True)
    minutes = models.IntegerField()
    package = models.ForeignKey(Package, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    session = models.ForeignKey(PlaySession, null=True, blank=True, on_delete=models.PROTECT, related_name="minute_moves")
    order = models.ForeignKey("pos.Order", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    at = models.DateTimeField(auto_now_add=True)
    by = models.CharField(max_length=100, blank=True)

    class Meta:
        ordering = ["-at", "-id"]
