import json
from decimal import Decimal

from django.conf import settings
from django.db import models

from ledger.models import Account, DimensionValue, JournalEntry, Partner

Q3 = Decimal("0.001")


class Warehouse(models.Model):
    code = models.CharField("الكود", max_length=10, unique=True)
    name = models.CharField("اسم المخزن", max_length=100)
    project = models.ForeignKey(DimensionValue, on_delete=models.PROTECT, verbose_name="المشروع", limit_choices_to={"dimension__number": 1, "is_project": True})
    keeper = models.CharField("أمين المخزن", max_length=100, blank=True)
    active = models.BooleanField("نشط", default=True)

    class Meta:
        verbose_name = "مخزن"
        verbose_name_plural = "المخازن"
        ordering = ["code"]

    def __str__(self):
        return f"{self.name} ({self.project.name})"


class ItemCategory(models.Model):
    name = models.CharField("التصنيف", max_length=80, unique=True)
    stock_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="+", verbose_name="حساب المخزون", limit_choices_to={"is_postable": True})
    farm_account = models.ForeignKey(Account, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
                                     verbose_name="حساب الصرف للمزرعة (تكلفة زراعية)", limit_choices_to={"is_postable": True})
    admin_account = models.ForeignKey(Account, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
                                      verbose_name="حساب الصرف للإدارة", limit_choices_to={"is_postable": True})
    needs_equipment = models.BooleanField("الصرف يتطلب معدة (سولار/زيوت)", default=False)

    class Meta:
        verbose_name = "تصنيف أصناف"
        verbose_name_plural = "تصنيفات الأصناف"

    def __str__(self):
        return self.name


class ArabicJSON(json.JSONEncoder):
    """Stored as readable Arabic, so a search (LIKE) finds «تويوتا» inside the item's extra fields."""

    def __init__(self, *a, **k):
        k["ensure_ascii"] = False
        super().__init__(*a, **k)


class Item(models.Model):
    code = models.CharField("الكود", max_length=20, unique=True)
    name = models.CharField("اسم الصنف", max_length=150, db_index=True)
    category = models.ForeignKey(ItemCategory, on_delete=models.PROTECT, verbose_name="التصنيف")
    unit = models.CharField("الوحدة", max_length=20)
    reorder_level = models.DecimalField("حد إعادة الطلب", max_digits=14, decimal_places=3, null=True, blank=True)
    barcode = models.CharField("الباركود", max_length=40, blank=True, db_index=True)
    sale_price = models.DecimalField("سعر البيع", max_digits=14, decimal_places=2, null=True, blank=True)
    vat_rate = models.DecimalField("نسبة القيمة المضافة %", max_digits=5, decimal_places=2, null=True, blank=True, help_text="متاح = الإعداد العام")
    aliases = models.TextField("كتابات أخرى", blank=True)
    extra = models.JSONField("بيانات حسب النشاط", default=dict, blank=True, encoder=ArabicJSON)  # e.g. part number, brand, size (core/activities fields)
    active = models.BooleanField("نشط", default=True)
    TYPES = [("stock", "صنف مخزني"), ("recipe", "يُحضَّر عند الطلب من خامات (وصفة)"), ("service", "خدمة (بدون مخزون)")]
    item_type = models.CharField("نوع الصنف", max_length=8, choices=TYPES, default="stock",
                                 help_text="الوصفة: البيع يخصم الخامات من المخزن (قهوة، ساندويتش…). الخدمة: ساعات لعب، توصيل…")
    std_cost = models.DecimalField("تكلفة تقديرية للوحدة", max_digits=14, decimal_places=4, null=True, blank=True,
                                   help_text="تُستخدم في تسعير الوصفات حتى يُشترى الصنف ويصبح له متوسط تكلفة")
    # restaurants (1.22): one product on the menu with sizes — every size is its own item (price, recipe, sales) under the product
    variant_of = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="sizes", verbose_name="حجم من المنتج")
    size_name = models.CharField("الحجم", max_length=40, blank=True)
    # add-ons sold with an order line (extra cheese…); no categories chosen = they fit every product
    is_addon = models.BooleanField("إضافة على الطلب", default=False)
    addon_for = models.ManyToManyField(ItemCategory, blank=True, related_name="addons", verbose_name="تظهر مع تصنيفات")
    # company-wide moving average
    qty_on_hand = models.DecimalField("الكمية الإجمالية", max_digits=18, decimal_places=3, default=0, editable=False)
    stock_value = models.DecimalField("قيمة المخزون", max_digits=18, decimal_places=2, default=0, editable=False)

    class Meta:
        verbose_name = "صنف"
        verbose_name_plural = "الأصناف"
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} - {self.name}"

    @property
    def avg_cost(self):
        # never negative: a shop that sold before recording the purchase has a negative balance, not a negative cost
        return (self.stock_value / self.qty_on_hand) if self.qty_on_hand > 0 and self.stock_value > 0 else Decimal("0")


class StockQuant(models.Model):
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="quants")
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="quants")
    qty = models.DecimalField(max_digits=18, decimal_places=3, default=0)

    class Meta:
        unique_together = [("item", "warehouse")]


class StockMove(models.Model):
    KINDS = [("receipt", "إذن إضافة (وارد)"), ("issue", "إذن صرف"), ("return", "مرتجع من الصرف"),
             ("adjust_in", "تسوية بالزيادة"), ("adjust_out", "تسوية بالعجز / توالف"), ("transfer", "تحويل بين مخازن"),
             ("opening", "رصيد أول المدة"), ("harvest", "إضافة حصاد"),
             ("preturn", "مرتجع مشتريات (لمورد)"), ("expired", "تالف / هالك / منتهي الصلاحية")]
    STATES = [("draft", "مسودة"), ("posted", "مرحّل"), ("cancelled", "ملغي")]
    kind = models.CharField("النوع", max_length=10, choices=KINDS)
    number = models.CharField("الرقم", max_length=30, blank=True, db_index=True)
    legacy_number = models.CharField("رقم الإذن القديم", max_length=30, blank=True)
    date = models.DateField("التاريخ", db_index=True)
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="moves", verbose_name="المخزن")
    to_warehouse = models.ForeignKey(Warehouse, null=True, blank=True, on_delete=models.PROTECT, related_name="incoming", verbose_name="إلى مخزن")
    partner = models.ForeignKey(Partner, null=True, blank=True, on_delete=models.PROTECT, verbose_name="المورد / المستلم")
    receiver = models.CharField("اسم المستلم", max_length=100, blank=True)
    credit_account = models.ForeignKey(Account, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
                                       verbose_name="الحساب الدائن (للوارد)", help_text="افتراضيًا: وسيط توريد مخزني، أو الموردون إن اخترت موردًا")
    description = models.CharField("البيان", max_length=300, blank=True)
    state = models.CharField("الحالة", max_length=10, choices=STATES, default="draft")
    entry = models.OneToOneField(JournalEntry, null=True, blank=True, on_delete=models.PROTECT, related_name="stock_move")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "حركة مخزن"
        verbose_name_plural = "حركات المخازن"
        ordering = ["-date", "-id"]

    def __str__(self):
        return f"{self.get_kind_display()} {self.number or 'مسودة'}"


class StockMoveLine(models.Model):
    move = models.ForeignKey(StockMove, on_delete=models.CASCADE, related_name="lines")
    expiry = models.DateField("تاريخ الصلاحية", null=True, blank=True)
    produced = models.DateField("تاريخ الإنتاج", null=True, blank=True)
    item = models.ForeignKey(Item, on_delete=models.PROTECT, verbose_name="الصنف")
    qty = models.DecimalField("الكمية", max_digits=18, decimal_places=3)
    unit_cost = models.DecimalField("سعر الوحدة", max_digits=18, decimal_places=4, null=True, blank=True, help_text="للوارد فقط؛ الصرف بمتوسط التكلفة")
    value = models.DecimalField("القيمة", max_digits=18, decimal_places=2, default=0, editable=False)
    account = models.ForeignKey(Account, null=True, blank=True, on_delete=models.PROTECT, verbose_name="حساب الصرف (اختياري)", limit_choices_to={"is_postable": True})
    dim1 = models.ForeignKey(DimensionValue, null=True, blank=True, on_delete=models.PROTECT, related_name="+", verbose_name="القطاع / مركز التكلفة")
    dim2 = models.ForeignKey(DimensionValue, null=True, blank=True, on_delete=models.PROTECT, related_name="+", verbose_name="المعدة")
    dim3 = models.ForeignKey(DimensionValue, null=True, blank=True, on_delete=models.PROTECT, related_name="+", verbose_name="العملية")
    note = models.CharField("ملاحظات", max_length=200, blank=True)

    class Meta:
        verbose_name = "سطر حركة"
        verbose_name_plural = "سطور الحركة"


class ItemUnit(models.Model):
    """A bigger selling/buying unit of the same item: a carton = 12 boxes. Stock is always kept in the base unit."""

    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="units", verbose_name="الصنف")
    name = models.CharField("الوحدة", max_length=40, help_text="كرتونة، شدة، دستة…")
    factor = models.DecimalField("كم تحتوي من الوحدة الأساسية", max_digits=10, decimal_places=3)
    barcode = models.CharField("باركود الوحدة", max_length=40, blank=True, db_index=True)
    sale_price = models.DecimalField("سعر بيع الوحدة", max_digits=12, decimal_places=2, null=True, blank=True)

    class Meta:
        verbose_name = "وحدة إضافية"
        verbose_name_plural = "الوحدات الإضافية"

    def __str__(self):
        return f"{self.name} {self.item.name} ({self.factor:g})"


class ProductionOrder(models.Model):
    """Turn items into other items (milk + rice + cream → rice pudding). Inputs leave the store into «إنتاج تحت التشغيل»;
    when the batch comes out, the outputs enter the store carrying the inputs' cost."""

    STATES = [("draft", "مسودة"), ("in_kitchen", "في المعمل"), ("done", "خلص"), ("cancelled", "ملغي")]
    number = models.CharField("الرقم", max_length=20, blank=True)
    name = models.CharField("البيان", max_length=150)
    from_warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="+", verbose_name="الخامات من مخزن")
    to_warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="+", verbose_name="الإنتاج يدخل مخزن")
    date_out = models.DateField("تاريخ الصرف للمعمل")
    date_in = models.DateField("تاريخ استلام الإنتاج", null=True, blank=True)
    state = models.CharField("الحالة", max_length=12, choices=STATES, default="draft")
    issue_move = models.ForeignKey(StockMove, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    receipt_move = models.ForeignKey(StockMove, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    cost = models.DecimalField("تكلفة الخامات", max_digits=16, decimal_places=2, default=0)
    notes = models.CharField("ملاحظات", max_length=300, blank=True)

    class Meta:
        verbose_name = "أمر تصنيع"
        verbose_name_plural = "أوامر التصنيع"
        ordering = ["-date_out", "-id"]

    def __str__(self):
        return f"{self.number} {self.name}"


class ProductionLine(models.Model):
    order = models.ForeignKey(ProductionOrder, on_delete=models.CASCADE, related_name="lines")
    side = models.CharField(max_length=3, choices=[("in", "خامة"), ("out", "إنتاج")])
    item = models.ForeignKey(Item, on_delete=models.PROTECT, verbose_name="الصنف")
    qty = models.DecimalField("الكمية", max_digits=14, decimal_places=3)
    share = models.DecimalField("نصيب من التكلفة %", max_digits=6, decimal_places=2, null=True, blank=True,
                                help_text="للإنتاج فقط — فارغ = بالتساوي حسب قيمة البيع")


class Recipe(models.Model):
    """What one unit of a made-to-order item (drink, sandwich…) uses. Selling it deducts the ingredients from the store,
    and the cost of the ingredients gives the suggested selling price."""

    item = models.OneToOneField(Item, on_delete=models.CASCADE, related_name="recipe", verbose_name="الصنف المباع")
    target_margin = models.DecimalField("هامش الربح المستهدف % من سعر البيع", max_digits=5, decimal_places=2, default=Decimal("65"),
                                        help_text="65% = تكلفة الخامات 35% من السعر (المعتاد للكافيهات 25–35%)")
    extra_cost = models.DecimalField("تكاليف إضافية للوحدة", max_digits=10, decimal_places=2, default=0,
                                     help_text="كوب، غطاء، شفاطة، غاز… إن لم تكن مسجّلة كخامات")
    notes = models.CharField("طريقة التحضير / ملاحظات", max_length=300, blank=True)

    class Meta:
        verbose_name = "وصفة"
        verbose_name_plural = "الوصفات"

    def __str__(self):
        return f"وصفة {self.item.name}"


class RecipeLine(models.Model):
    recipe = models.ForeignKey(Recipe, on_delete=models.CASCADE, related_name="lines")
    ingredient = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="+", verbose_name="الخامة")
    qty = models.DecimalField("الكمية (بوحدة الخامة)", max_digits=14, decimal_places=4)
    waste_pct = models.DecimalField("هالك %", max_digits=5, decimal_places=2, default=0)

    class Meta:
        verbose_name = "خامة في وصفة"
        verbose_name_plural = "خامات الوصفة"
