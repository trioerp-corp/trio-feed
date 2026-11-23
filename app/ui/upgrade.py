"""The owner's «التحديثات» page (version, licence and the yearly subscription — renew / pay by card —, update now).
Plus the update notices: «تحديث جاهز» (install now) and «تم التحديث لأحدث إصدار»."""
import json

from core.middleware import safe_next
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.utils import timezone

from core import license as LIC
from core import modules as M
from core import updates as U
from core.models import CompanySettings
from core.version import VERSION


def _owner(request):
    from pos.branches import is_owner_level

    if not (request.user.is_superuser or is_owner_level(request.user)):
        raise PermissionDenied("هذه الصفحة لصاحب النشاط")


def _fetch_now():
    """Look in the channel right now (instead of waiting for the hourly round): downloads and stages a release meant for
    this machine. Returns (ready, message)."""
    from core import feed

    if not feed.feed_url():
        return None, "هذه النسخة غير متصلة بقناة التحديثات — تواصل مع Trio"
    out = feed.check()
    if isinstance(out, str):
        return None, out
    st = U.state()
    if st["ready"]:
        return st["ready"], ""
    bad = [x for x in out if x.startswith("التحديثات: تعذّر")]
    if bad:
        return None, bad[0].replace("التحديثات: ", "") + " — تأكد من الإنترنت وحاول مرة أخرى"
    nv = feed.newer_available()
    if nv:
        return None, f"الإصدار {nv} لم يُنشر لجهازك بعد — سيصلك تلقائيًا فور نشره"
    return None, "لديك أحدث إصدار ✔"


def _install(request):
    (U.folder() / "now.flag").write_text(timezone.now().isoformat())
    (U.folder() / "failed.txt").unlink(missing_ok=True)
    messages.success(request, "سيُثبَّت التحديث خلال دقيقة — يتوقف البرنامج ثوانيَ ثم يعود. ومن يبيع على الكاشير يستمر كالمعتاد ويزامن بعدها.")


@login_required
def page(request):
    """«التحديثات» (11/2026: no plans for now — every licence has everything; updates come by themselves at night, or now)."""
    _owner(request)
    s = CompanySettings.get()
    if request.method == "POST":
        ready, msg = _fetch_now()
        if ready:
            messages.success(request, f"التحديث {ready['v']} جاهز ✔ — اضغط «حدّث الآن» أو اتركه يُثبَّت تلقائيًا ليلًا")
        else:
            (messages.success if msg.endswith("✔") else messages.info)(request, msg)
        return redirect("upgrade")
    from core.feed import newer_available

    from core import pay

    from .shop import renew_box

    mods = M.enabled(s)
    lic = LIC.status(s)
    br = int(lic.get("branches") or 0)
    return render(request, "ui/upgrade.html", dict(s=s, version=VERSION, lic=lic, machine=LIC.machine_code(), st=U.state(), renew=renew_box(request, s),
                                                  pay_on=pay.enabled(), year_price=f"{M.year_total(br):,}", branches=br,
                                                  nv=newer_available(), have=[M.MODULES[k][0] for k in M.MODULES if k in mods]))


@login_required
def paid(request):
    """Back from Paymob: wait for the confirmation, then install the licence (the page refreshes itself)."""
    _owner(request)
    from core import feed, pay

    s = CompanySettings.get()
    oid = request.GET.get("id") or feed.state().get("pay_order") or ""
    status, err = "", ""
    if oid:
        try:
            res = pay.order(oid, s)
            status = res.get("status", "")
            if status == "paid" and res.get("code"):
                feed._save(pay_order="", pay_done=oid)
                if "".join(res["code"].split()) != s.license_code:
                    for lvl, msg in pay.activate(res["code"], s):
                        getattr(messages, lvl)(request, msg)
                messages.success(request, f"تم الدفع ✔ — طلب رقم {oid}. شكرًا لاختيارك Trio")
                return redirect("/")
        except (pay.PayError, ValueError) as e:
            err = str(e)
    return render(request, "ui/pay_wait.html", dict(oid=oid, status=status, err=err))


@login_required
def update_now(request):
    """«حدّث الآن»: staged → install within a minute; not yet downloaded → fetch it now, then install."""
    _owner(request)
    if request.method == "POST":
        ready = U.state()["ready"]
        msg = ""
        if not ready:
            ready, msg = _fetch_now()
        if ready:
            _install(request)
        else:
            (messages.success if msg.endswith("✔") else messages.error)(request, msg)
    return redirect(safe_next(request))


@login_required
def seen(request):
    _owner(request)
    if request.method == "POST":
        f = U.folder()
        what = request.POST.get("what")
        if what == "upgrade":
            (f / "seen-upgrade.txt").write_text("1")
        else:
            st = U.state()
            if st["applied"]:
                (f / "seen.txt").write_text(st["applied"]["v"])
    return redirect(safe_next(request))


def notice(request):
    """For the header: only the owner sees update / upgrade notices."""
    try:
        from pos.branches import is_owner_level

        if not (request.user.is_authenticated and (request.user.is_superuser or is_owner_level(request.user))):
            return {}
        st = U.state()
        f = U.folder()
        up = json.loads((f / "upgraded.json").read_text(encoding="utf-8")) if (f / "upgraded.json").exists() and not (f / "seen-upgrade.txt").exists() else None
        return dict(upd_ready=st["ready"], upd_applied=st["applied"] if st["applied"] and st["seen"] != st["applied"]["v"] else None,
                    upd_failed=st["failed"], upgraded=up, acc_version=VERSION)
    except Exception:  # noqa: BLE001
        return {}
