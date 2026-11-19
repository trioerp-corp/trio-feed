from django.contrib import admin

from .models import Appointment, Doctor, Patient, Service, Visit


@admin.register(Doctor)
class DoctorAdmin(admin.ModelAdmin):
    list_display = ("name", "specialty", "share_pct", "active")


@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    list_display = ("number", "name", "phone")
    search_fields = ("name", "phone", "number")


admin.site.register(Service)
admin.site.register(Visit)
admin.site.register(Appointment)
