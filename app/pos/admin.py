from django.contrib import admin

from .models import Branch, Device, Order, OrderLine, Shift


@admin.register(Branch)
class BranchAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "warehouse", "cashbox", "cost_center", "active")
    autocomplete_fields = ("cost_center",)


@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display = ("branch", "code", "name", "token", "last_sync", "active")
    readonly_fields = ("token", "last_sync")


class LineInline(admin.TabularInline):
    model = OrderLine
    extra = 0
    readonly_fields = ("item", "qty", "price", "discount")
    can_delete = False


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("uid", "device", "created_at_device", "total", "paid_cash", "paid_card", "is_return", "flags")
    list_filter = ("device__branch", "is_return")
    search_fields = ("uid",)
    inlines = [LineInline]
    readonly_fields = [f.name for f in Order._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Shift)
class ShiftAdmin(admin.ModelAdmin):
    list_display = ("cashier", "device", "opened_at", "closed_at", "opening_float", "expected_cash", "counted_cash")
    readonly_fields = [f.name for f in Shift._meta.fields]

    def has_add_permission(self, request):
        return False
