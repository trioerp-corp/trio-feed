"""Point of sale (constitution XVII). Disabled unless CompanySettings.pos_enabled."""
import secrets

from django.conf import settings
from django.db import models

from inventory.models import Item, StockMove, Warehouse
from ledger.models import DimensionValue, JournalEntry
from treasury.models import CashBox


def _token():
    return secrets.token_urlsafe(24)


class Branch(models.Model):
    code = models.CharField("كود الفرع", max_length=6, unique=True)
    name = models.CharField("اسم الفرع", max_length=100)
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, verbose_name="مخزن الفرع")
    cashbox = models.ForeignKey(CashBox, on_delete=models.PROTECT, verbose_name="خزنة الفرع")
    cost_center = models.ForeignKey(DimensionValue, on_delete=models.PROTECT, verbose_name="مركز تكلفة الفرع", limit_choices_to={"dimension__number": 1})
    receipt_footer = models.CharField("تذييل الإيصال", max_length=200, blank=True)
    address = models.CharField("العنوان", max_length=200, blank=True)
    phone = models.CharField("التليفون", max_length=40, blank=True)
    active = models.BooleanField("نشط", default=True)

    class Meta:
        verbose_name = "فرع بيع"
        verbose_name_plural = "فروع البيع"

    def __str__(self):
        return self.name


class Device(models.Model):
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="devices", verbose_name="الفرع")
    code = models.CharField("كود الجهاز", max_length=6)
    name = models.CharField("اسم الجهاز", max_length=60)
    token = models.CharField("مفتاح الجهاز", max_length=64, unique=True, default=_token, editable=False)
    active = models.BooleanField("نشط", default=True)
    last_sync = models.DateTimeField("آخر مزامنة", null=True, blank=True)
    stuck = models.JSONField("ورق مرفوض من آخر مزامنة", default=dict, blank=True)  # {uid: reason} — shown to the manager
    next_no = models.PositiveIntegerField("آخر رقم فاتورة محجوز", default=0)  # receipt numbers handed to the till in blocks

    class Meta:
        verbose_name = "جهاز كاشير"
        verbose_name_plural = "أجهزة الكاشير"
        unique_together = [("branch", "code")]

    def __str__(self):
        return f"{self.branch.code}-{self.code} {self.name}"


class Shift(models.Model):
    uid = models.CharField(max_length=60, unique=True)
    device = models.ForeignKey(Device, on_delete=models.PROTECT, related_name="shifts")
    cashier = models.CharField("الكاشير", max_length=100)
    verified = models.BooleanField("تم التأكيد بكلمة السر", default=False)
    opened_at = models.DateTimeField("الفتح")
    opening_float = models.DecimalField("رصيد الفتح", max_digits=14, decimal_places=2, default=0)
    closed_at = models.DateTimeField("القفل", null=True, blank=True)
    counted_cash = models.DecimalField("الجرد عند القفل", max_digits=14, decimal_places=2, null=True, blank=True)
    expected_cash = models.DecimalField("المتوقع", max_digits=14, decimal_places=2, null=True, blank=True)
    difference_entry = models.OneToOneField(JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="+")

    class Meta:
        verbose_name = "وردية"
        verbose_name_plural = "الورديات"
        ordering = ["-opened_at"]


class Order(models.Model):
    uid = models.CharField("المعرف الفريد", max_length=60, unique=True, help_text="الفرع-الجهاز-المسلسل، يُولَّد على الجهاز")
    device = models.ForeignKey(Device, on_delete=models.PROTECT, related_name="orders")
    shift = models.ForeignKey(Shift, null=True, blank=True, on_delete=models.PROTECT, related_name="orders")
    created_at_device = models.DateTimeField("وقت البيع على الجهاز")
    received_at = models.DateTimeField("وقت الوصول للسيرفر", auto_now_add=True)
    total = models.DecimalField("الإجمالي", max_digits=14, decimal_places=2)
    vat = models.DecimalField("القيمة المضافة", max_digits=14, decimal_places=2, default=0)
    paid_cash = models.DecimalField("نقدي", max_digits=14, decimal_places=2, default=0)
    paid_card = models.DecimalField("فيزا/محفظة", max_digits=14, decimal_places=2, default=0)
    paid_credit = models.DecimalField("آجل", max_digits=14, decimal_places=2, default=0)
    paid_rider = models.DecimalField("مع الطيار (دليفري)", max_digits=14, decimal_places=2, default=0)
    paid_insta = models.DecimalField("إنستاباي", max_digits=14, decimal_places=2, default=0)  # 1.25: a transfer to the shop's InstaPay — not cash
    customer = models.ForeignKey("ledger.Partner", null=True, blank=True, on_delete=models.PROTECT, related_name="+", verbose_name="العميل (آجل)")
    is_return = models.BooleanField("مرتجع", default=False)
    flags = models.CharField("تنبيهات للمراجعة", max_length=300, blank=True)
    cancelled = models.BooleanField("ملغية", default=False)
    shift_seq = models.PositiveIntegerField("رقم الفاتورة في الوردية", null=True, blank=True)
    entry = models.OneToOneField(JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    stock_move = models.OneToOneField(StockMove, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    raw = models.JSONField("البيانات كما وصلت", default=dict)

    class Meta:
        verbose_name = "فاتورة بيع (نقطة بيع)"
        verbose_name_plural = "فواتير نقاط البيع"
        ordering = ["-created_at_device"]

    def __str__(self):
        return self.uid


class OrderLine(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey(Item, on_delete=models.PROTECT)
    qty = models.DecimalField(max_digits=14, decimal_places=3)
    price = models.DecimalField(max_digits=14, decimal_places=2)
    discount = models.DecimalField(max_digits=14, decimal_places=2, default=0)


class CashMove(models.Model):
    """Money in/out that is not a sale: owner drawings, running expenses, wages and advances, supplier payments.
    From the cashier drawer (POS, works offline) or from the back office (drawer, wallet, bank or the owner's own pocket)."""

    KINDS = [("drawing", "مسحوبات صاحب المحل"), ("deposit", "إيداع من صاحب المحل"), ("expense", "مصروف تشغيل"),
             ("wage", "أجر / مرتب موظف"), ("advance", "سلفة موظف"), ("supplier", "سداد مورد"),
             ("doctor", "صرف مستحقات طبيب"), ("collect", "تحصيل من عميل آجل / مريض / شركة تأمين"),
             ("delivery", "تحصيل دليفري من الطيار")]
    uid = models.CharField(max_length=60, unique=True)
    kind = models.CharField("النوع", max_length=10, choices=KINDS)
    date = models.DateField("التاريخ")
    shift = models.ForeignKey(Shift, null=True, blank=True, on_delete=models.PROTECT, related_name="cash_moves")
    from_owner = models.BooleanField("دُفع من مال صاحب المحل الخاص", default=False)
    cash_effect = models.DecimalField("أثرها على الدرج/الخزينة", max_digits=14, decimal_places=2, default=0)
    amount = models.DecimalField("المبلغ", max_digits=14, decimal_places=2)
    deduct_advance = models.DecimalField("خصم سلفة", max_digits=14, decimal_places=2, default=0)
    deduct_penalty = models.DecimalField("خصم / جزاء", max_digits=14, decimal_places=2, default=0)
    partner = models.ForeignKey("ledger.Partner", null=True, blank=True, on_delete=models.PROTECT, related_name="+", verbose_name="الطرف")
    account = models.ForeignKey("ledger.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+", verbose_name="بند المصروف")
    note = models.CharField("بيان", max_length=200, blank=True)
    entry = models.ForeignKey(JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    by = models.CharField("بواسطة", max_length=100, blank=True)
    cancelled = models.BooleanField("ملغية", default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "حركة نقدية"
        verbose_name_plural = "الحركات النقدية (مسحوبات، مصروفات، أجور، موردين)"
        ordering = ["-date", "-id"]


class ChangeRequest(models.Model):
    """A cashier cannot cancel or change a posted movement: he asks, and the owner approves or rejects."""

    KINDS = [("void", "إلغاء"), ("edit", "تعديل")]
    STATES = [("pending", "بانتظار الاعتماد"), ("approved", "اعتُمد"), ("rejected", "رُفض")]
    uid = models.CharField(max_length=60, unique=True)
    kind = models.CharField("الطلب", max_length=6, choices=KINDS)
    target = models.CharField("الحركة", max_length=60, help_text="رقم الفاتورة أو الحركة")
    reason = models.CharField("السبب / التعديل المطلوب", max_length=300)
    requested_by = models.CharField("طلبه", max_length=100)
    device = models.ForeignKey(Device, null=True, blank=True, on_delete=models.SET_NULL)
    state = models.CharField("الحالة", max_length=10, choices=STATES, default="pending")
    decided_by = models.CharField("قرار", max_length=100, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField("ملاحظة صاحب المحل", max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "طلب إلغاء / تعديل"
        verbose_name_plural = "طلبات الإلغاء والتعديل"
        ordering = ["-created_at"]


class Promotion(models.Model):
    """Shop offers, applied by the cashier screen automatically while they are active."""

    KINDS = [("percent", "خصم نسبة %"), ("amount", "خصم مبلغ على الوحدة"), ("bxgy", "اشترِ كمية والزيادة هدية"), ("qty_price", "سعر جملة من كمية")]
    name = models.CharField("اسم العرض", max_length=100)
    kind = models.CharField("النوع", max_length=10, choices=KINDS)
    item = models.ForeignKey("inventory.Item", on_delete=models.CASCADE, related_name="promotions", verbose_name="الصنف")
    value = models.DecimalField("القيمة", max_digits=10, decimal_places=2, default=0,
                                help_text="النسبة %، أو الخصم بالجنيه، أو سعر الوحدة للجملة")
    min_qty = models.DecimalField("من كمية", max_digits=10, decimal_places=3, default=1)
    free_qty = models.DecimalField("الهدية", max_digits=10, decimal_places=3, default=0, help_text="لعرض «اشترِ 2 والثالث هدية»: من كمية 2 والهدية 1")
    start = models.DateField("من")
    end = models.DateField("حتى")
    active = models.BooleanField("شغال", default=True)

    class Meta:
        verbose_name = "عرض"
        verbose_name_plural = "العروض والخصومات"
        ordering = ["-start"]

    def __str__(self):
        return self.name


class ShopPartner(models.Model):
    """A partner (شريك) in the business and his share of the profit. His drawings are tagged with his ledger Partner."""

    partner = models.OneToOneField("ledger.Partner", on_delete=models.PROTECT, related_name="shop_partner", verbose_name="الشريك")
    share = models.DecimalField("نسبته من الربح %", max_digits=6, decimal_places=2)
    active = models.BooleanField("نشط", default=True)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        verbose_name = "شريك"
        verbose_name_plural = "الشركاء ونسبهم"
        ordering = ["order", "id"]

    def __str__(self):
        return f"{self.partner.name} ({self.share}%)"


class Staff(models.Model):
    """Who a user is in the business: his branch (empty = all branches), his job title, and what he may do."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="staff")
    branch = models.ForeignKey(Branch, null=True, blank=True, on_delete=models.SET_NULL, related_name="staff", verbose_name="الفرع")
    role = models.CharField("القالب", max_length=20, default="custom")
    title = models.CharField("المسمى الوظيفي", max_length=60, blank=True)
    caps = models.JSONField("الصلاحيات", default=list, blank=True)
    email = models.CharField("بريد التقارير", max_length=150, blank=True)
    phone = models.CharField("موبايل / واتساب", max_length=40, blank=True)

    class Meta:
        verbose_name = "موظف"
        verbose_name_plural = "الموظفين والصلاحيات"

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.username} — {self.title}"


def _qr():
    return secrets.token_urlsafe(9)[:10].replace("-", "x").replace("_", "y")


class DiningTable(models.Model):
    """A table in a café / restaurant (1.23): the waiter keeps adding to its bill until the customer pays.
    `qr` goes in the table's QR code so a customer's own order lands on this table."""

    name = models.CharField("اسم الترابيزة", max_length=40, help_text="مثلًا: ترابيزة 1، تراس 2، VIP")
    area = models.CharField("المكان", max_length=40, blank=True, help_text="صالة، تراس، دور علوي… (اختياري)")
    seats = models.PositiveSmallIntegerField("عدد الكراسي", null=True, blank=True)
    order = models.PositiveSmallIntegerField("الترتيب", default=0)
    active = models.BooleanField("تعمل", default=True)
    qr = models.CharField(max_length=12, unique=True, default=_qr, editable=False)

    class Meta:
        verbose_name = "ترابيزة"
        verbose_name_plural = "الترابيزات"
        ordering = ["order", "id"]

    def __str__(self):
        return self.name


class TableTab(models.Model):
    """The open bill of a table. `lines`: [{id, code, name, price, qty, cat, vr, adds:[{code,name,p,v}], note, by, at, src, batch}],
    `batches`: [{n, at, by, src, printed}] — one per «إرسال للمطبخ» or customer order (a kitchen ticket each)."""

    STATES = [("open", "مفتوحة"), ("paid", "حُوسبت"), ("void", "أُلغيت")]
    table = models.ForeignKey(DiningTable, on_delete=models.PROTECT, related_name="tabs", verbose_name="الترابيزة")
    state = models.CharField("الحالة", max_length=6, choices=STATES, default="open")
    opened_at = models.DateTimeField("فُتحت", auto_now_add=True)
    opened_by = models.CharField("فتحها", max_length=100, blank=True)
    closed_at = models.DateTimeField("أُغلقت", null=True, blank=True)
    closed_by = models.CharField("قفلها", max_length=100, blank=True)
    order_uid = models.CharField("رقم الفاتورة", max_length=60, blank=True)
    lines = models.JSONField(default=list, blank=True)
    batches = models.JSONField(default=list, blank=True)
    log = models.JSONField(default=list, blank=True)
    note = models.CharField("ملاحظة", max_length=200, blank=True)

    class Meta:
        verbose_name = "حساب ترابيزة"
        verbose_name_plural = "حسابات الترابيزات"
        ordering = ["-opened_at"]

    def __str__(self):
        return f"{self.table} — {self.opened_at:%d/%m %H:%M}"


class DeliveryCustomer(models.Model):
    """The delivery address book (1.24): the cashier types the phone, the name and address come back."""
    phone = models.CharField("الموبايل", max_length=30, unique=True)
    name = models.CharField("الاسم", max_length=120, blank=True)
    address = models.CharField("العنوان", max_length=300, blank=True)
    orders = models.PositiveIntegerField("عدد الطلبات", default=0)
    last_at = models.DateTimeField("آخر طلب", null=True, blank=True)

    class Meta:
        verbose_name = "عميل دليفري"
        verbose_name_plural = "دفتر عناوين الدليفري"
        ordering = ["-last_at"]

    def __str__(self):
        return f"{self.name} {self.phone}".strip()


class DeliveryOrder(models.Model):
    """A sale sent with a rider (1.24): rung like any sale, its money is «مع الطيار» until he hands it in at a till."""
    STATES = [("out", "مع الطيار"), ("paid", "تم التحصيل"), ("void", "ملغي")]
    order = models.OneToOneField(Order, on_delete=models.PROTECT, related_name="delivery", verbose_name="الفاتورة")
    phone = models.CharField("الموبايل", max_length=30, blank=True)
    name = models.CharField("العميل", max_length=120, blank=True)
    address = models.CharField("العنوان", max_length=300, blank=True)
    rider = models.CharField("الطيار", max_length=80, blank=True)
    fee = models.DecimalField("رسم التوصيل", max_digits=10, decimal_places=2, default=0)
    due = models.DecimalField("المطلوب من الطيار", max_digits=14, decimal_places=2, default=0)
    state = models.CharField("الحالة", max_length=5, choices=STATES, default="out")
    created_at = models.DateTimeField("وقت الطلب")
    collected_at = models.DateTimeField("وقت التحصيل", null=True, blank=True)
    collect_move = models.ForeignKey(CashMove, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    # 1.26 tracking (pos/delivery.py advance): the order's way to the customer; «state» above stays the money's
    STAGES = [("new", "جديد"), ("prep", "قيد التحضير"), ("out", "خرج مع عامل التوصيل"), ("done", "تم التسليم"), ("void", "ملغي")]
    stage = models.CharField("مرحلة الطلب", max_length=5, choices=STAGES, default="new", db_index=True)
    steps = models.JSONField("المراحل", default=list, blank=True)  # [{s, at, by, dev, u}] — when each step happened and who did it
    platform = models.CharField("المنصة", max_length=12, blank=True)  # «talabat» / «elmenus»: the platform's order (its rider)

    class Meta:
        verbose_name = "طلب دليفري"
        verbose_name_plural = "طلبات الدليفري"
        ordering = ["-created_at"]
