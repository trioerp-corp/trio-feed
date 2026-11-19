import threading

_state = threading.local()


def current_user():
    return getattr(_state, "user", None)


def current_ip():
    return getattr(_state, "ip", None)


def set_current(user=None, ip=None):
    _state.user = user
    _state.ip = ip


class CurrentUserMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user if getattr(request, "user", None) and request.user.is_authenticated else None
        from django.conf import settings

        ip = request.META.get("REMOTE_ADDR", "")
        # only from our own tunnel (cloudflared on this computer) is the forwarded address believed — never from a device on the network
        if getattr(settings, "SECURE_PROXY_SSL_HEADER", None) and ip in ("127.0.0.1", "::1"):
            ip = (request.META.get("HTTP_CF_CONNECTING_IP") or request.META.get("HTTP_X_FORWARDED_FOR", ip)).split(",")[0].strip()
        ip = ip or None
        set_current(user, ip)
        try:
            return self.get_response(request)
        finally:
            set_current(None, None)


class ForcePasswordChangeMiddleware:
    """A user with a temporary password can only reach the change-password page until he picks his own."""

    ALLOW = ("/password/", "/logout/", "/login/", "/static/", "/pos/", "/2fa/", "/trio/")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        u = getattr(request, "user", None)
        if u and u.is_authenticated and not request.path.startswith(self.ALLOW):
            from .models import UserFlag

            if UserFlag.objects.filter(user=u, must_change_password=True).exists():
                from django.shortcuts import redirect

                return redirect("/password/")
        return self.get_response(request)


class ShopEditionGuard:
    """Shop edition: the owner and the staff see only the shop / POS section. Accounting, settings and admin open only for the
    system developer (superuser)."""

    DEV_ONLY = ("/admin/", "/ui/entries", "/ui/setup", "/ui/accruals", "/reports/")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        p = request.path
        if p.startswith(self.DEV_ONLY) and not p.startswith(("/admin/login", "/admin/logout", "/ui/setup/items/")):
            u = getattr(request, "user", None)
            if not (u and u.is_authenticated and u.is_superuser):
                if True:
                    from django.shortcuts import render

                    return render(request, "core/dev_only.html", status=403)
        return self.get_response(request)


class LicenseGuard:
    """Shop edition: without a valid activation code (after the trial / grace period) only the activation page opens."""

    ALLOW = ("/license/", "/static/", "/login/", "/logout/", "/healthz/", "/favicon", "/2fa/", "/password/", "/trio/")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.license = None
        try:
            from .models import CompanySettings

            s = CompanySettings.get()
            if True:
                from . import license as LIC

                request.license = LIC.status(s)
                if LIC.blocked(request.license) and not request.path.startswith(self.ALLOW):
                    if request.path.startswith("/pos/api/"):
                        from django.http import JsonResponse

                        return JsonResponse({"error": "انتهى الاشتراك — يحتاج البرنامج إلى كود تفعيل جديد"}, status=402)
                    from django.shortcuts import redirect

                    return redirect("/license/")
        except Exception:  # noqa: BLE001 — never lock the program because of a check error
            request.license = None
        return self.get_response(request)


class SetupGuard:
    """Trio: a fresh copy (not set up yet) opens only the developer login and setup screen."""

    ALLOW = ("/trio/", "/static/", "/healthz/", "/favicon", "/logout/")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.path.startswith(self.ALLOW):
            try:
                from .models import CompanySettings

                done = CompanySettings.get().setup_done
            except Exception:  # noqa: BLE001 — before the first migrate
                done = True
            if not done:
                if request.path.startswith("/pos/api/"):
                    from django.http import JsonResponse

                    return JsonResponse({"error": "البرنامج لم يُثبّت بعد"}, status=503)
                from django.shortcuts import redirect

                return redirect("/trio/")
        return self.get_response(request)


class ModuleGuard:
    """Parts of the program outside the client's plan show a polite «not in your plan» page."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from . import modules as M

        key = M.module_for_path(request.path)
        if key and not request.path.startswith("/pos/api/"):
            try:
                from .models import CompanySettings

                s = CompanySettings.get()
                off = key not in M.enabled(s)
            except Exception:  # noqa: BLE001
                off = False
            if off:
                from django.shortcuts import render

                return render(request, "core/not_in_plan.html", {"module": M.MODULES[key]}, status=403)
        return self.get_response(request)


class SuspendGuard:
    """Forced stop by Trio: every page shows «البرنامج موقوف», nothing is deleted; only the developer interface opens."""

    ALLOW = ("/trio/", "/static/", "/healthz/", "/favicon")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.path.startswith(self.ALLOW):
            try:
                from .models import CompanySettings

                s = CompanySettings.get()
                stopped = s.suspended
            except Exception:  # noqa: BLE001
                stopped = False
            if stopped:
                msg = s.suspend_note or "البرنامج موقوف مؤقتًا"
                if request.path.startswith("/pos/api/"):
                    from django.http import JsonResponse

                    return JsonResponse({"error": msg}, status=423)
                from django.shortcuts import render

                return render(request, "core/suspended.html", {"s": s, "hide_nav": True}, status=423)
        return self.get_response(request)


class FriendlyErrors:
    """A wrong link or junk typed into a form shows a plain Arabic page (and a line in the log), never a bare «Server Error»."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exc):
        import logging
        from decimal import InvalidOperation

        from django.core.exceptions import ObjectDoesNotExist, ValidationError
        from django.utils.datastructures import MultiValueDictKeyError

        if isinstance(exc, ObjectDoesNotExist):
            status, msg = 404, "هذه الصفحة أو السجل غير موجود (ربما حُذف أو الرقم غير صحيح)."
        elif isinstance(exc, (ValueError, InvalidOperation, MultiValueDictKeyError, ValidationError, OverflowError)):
            status, msg = 400, "البيانات المرسلة غير صحيحة — ارجع إلى الصفحة وحاول مرة أخرى."
            logging.getLogger("trio").warning("bad input on %s %s", request.method, request.path, exc_info=exc)
        else:
            return None  # a real fault: Django logs it and shows 500.html
        if request.path.startswith("/pos/api/") or request.headers.get("Accept", "").startswith("application/json"):
            from django.http import JsonResponse

            return JsonResponse({"error": msg}, status=status)
        from django.shortcuts import render

        return render(request, "core/oops.html", {"code": status, "msg": msg, "hide_nav": True}, status=status)


def safe_next(request, default="/"):
    """Where a form asked to go back to — only ever a page of this program (never another site)."""
    from django.utils.http import url_has_allowed_host_and_scheme

    n = request.POST.get("next") or request.GET.get("next") or default
    return n if url_has_allowed_host_and_scheme(n, {request.get_host()}) else default
