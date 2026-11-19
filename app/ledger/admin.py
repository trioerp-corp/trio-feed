from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.db.models import Q

from . import services as L
from .models import Account, Dimension, DimensionValue, Journal, JournalEntry, JournalLine, Partner, Period


def _err(request, e):
    msgs = e.messages if isinstance(e, ValidationError) else [str(e)]
    for m in msgs:
        messages.error(request, m)


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "level", "is_postable", "account_type", "currencies", "dim1_policy", "dim2_policy", "active")
    list_filter = ("is_postable", "account_type", "report", "cash_flow_category", "dim1_policy", "dim2_policy", "active", "level")
    search_fields = ("code", "name")
    autocomplete_fields = ("parent",)
    list_per_page = 100
    fieldsets = (
        (None, {"fields": ("code", "name", "parent", "level", "is_postable", "active")}),
        ("التصنيف", {"fields": ("account_type", "normal_balance", "report", "cash_flow_category", "currencies", "is_partner_control")}),
        ("الأبعاد التحليلية", {"fields": ("dim1_policy", "dim2_policy", "dim3_policy")}),
        (None, {"fields": ("notes",)}),
    )

    def has_delete_permission(self, request, obj=None):
        return bool(obj is None or not obj.journalline_set.exists()) and super().has_delete_permission(request, obj)


@admin.register(Dimension)
class DimensionAdmin(admin.ModelAdmin):
    list_display = ("number", "name", "active")


class ChildInline(admin.TabularInline):
    model = DimensionValue
    fk_name = "parent"
    fields = ("code", "name", "is_postable", "active")
    extra = 0
    show_change_link = True
    max_num = 0
    can_delete = False
    readonly_fields = ("code", "name", "is_postable", "active")


@admin.register(DimensionValue)
class DimensionValueAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "dimension", "level", "is_postable", "is_project", "project_status", "legacy_code", "active", "needs_confirmation")
    list_filter = ("dimension", "level", "is_project", "project_status", "active", "needs_confirmation")
    search_fields = ("code", "name", "legacy_code", "aliases")
    autocomplete_fields = ("parent", "default_cost_center")
    list_per_page = 100
    inlines = [ChildInline]
    actions = ["close_projects", "deactivate", "activate"]

    def get_search_results(self, request, queryset, search_term):
        qs, dup = super().get_search_results(request, queryset, search_term)
        # autocomplete from a journal/voucher line: restrict to the right dimension when the field asks for it
        field = request.GET.get("field_name", "")
        if field.startswith("dim") and field[3:4].isdigit():
            qs = qs.filter(dimension__number=int(field[3]), is_postable=True, active=True)
        elif field in ("project",):
            qs = qs.filter(dimension__number=1, is_project=True)
        elif field in ("cost_center", "sector", "source", "target_parent", "default_cost_center"):
            qs = qs.filter(dimension__number=1)
        return qs, dup

    @admin.action(description="قفل المشاريع المحددة (يمنع التسجيل عليها)")
    def close_projects(self, request, queryset):
        import datetime

        for p in queryset.filter(is_project=True):
            L.close_project(p, datetime.date.today())
        messages.success(request, "تم قفل المشاريع — راجع تقرير التكلفة النهائية من صفحة التقارير")

    @admin.action(description="إيقاف المحدد")
    def deactivate(self, request, queryset):
        for v in queryset:
            v.active = False
            v.save()

    @admin.action(description="تفعيل المحدد")
    def activate(self, request, queryset):
        for v in queryset:
            v.active = True
            v.save()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Partner)
class PartnerAdmin(admin.ModelAdmin):
    list_display = ("name", "kind", "phone", "country", "active", "merged_into")
    list_filter = ("kind", "active")
    search_fields = ("name", "aliases", "tax_id", "phone")
    autocomplete_fields = ("merged_into",)

    def get_search_results(self, request, queryset, search_term):
        qs, dup = super().get_search_results(request, queryset, search_term)
        if request.GET.get("app_label"):
            qs = qs.filter(active=True, merged_into__isnull=True)
        return qs, dup


@admin.register(Journal)
class JournalAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "prefix", "yearly_reset", "start_after")


@admin.register(Period)
class PeriodAdmin(admin.ModelAdmin):
    list_display = ("__str__", "status", "closed_at", "closed_by")
    actions = ["close", "reopen"]
    readonly_fields = ("closed_at", "closed_by")

    @admin.action(description="قفل الفترات المحددة")
    def close(self, request, queryset):
        for p in queryset:
            try:
                L.close_period(p.year, p.month, user=request.user)
            except ValidationError as e:
                _err(request, e)

    @admin.action(description="إعادة فتح الفترات المحددة")
    def reopen(self, request, queryset):
        for p in queryset:
            L.reopen_period(p.year, p.month, user=request.user, reason="من لوحة الإدارة")


class LineInline(admin.TabularInline):
    model = JournalLine
    extra = 4
    fields = ("account", "partner", "debit", "credit", "currency", "amount_currency", "rate", "dim1", "dim2", "dim3", "quantity", "description")
    autocomplete_fields = ("account", "partner", "dim1", "dim2", "dim3")

    def has_change_permission(self, request, obj=None):
        return obj is None or obj.state == "draft"

    def has_add_permission(self, request, obj=None):
        return obj is None or obj.state == "draft"

    def has_delete_permission(self, request, obj=None):
        return obj is None or obj.state == "draft"


@admin.register(JournalEntry)
class JournalEntryAdmin(admin.ModelAdmin):
    list_display = ("number", "date", "journal", "description", "state", "total", "source")
    list_filter = ("state", "journal", ("date", admin.DateFieldListFilter))
    search_fields = ("number", "description", "legacy_ref", "lines__account__code", "lines__partner__name")
    date_hierarchy = "date"
    inlines = [LineInline]
    actions = ["post_selected", "reverse_selected"]
    readonly_fields = ("number", "state", "reversal_of", "source", "legacy_ref", "posted_by", "posted_at")
    list_per_page = 50

    @admin.display(description="الإجمالي")
    def total(self, obj):
        return obj.total_debit

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("lines")

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

    @admin.action(description="ترحيل القيود المحددة")
    def post_selected(self, request, queryset):
        ok = 0
        for e in queryset.filter(state="draft"):
            try:
                L.post(e, user=request.user)
                ok += 1
            except ValidationError as ex:
                _err(request, ex)
        if ok:
            messages.success(request, f"تم ترحيل {ok} قيد")

    @admin.action(description="عكس القيود المحددة (قيد عكسي)")
    def reverse_selected(self, request, queryset):
        for e in queryset.filter(state="posted"):
            try:
                rev = L.reverse(e, reason="من لوحة الإدارة", user=request.user)
                messages.success(request, f"عُكس {e.number} بالقيد {rev.number}")
            except ValidationError as ex:
                _err(request, ex)
