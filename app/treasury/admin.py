from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils.html import format_html

from . import services as T
from .models import CashBox, DayClose, DayCloseCount, TreasuryMapping, Voucher, VoucherLine


@admin.register(CashBox)
class CashBoxAdmin(admin.ModelAdmin):
    list_display = ("name", "kind", "currency", "account", "balance", "active")
    list_filter = ("kind", "currency", "active")
    search_fields = ("name", "account__code", "legacy_name")
    autocomplete_fields = ("account",)

    @admin.display(description="الرصيد")
    def balance(self, obj):
        return T.cashbox_balance(obj, in_currency=True)


class VoucherLineInline(admin.TabularInline):
    model = VoucherLine
    extra = 1
    fields = ("account", "partner", "amount", "dim1", "dim2", "dim3", "description")
    autocomplete_fields = ("account", "partner", "dim1", "dim2", "dim3")

    def has_change_permission(self, request, obj=None):
        return obj is None or obj.state == "draft"

    def has_add_permission(self, request, obj=None):
        return obj is None or obj.state == "draft"

    def has_delete_permission(self, request, obj=None):
        return obj is None or obj.state == "draft"


@admin.register(Voucher)
class VoucherAdmin(admin.ModelAdmin):
    list_display = ("number", "legacy_number", "kind", "date", "cashbox", "amount", "currency", "payee", "description", "state", "print_link")
    list_filter = ("kind", "state", "cashbox", ("date", admin.DateFieldListFilter))
    search_fields = ("number", "legacy_number", "description", "payee", "partner__name")
    date_hierarchy = "date"
    autocomplete_fields = ("partner",)
    inlines = [VoucherLineInline]
    actions = ["post_selected", "cancel_selected"]
    readonly_fields = ("number", "state", "entry")
    fieldsets = (
        (None, {"fields": ("kind", "date", "cashbox", "to_cashbox", "amount", "currency", "rate")}),
        ("البيانات", {"fields": ("partner", "payee", "description", "approved_by_text")}),
        ("الحالة", {"fields": ("number", "state", "entry", "legacy_number")}),
    )

    @admin.display(description="طباعة")
    def print_link(self, obj):
        return format_html('<a href="{}" target="_blank">🖨</a>', reverse("voucher_print", args=[obj.pk]))

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.state != "draft":
            return [f.name for f in self.model._meta.fields]
        return self.readonly_fields

    def save_model(self, request, obj, form, change):
        if not obj.created_by_id:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    def has_delete_permission(self, request, obj=None):
        return obj is None or obj.state == "draft"

    def response_change(self, request, obj):
        if "_post" in request.POST:
            try:
                T.post_voucher(obj, user=request.user)
                messages.success(request, f"تم ترحيل {obj}")
            except ValidationError as e:
                for m in e.messages:
                    messages.error(request, m)
        return super().response_change(request, obj)

    @admin.action(description="ترحيل السندات المحددة")
    def post_selected(self, request, queryset):
        for v in queryset.filter(state="draft").order_by("date", "id"):
            try:
                T.post_voucher(v, user=request.user)
                messages.success(request, f"تم ترحيل {Voucher.objects.get(pk=v.pk)}")
            except ValidationError as e:
                for m in e.messages:
                    messages.error(request, f"{v}: {m}")

    @admin.action(description="إلغاء السندات المحددة (بقيد عكسي)")
    def cancel_selected(self, request, queryset):
        for v in queryset.filter(state="posted"):
            try:
                T.cancel_voucher(v, "من لوحة الإدارة", user=request.user)
            except ValidationError as e:
                for m in e.messages:
                    messages.error(request, m)


class CountInline(admin.TabularInline):
    model = DayCloseCount
    extra = 0
    readonly_fields = ("cashbox", "book_balance", "counted")
    can_delete = False


@admin.register(DayClose)
class DayCloseAdmin(admin.ModelAdmin):
    list_display = ("date", "closed_by", "closed_at", "adjustment")
    inlines = [CountInline]
    readonly_fields = ("date", "closed_by", "closed_at", "notes", "adjustment")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(TreasuryMapping)
class TreasuryMappingAdmin(admin.ModelAdmin):
    list_display = ("kind", "legacy_value", "context", "account", "cost_center", "note")
    list_filter = ("kind", "context")
    list_editable = ("account", "cost_center")
    search_fields = ("legacy_value", "account__code", "account__name")
    autocomplete_fields = ("account", "cost_center")
