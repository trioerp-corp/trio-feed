from django.contrib import admin, messages
from django.core.exceptions import ValidationError

from . import services as INV
from .models import Item, ItemCategory, StockMove, StockMoveLine, StockQuant, Warehouse


@admin.register(Warehouse)
class WarehouseAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "project", "keeper", "active")
    autocomplete_fields = ("project",)
    search_fields = ("code", "name")


@admin.register(ItemCategory)
class ItemCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "stock_account", "farm_account", "admin_account", "needs_equipment")
    autocomplete_fields = ("stock_account", "farm_account", "admin_account")
    search_fields = ("name",)


class QuantInline(admin.TabularInline):
    model = StockQuant
    readonly_fields = ("warehouse", "qty")
    extra = 0
    can_delete = False
    max_num = 0


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "category", "unit", "qty_on_hand", "avg_cost_display", "stock_value", "reorder_level", "active")
    list_filter = ("category", "active")
    search_fields = ("code", "name", "aliases")
    readonly_fields = ("qty_on_hand", "stock_value")
    inlines = [QuantInline]

    @admin.display(description="متوسط التكلفة")
    def avg_cost_display(self, obj):
        return round(obj.avg_cost, 4)


class MoveLineInline(admin.TabularInline):
    model = StockMoveLine
    extra = 3
    fields = ("item", "qty", "unit_cost", "value", "dim1", "dim2", "dim3", "account", "note")
    readonly_fields = ("value",)
    autocomplete_fields = ("item", "dim1", "dim2", "dim3", "account")

    def has_change_permission(self, request, obj=None):
        return obj is None or obj.state == "draft"

    def has_add_permission(self, request, obj=None):
        return obj is None or obj.state == "draft"

    def has_delete_permission(self, request, obj=None):
        return obj is None or obj.state == "draft"


@admin.register(StockMove)
class StockMoveAdmin(admin.ModelAdmin):
    list_display = ("number", "kind", "date", "warehouse", "partner", "receiver", "description", "state")
    list_filter = ("kind", "state", "warehouse")
    search_fields = ("number", "legacy_number", "description", "receiver", "lines__item__name")
    date_hierarchy = "date"
    inlines = [MoveLineInline]
    autocomplete_fields = ("partner", "credit_account")
    actions = ["post_selected"]
    readonly_fields = ("number", "state", "entry")

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

    @admin.action(description="ترحيل الحركات المحددة")
    def post_selected(self, request, queryset):
        for m in queryset.filter(state="draft").order_by("date", "id"):
            try:
                INV.post_move(m, user=request.user)
                messages.success(request, f"تم ترحيل {StockMove.objects.get(pk=m.pk)}")
            except ValidationError as e:
                for msg in e.messages:
                    messages.error(request, f"{m}: {msg}")
