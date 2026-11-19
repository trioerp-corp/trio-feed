"""Mobile confirmation code (2FA): enrolment, verification at every login, backup codes, lockout."""
import io

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from . import audit, totp
from .models import CompanySettings, TwoFactor

SESSION_OK = "gg_2fa_ok"
MAX_TRIES = 5
EXEMPT = ("/trio/", "/login/", "/logout/", "/2fa/", "/static/", "/pos/api/", "/pos/sw.js", "/admin/login/", "/admin/logout/", "/favicon")


def required():
    if not getattr(settings, "TWO_FACTOR_ENFORCE", True):
        return False
    try:
        return CompanySettings.get().require_2fa
    except Exception:
        return False


class TwoFactorMiddleware:
    """After the password, a logged-in user can reach nothing until the mobile code is entered (or set up)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        u = getattr(request, "user", None)
        if u and u.is_authenticated and not request.session.get(SESSION_OK) and not request.path.startswith(EXEMPT):
            tf = TwoFactor.objects.filter(user=u, confirmed=True).first()
            if tf:
                return redirect(f"/2fa/verify/?next={request.get_full_path()}")
            if required():
                return redirect("/2fa/setup/")
        return self.get_response(request)


def _next(request):
    n = request.POST.get("next") or request.GET.get("next") or "/"
    return n if url_has_allowed_host_and_scheme(n, {request.get_host()}) else "/"


def _qr_svg(data):
    import segno

    buf = io.BytesIO()
    segno.make(data, error="m").save(buf, kind="svg", scale=6, border=2, dark="#0B1F3A", xmldecl=False, svgns=True)
    return buf.getvalue().decode()


@login_required
def setup(request):
    tf = TwoFactor.objects.filter(user=request.user).first()
    if tf and tf.confirmed:
        if not request.session.get(SESSION_OK):
            return redirect("/2fa/verify/")
        messages.info(request, "رمز التأكيد مفعّل بالفعل على حسابك")
        return redirect("/")
    if not tf:
        tf = TwoFactor.objects.create(user=request.user, secret=totp.new_secret())
    error = ""
    if request.method == "POST":
        k = totp.verify(tf.secret, request.POST.get("code", ""))
        if k:
            plain, hashed = totp.new_backup_codes()
            tf.confirmed, tf.last_counter, tf.backup_codes, tf.last_used = True, k, hashed, timezone.now()
            tf.save()
            request.session[SESSION_OK] = True
            audit.log("2fa_on", model="TwoFactor", object_id=str(request.user.pk), summary=f"تفعيل رمز تأكيد الهاتف لـ {request.user}")
            return render(request, "core/2fa_backup.html", {"codes": plain, "hide_nav": True})
        error = "الرقم غير صحيح. تأكد من ضبط ساعة الهاتف (تلقائي) وجرّب الرقم الجديد."
    company = CompanySettings.get()
    uri = totp.uri(tf.secret, request.user.get_username(), company.name_en or company.name or "ERP")
    return render(request, "core/2fa_setup.html", {"qr": _qr_svg(uri), "secret": " ".join(tf.secret[i:i + 4] for i in range(0, len(tf.secret), 4)),
                                                   "error": error, "hide_nav": True})


@login_required
def verify(request):
    tf = TwoFactor.objects.filter(user=request.user, confirmed=True).first()
    if not tf:
        return redirect("/2fa/setup/")
    if request.session.get(SESSION_OK):
        return redirect(_next(request))
    error = ""
    if request.method == "POST":
        code = request.POST.get("code", "").strip()
        ok = False
        k = totp.verify(tf.secret, code, tf.last_counter)
        if k:
            tf.last_counter, ok = k, True
        else:
            h = totp.hash_code(code)
            if len("".join(c for c in code if c.isdigit())) == 8 and h in tf.backup_codes:
                tf.backup_codes = [x for x in tf.backup_codes if x != h]
                ok = True
                audit.log("2fa_backup", model="TwoFactor", object_id=str(request.user.pk),
                          summary=f"دخول برمز احتياطي — متبقٍ {len(tf.backup_codes)}")
                messages.warning(request, f"استخدمت رمزًا احتياطيًا. متبقٍ لديك {len(tf.backup_codes)} كود.")
        if ok:
            tf.last_used = timezone.now()
            tf.save()
            request.session[SESSION_OK] = True
            request.session.pop("gg_2fa_tries", None)
            return redirect(_next(request))
        tries = request.session.get("gg_2fa_tries", 0) + 1
        request.session["gg_2fa_tries"] = tries
        if tries >= MAX_TRIES:
            audit.log("2fa_lock", model="TwoFactor", object_id=str(request.user.pk), summary=f"{MAX_TRIES} محاولات خاطئة لرمز التأكيد — تم تسجيل الخروج")
            logout(request)
            messages.error(request, "محاولات خاطئة كثيرة. سجّل الدخول من البداية.")
            return redirect("/login/")
        error = f"الرقم غير صحيح. متبقٍ {MAX_TRIES - tries} محاولات."
    return render(request, "core/2fa_verify.html", {"error": error, "next": _next(request), "hide_nav": True})


@login_required
def regenerate(request):
    """New backup codes (old ones stop working). Only after this session passed 2FA."""
    tf = TwoFactor.objects.filter(user=request.user, confirmed=True).first()
    if request.method != "POST" or not tf or not request.session.get(SESSION_OK):
        return redirect("/")
    plain, tf.backup_codes = totp.new_backup_codes()
    tf.save()
    audit.log("2fa_codes", model="TwoFactor", object_id=str(request.user.pk), summary="أكواد احتياطية جديدة")
    return render(request, "core/2fa_backup.html", {"codes": plain, "hide_nav": True})
