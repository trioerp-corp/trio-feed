from django.contrib import admin
from ui.restore import restore_page
from django.urls import include, path

from core import devaccess, license_views, password, twofa

class _CompanyName:
    """Admin header follows the company name in settings (works for every client of the system)."""

    def __str__(self):
        try:
            from core.models import CompanySettings

            return CompanySettings.get().name
        except Exception:
            return "نظام الحسابات"


admin.site.site_header = _CompanyName()
admin.site.site_title = _CompanyName()
admin.site.index_title = "الإعدادات والبيانات الأساسية"

def _healthz(request):
    """Which copy of the program answers on this port (the launcher uses it to spot an old copy still running)."""
    from django.conf import settings
    from django.http import JsonResponse

    return JsonResponse({"dir": str(settings.BASE_DIR), "version": "acc-erp-1"})


def _cashier(request):
    """Trio Cashier.exe opens this: the gaming hall screen for a PlayStation / billiards client, the sales screen otherwise."""
    from django.shortcuts import redirect

    from core.models import CompanySettings
    from core.modules import enabled

    s = CompanySettings.get()
    mods = enabled(s)
    if s.business == "clinic" and "clinic" in mods:
        return redirect("/ui/clinic/")
    return redirect("/ui/play/" if s.business == "gaming" and "gaming" in mods else "/pos/")


urlpatterns = [
    path("cashier/", _cashier),
    path("healthz/", _healthz),
    path("admin/", admin.site.urls),
    path("license/", license_views.page, name="license"),
    path("trio/", devaccess.login_page, name="acc_login"),
    path("trio/setup/", devaccess.setup_page, name="acc_setup"),
    path("trio/restore/", restore_page, name="acc_restore"),  # a fresh copy (new computer) brings its old data back
    path("trio/activity/", devaccess.activity_preview, name="acc_activity"),
    path("trio/stations/", devaccess.stations_save, name="acc_stations"),
    path("trio/mail/", devaccess.mail_setup, name="acc_mail"),
    path("trio/control/", devaccess.control_page, name="acc_control"),
    path("trio/logout/", devaccess.dev_logout, name="acc_logout"),
    path("ui/play/", include("gaming.urls")),
    path("ui/clinic/", include("pos.clinic.urls")),
    path("ui/", include("pos.staff.urls")),
    path("password/", password.change, name="password_change"),
    path("2fa/setup/", twofa.setup, name="2fa_setup"),
    path("2fa/verify/", twofa.verify, name="2fa_verify"),
    path("2fa/new-codes/", twofa.regenerate, name="2fa_codes"),
    path("ui/accruals/", include("accruals.urls")),
    path("pos/", include("pos.urls")),
    path("", include("ui.urls")),
    path("", include("reports.urls")),
]
