from django.contrib import admin

from .models import AuditLog, CompanySettings, Currency, ExchangeRate


@admin.register(CompanySettings)
class CompanySettingsAdmin(admin.ModelAdmin):
    list_display = ("name", "base_currency", "allow_negative_cash", "allow_negative_stock")

    def has_add_permission(self, request):
        return not CompanySettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Currency)
class CurrencyAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "active")


@admin.register(ExchangeRate)
class ExchangeRateAdmin(admin.ModelAdmin):
    list_display = ("currency", "date", "rate")
    list_filter = ("currency",)
    date_hierarchy = "date"


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("ts", "user", "action", "model", "object_id", "summary", "ip")
    list_filter = ("action", "model", "user")
    search_fields = ("summary", "object_id")
    date_hierarchy = "ts"

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


from .models import TwoFactor  # noqa: E402


@admin.register(TwoFactor)
class TwoFactorAdmin(admin.ModelAdmin):
    """Only shows who has the code on. To reset a lost phone: select the user ← «إلغاء الرمز»."""

    list_display = ("user", "confirmed", "created", "last_used", "backup_left")
    fields = ("user", "confirmed", "created", "last_used")
    readonly_fields = fields
    actions = ["reset"]

    @admin.display(description="أكواد احتياطية متبقية")
    def backup_left(self, obj):
        return len(obj.backup_codes)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.action(description="إلغاء الرمز (فُقد الهاتف) — يُفعّله من جديد عند أول دخول")
    def reset(self, request, qs):
        from . import audit

        for tf in qs:
            audit.log("2fa_reset", model="TwoFactor", object_id=str(tf.user_id), summary=f"إلغاء رمز التأكيد لـ {tf.user}")
        n = qs.delete()[0]
        self.message_user(request, f"تم إلغاء الرمز لـ {n} مستخدم")
