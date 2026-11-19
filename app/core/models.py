from decimal import Decimal

from django.conf import settings
from django.db import models


class CompanySettings(models.Model):
    """Single-row company configuration. Everything here is editable from the admin."""

    name = models.CharField("اسم المشروع", max_length=200, default="Trio")
    name_en = models.CharField("الاسم بالإنجليزية", max_length=200, default="Trio")
    base_currency = models.CharField("العملة الأساسية", max_length=3, default="EGP")
    tax_registration = models.CharField("رقم التسجيل الضريبي", max_length=50, blank=True)
    default_vat_rate = models.DecimalField("نسبة القيمة المضافة الافتراضية %", max_digits=5, decimal_places=2, default=Decimal("14.00"))
    allow_negative_cash = models.BooleanField("السماح بالصرف بأكثر من رصيد الخزينة", default=False)
    allow_negative_stock = models.BooleanField("السماح بالرصيد السالب في المخازن", default=False)
    pos_enabled = models.BooleanField("تفعيل نقاط البيع", default=True, help_text="يُظهر تطبيق نقاط البيع ويسمح للأجهزة بالمزامنة")
    pos_prices_include_vat = models.BooleanField("أسعار البيع شاملة القيمة المضافة", default=True)
    logo_svg = models.TextField("اللوجو (SVG)", blank=True, help_text="كود SVG للوجو — يظهر في رأس الصفحة والإيصال")
    KITCHEN = [("off", "لا تُطبع"), ("same", "على طابعة الكاشير نفسها"), ("printer", "على طابعة المطبخ")]
    kitchen_ticket = models.CharField("ورقة المطبخ", max_length=8, choices=KITCHEN, default="off")
    kitchen_printer = models.CharField("طابعة المطبخ", max_length=120, blank=True)
    phone = models.CharField("تليفون (يظهر في الإيصال)", max_length=60, blank=True)
    address = models.CharField("العنوان (يظهر في الإيصال)", max_length=200, blank=True)
    # shop: scale barcodes (EAN-13 printed by the deli scale: prefix + item code + weight/price + check digit)
    scale_prefix = models.CharField("بداية باركود الميزان", max_length=3, default="2", help_text="الباركود الذي يبدأ بهذا الرقم يُقرأ كملصق ميزان")
    scale_mode = models.CharField("ملصق الميزان فيه", max_length=6, default="weight", choices=[("weight", "الوزن بالجرام"), ("price", "السعر بالقرش")])
    scale_item_digits = models.PositiveSmallIntegerField("عدد أرقام كود الصنف في ملصق الميزان", default=5)
    # backups
    backup_drive_dir = models.CharField("مجلد جوجل درايف للنسخ الاحتياطي", max_length=300, blank=True,
                                        help_text="مثلًا G:\\My Drive\\ACC-Backup — برنامج Google Drive على الجهاز يرفعه عبر الإنترنت")
    last_backup = models.DateTimeField("آخر نسخة احتياطية", null=True, blank=True)
    backup_key = models.CharField("مفتاح استرجاع النسخ", max_length=40, blank=True)  # opens the encrypted Drive copies
    backup_key_sent = models.BooleanField(default=False)  # a copy of the key is with Trio
    clock_day = models.DateField("آخر يوم سجّله البرنامج", null=True, blank=True)  # licence days never go backwards with the PC clock
    owner_whatsapp = models.CharField("واتساب صاحب المحل (للملخص اليومي)", max_length=20, blank=True)
    # daily summary channels — the developer opens the ones this client bought
    report_whatsapp = models.BooleanField("ملخص اليوم: واتساب (زر)", default=True)
    report_telegram = models.BooleanField("ملخص اليوم: تليجرام (تلقائي)", default=False)
    report_email = models.BooleanField("ملخص اليوم: البريد (تلقائي)", default=False)
    modules_stamp = models.CharField("آخر أمر ترقية طُبّق", max_length=40, blank=True)
    last_month_report = models.DateField("آخر تقرير شهري أُرسل", null=True, blank=True)
    last_year_report = models.DateField("آخر تقرير سنوي أُرسل", null=True, blank=True)
    report_hour = models.PositiveSmallIntegerField("ساعة الإرسال التلقائي", default=23, help_text="إن لم يُقفل اليوم نهائيًا يُرسل الملخص في هذه الساعة")
    telegram_token = models.CharField("توكن بوت تليجرام", max_length=100, blank=True)
    telegram_chat = models.CharField("رقم محادثة تليجرام (chat id)", max_length=200, blank=True, help_text="أكثر من واحد: افصل بفاصلة")
    email_to = models.CharField("البريد الذي سيصله الملخص", max_length=300, blank=True, help_text="أكثر من واحد: افصل بفاصلة")
    smtp_host = models.CharField("SMTP سيرفر", max_length=100, blank=True, default="smtp.gmail.com")
    smtp_port = models.PositiveIntegerField("SMTP بورت", default=587)
    smtp_user = models.CharField("SMTP بريد الإرسال", max_length=150, blank=True)
    smtp_password = models.CharField("SMTP كلمة سر التطبيق", max_length=150, blank=True)
    last_report = models.DateField("آخر يوم أُرسل ملخصه", null=True, blank=True)
    # the owner's phone page and Trio's Telegram bot / mail: one secret per shop (never shown to cashiers)
    mobile_key = models.CharField("مفتاح متابعة الموبايل", max_length=40, blank=True)
    cashier_can_edit = models.BooleanField("يمكن للكاشير إلغاء الفواتير وتعديلها", default=False,
                                           help_text="بدونها يرسل الكاشير طلبًا ويوافق صاحب المحل")
    menu_cfg = models.JSONField("منيو QR", default=dict, blank=True)  # {"hide": [category ids], "note": "…"}
    # forced stop by Trio (fees dispute…): every screen stops, nothing is deleted, the developer turns it back on
    suspended = models.BooleanField("البرنامج موقوف", default=False)
    suspend_note = models.CharField("سبب الإيقاف (يظهر للعميل)", max_length=200, blank=True)
    suspend_source = models.CharField("من أوقفه", max_length=10, blank=True, choices=[("dev", "المطور"), ("remote", "عن بعد")])
    suspended_at = models.DateTimeField("وقت الإيقاف", null=True, blank=True)
    control_url = models.CharField("رابط ملف التحكم عن بعد", max_length=400, blank=True)
    control_stamp = models.CharField("آخر ملف تحكم طُبّق", max_length=40, blank=True)
    control_checked = models.DateTimeField("آخر مراجعة لملف التحكم", null=True, blank=True)
    # licence
    license_code = models.TextField("كود التفعيل", blank=True)
    trial_started = models.DateField("بداية الفترة التجريبية", null=True, blank=True)
    trial_extra = models.PositiveSmallIntegerField("أيام تجربة إضافية من Trio", default=0)
    # Trio: a fresh copy opens the developer setup until the developer configures it (plan, modules, business, users)
    setup_done = models.BooleanField("اكتمل التثبيت", default=False)
    activity = models.JSONField("النشاط بالتفصيل", default=dict, blank=True)  # core/activities profile (categories, item fields, words)
    business = models.CharField("نوع النشاط", max_length=10, default="market", choices=[
        ("market", "سوبر ماركت / بقالة"), ("retail", "محل تجزئة"), ("cafe", "كافيه / مطعم"), ("gaming", "بلايستيشن / بلياردو + كافيه")])
    plan = models.CharField("خطة الأسعار", max_length=10, default="pro", choices=[("basic", "أساسي"), ("standard", "متقدم"), ("pro", "شامل")])
    modules = models.JSONField("الأجزاء المفتوحة", default=list, blank=True, help_text="يُحدَّد من واجهة التثبيت حسب الخطة")
    gaming_round_minutes = models.PositiveSmallIntegerField("تقريب وقت اللعب لأقرب (دقيقة)", default=15)
    gaming_min_minutes = models.PositiveSmallIntegerField("أقل وقت يُحاسب عليه (دقيقة)", default=30)
    gaming_grace_minutes = models.PositiveSmallIntegerField("سماح قبل التقريب (دقيقة)", default=5,
                                                            help_text="إن زاد عن آخر ربع ساعة بأقل من ذلك لا يُحتسب")
    require_2fa = models.BooleanField("إلزام كل المستخدمين برمز تأكيد الهاتف", default=False,
                                      help_text="بعد كلمة السر يطلب البرنامج رقمًا من تطبيق Authenticator على هاتف المستخدم")
    # 1.21 — the shop's tax file (filled once in «تجهيز المحل»; the monthly / yearly tax drafts read it)
    TAX_REGIMES = [("simplified", "النظام الضريبي المبسّط (حجم أعمال أقل من 20 مليون جنيه في السنة)"),
                   ("normal", "النظام العادي (دفاتر وقوائم مالية)"), ("none", "غير مسجّل في الضرائب بعد")]
    tax_regime = models.CharField("النظام الضريبي", max_length=12, choices=TAX_REGIMES, default="simplified")
    vat_registered = models.BooleanField("مسجل في القيمة المضافة", default=False)
    tax_file_no = models.CharField("رقم الملف الضريبي", max_length=40, blank=True)
    tax_office = models.CharField("المأمورية", max_length=80, blank=True)
    commercial_register = models.CharField("رقم السجل التجاري", max_length=40, blank=True)
    insurance_no = models.CharField("رقم المنشأة في التأمينات", max_length=40, blank=True)
    onboarding = models.JSONField("تجهيز المحل", default=dict, blank=True)  # {"done": iso time, "skip": [...]}

    class Meta:
        verbose_name = "إعدادات الشركة"
        verbose_name_plural = "إعدادات الشركة"

    def __str__(self):
        return self.name

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class Currency(models.Model):
    code = models.CharField("الكود", max_length=3, unique=True)
    name = models.CharField("الاسم", max_length=50)
    active = models.BooleanField("نشطة", default=True)

    class Meta:
        verbose_name = "عملة"
        verbose_name_plural = "العملات"
        ordering = ["code"]

    def __str__(self):
        return self.code


class ExchangeRate(models.Model):
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT, verbose_name="العملة")
    date = models.DateField("التاريخ")
    rate = models.DecimalField("السعر بالجنيه", max_digits=14, decimal_places=6)

    class Meta:
        verbose_name = "سعر صرف"
        verbose_name_plural = "أسعار الصرف"
        unique_together = [("currency", "date")]
        ordering = ["-date"]

    def __str__(self):
        return f"{self.currency} {self.date}: {self.rate}"

    @classmethod
    def rate_on(cls, code, date):
        if code == CompanySettings.get().base_currency:
            return Decimal("1")
        r = cls.objects.filter(currency__code=code, date__lte=date).order_by("-date").first()
        return r.rate if r else None


class AuditLog(models.Model):
    """Append-only record of every change. Never edited or deleted from the UI."""

    ts = models.DateTimeField("الوقت", auto_now_add=True, db_index=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, verbose_name="المستخدم")
    ip = models.GenericIPAddressField("IP", null=True, blank=True)
    action = models.CharField("العملية", max_length=30, db_index=True)
    model = models.CharField("النوع", max_length=80, db_index=True)
    object_id = models.CharField("المعرف", max_length=40, db_index=True)
    summary = models.CharField("الوصف", max_length=300, blank=True)
    before = models.JSONField("قبل", null=True, blank=True)
    after = models.JSONField("بعد", null=True, blank=True)

    class Meta:
        verbose_name = "سجل تدقيق"
        verbose_name_plural = "سجل التدقيق"
        ordering = ["-ts"]

    def __str__(self):
        return f"{self.ts:%Y-%m-%d %H:%M} {self.action} {self.model}#{self.object_id}"

    def save(self, *args, **kwargs):
        if self.pk:
            raise PermissionError("سجل التدقيق لا يُعدَّل")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("سجل التدقيق لا يُحذف")


class TwoFactor(models.Model):
    """Mobile authenticator (TOTP) for one user. The secret never leaves the server except as the setup QR."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="two_factor", verbose_name="المستخدم")
    secret = models.CharField(max_length=64)
    confirmed = models.BooleanField("مفعّل", default=False)
    last_counter = models.BigIntegerField(default=0)  # blocks reusing the same code
    backup_codes = models.JSONField(default=list, blank=True)  # sha256 of unused one-time codes
    created = models.DateTimeField("تاريخ التفعيل", auto_now_add=True)
    last_used = models.DateTimeField("آخر استخدام", null=True, blank=True)

    class Meta:
        verbose_name = "رمز تأكيد الهاتف"
        verbose_name_plural = "رموز تأكيد الهاتف"

    def __str__(self):
        return str(self.user)


class UserFlag(models.Model):
    """Per-user switches: a temporary password must be changed at the first login."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="flags")
    must_change_password = models.BooleanField("يجب أن يغيّر كلمة السر", default=False)

    class Meta:
        verbose_name = "إعداد مستخدم"
        verbose_name_plural = "إعدادات المستخدمين"


class DevOTP(models.Model):
    """One-time code e-mailed to Trio developer to open the setup / developer interface. Only the hash is kept."""

    code_hash = models.CharField(max_length=64)
    created = models.DateTimeField(auto_now_add=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    used = models.BooleanField(default=False)
    challenge = models.CharField(max_length=20, blank=True)
    ip = models.CharField(max_length=60, blank=True)

    class Meta:
        verbose_name = "كود دخول المطور"
        verbose_name_plural = "أكواد دخول المطور"
        ordering = ["-created"]


class OwnerPhone(models.Model):
    """The owner's phone, linked from the program («اربط هاتف صاحب المحل»). Its secret signs every order it sends (drawing,
    approval, purchase invoice), so only this phone — not Trio's server, not anyone with the link — can make the program act."""

    name = models.CharField("الموبايل", max_length=80)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, verbose_name="الأونر")
    pair_hash = models.CharField(max_length=64, unique=True)  # how the Worker knows this phone
    secret = models.CharField(max_length=64)  # HMAC key, derived from the one-time link code; never leaves this computer
    active = models.BooleanField(default=False)  # True once the phone entered the code
    expires = models.DateTimeField()  # the code is good for 15 minutes
    created = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "هاتف صاحب المحل"
        ordering = ["-created"]


class PhoneCommand(models.Model):
    """One order from the owner's phone, done once (the id is the phone's) — kept with its answer."""

    cid = models.CharField(max_length=40, unique=True)
    phone = models.ForeignKey(OwnerPhone, null=True, on_delete=models.SET_NULL)
    kind = models.CharField(max_length=20)
    body = models.JSONField(default=dict)
    ok = models.BooleanField(default=False)
    result = models.CharField(max_length=400, blank=True)
    at = models.DateTimeField(auto_now_add=True)
    reported = models.BooleanField(default=False)

    class Meta:
        ordering = ["-at"]
