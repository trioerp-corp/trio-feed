from django.contrib import admin

from .models import PlaySession, Station


@admin.register(Station)
class StationAdmin(admin.ModelAdmin):
    list_display = ("name", "kind", "rate", "rate_multi", "order", "active")
    list_editable = ("rate", "rate_multi", "order", "active")


@admin.register(PlaySession)
class SessionAdmin(admin.ModelAdmin):
    list_display = ("__str__", "customer", "state", "billed_minutes", "time_amount", "items_amount", "total")
    list_filter = ("state", "station")
