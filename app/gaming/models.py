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
                                     help_text="متاح = نفس السعر")
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
                                help_text="متاح = طلب تيك أواي / بوفيه بدون جهاز")
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
