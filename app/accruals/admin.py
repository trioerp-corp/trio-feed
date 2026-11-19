from django.contrib import admin

from .models import Accrual, AccrualLine, RecurringAccrual


@admin.register(RecurringAccrual)
class RecurringAdmin(admin.ModelAdmin):
    list_display = ("name", "partner", "account", "dim1", "amount", "liability_account", "active")
    list_filter = ("active",)
    search_fields = ("name",)
    autocomplete_fields = ("partner", "account", "liability_account")


class LineInline(admin.TabularInline):
    model = AccrualLine
    extra = 0
    autocomplete_fields = ("account",)


@admin.register(Accrual)
class AccrualAdmin(admin.ModelAdmin):
    list_display = ("number", "date", "partner", "description", "amount", "state", "source")
    list_filter = ("state", "source", "liability_account")
    search_fields = ("number", "description", "partner__name")
    inlines = [LineInline]
    readonly_fields = ("entry", "number")
