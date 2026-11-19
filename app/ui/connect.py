"""«📱 الموبايل والتقارير»: the owner's phone page (link + QR + home-screen icon) and where his daily summary goes
(WhatsApp number, e-mail, Telegram linked to Trio's bot)."""
from urllib.parse import quote

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render
from django.utils import timezone

from core import mobile
from core.models import CompanySettings
from core.modules import MODULES, enabled

from .shop import can_see_cash


def _qr_svg(data):
    """A QR that scales to its box (viewBox, no fixed size) — long Telegram links make bigger codes."""
    import io

    import segno

    buf = io.BytesIO()
    segno.make(data, error="m").save(buf, kind="svg", scale=1, border=2, dark="#0B1F3A", xmldecl=False, svgns=True, omitsize=True)
    return buf.getvalue().decode()


@login_required
def connect(request):
    if not can_see_cash(request.user):
        raise PermissionDenied("هذه الصفحة لصاحب المحل")
    s = CompanySettings.get()
    mods = enabled(s)
    if request.method == "POST":
        a = request.POST.get("action")
        if a == "contacts":
            wa = "".join(ch for ch in request.POST.get("whatsapp", "") if ch.isdigit() or ch == "+")[:20]
            if wa.startswith("01") and len(wa) == 11:
                wa = "2" + wa
            s.owner_whatsapp = wa
            mail = request.POST.get("email", "").strip()[:300]
            if mail and not all("@" in m and "." in m.split("@")[-1] for m in [x.strip() for x in mail.split(",") if x.strip()]):
                messages.error(request, "البريد مكتوب بشكل غير صحيح")
                return redirect("connect")
            s.email_to = mail
            try:
                s.report_hour = min(max(int(request.POST.get("report_hour") or s.report_hour), 0), 23)
            except ValueError:
                pass
            s.save(update_fields=["owner_whatsapp", "email_to", "report_hour"])
            messages.success(request, "تم الحفظ ✔")
        elif a == "test":
            from core.reports_send import send_daily

            res = send_daily(force=True)
            if not res:
                messages.info(request, "لا توجد قناة تلقائية (تليجرام / بريد) مفتوحة — يُرسل واتساب من زر «الملخص على واتساب» في تقفيل اليوم")
            for ch, ok, msg in res:
                (messages.success if ok else messages.error)(request, f"{ch}: {msg}")
        elif a == "push" and "mobile" in mods:
            try:
                st = mobile.push(force=True)
                messages.success(request, "تم تحديث صفحة الهاتف ✔" if st == "sent" else "صفحة الهاتف غير متاحة الآن — تأكد من الإنترنت")
            except Exception as e:  # noqa: BLE001
                messages.error(request, f"لم يتم التحديث: {e}")
        elif a in ("pair", "unpair"):
            from core import owner_phone as OP
            from core.models import OwnerPhone
            from pos.branches import is_owner_level

            if not (request.user.is_superuser or is_owner_level(request.user)):
                raise PermissionDenied("ربط هاتف صاحب المحل لصاحب المحل فقط")
            try:
                if a == "pair":
                    code, plink = OP.start_pair(request.user)
                    request.session["pair"] = dict(code=code, link=plink, until=(timezone.now() + timezone.timedelta(minutes=15)).isoformat())
                else:
                    OP.unpair(OwnerPhone.objects.get(pk=int(request.POST.get("pk") or 0)))
                    messages.success(request, "فُصل الهاتف ✔ — يُرفض أي أمر منه بعد ذلك")
            except (ValidationError, ConnectionError, OwnerPhone.DoesNotExist) as e:
                messages.error(request, " ".join(getattr(e, "messages", [str(e)])))
        elif a == "rotate":
            from core.models import OwnerPhone

            OwnerPhone.objects.all().delete()  # the phones belong to the old link
            s.mobile_key = ""
            s.save(update_fields=["mobile_key"])
            mobile.key(s)
            try:
                mobile.push(force=True)
            except Exception:  # noqa: BLE001
                pass
            messages.success(request, "أُنشئ رابط جديد — توقف الرابط القديم وربط تليجرام القديم. افتح الرابط الجديد على هاتفك واربط تليجرام مرة أخرى.")
        return redirect("connect")

    link = mobile.link(s) if "mobile" in mods else ""
    tg = mobile.telegram_link(s) if "report_telegram" in mods and not (s.telegram_token and s.telegram_chat) else ""
    wa_digits = "".join(ch for ch in s.owner_whatsapp if ch.isdigit())
    send_self = f"https://wa.me/{wa_digits}?text={quote('تابع محلك من الهاتف — افتح هذا الرابط وأضفه إلى الشاشة الرئيسية: ' + link)}" if link else ""
    from core.models import OwnerPhone
    from pos.branches import is_owner_level

    pair = request.session.get("pair")
    if pair and pair.get("until", "") < timezone.now().isoformat():
        pair = request.session.pop("pair", None) and None
    if pair and OwnerPhone.objects.filter(active=True, created__gte=timezone.now() - timezone.timedelta(minutes=16)).exists():
        request.session.pop("pair", None)
        pair = None
        messages.success(request, "تم ربط الهاتف ✔")
    mark = mobile._mark()
    last = timezone.datetime.fromtimestamp(mark["t"], tz=timezone.get_current_timezone()) if mark.get("t") else None
    return render(request, "ui/connect.html", dict(
        s=s, mods=mods, link=link, qr=_qr_svg(link) if link else "", send_self=send_self, last=last,
        tg=tg, tg_qr=_qr_svg(tg) if tg else "", tg_chats=mobile.telegram_status(s) if tg else None,
        names={k: MODULES[k][0] for k in ("mobile", "report_whatsapp", "report_telegram", "report_email")},
        own_bot=bool(s.telegram_token and s.telegram_chat), own_mail=bool(s.smtp_user and s.smtp_password),
        any_report=bool({"report_whatsapp", "report_telegram", "report_email"} & mods),
        owner=request.user.is_superuser or is_owner_level(request.user), phones=OwnerPhone.objects.filter(active=True),
        pair=pair, pair_qr=_qr_svg(pair["link"]) if pair else ""))


@login_required
def menu_qr(request):
    """«📱 منيو QR»: the café's menu online — a QR on every table opens it on the customer's phone."""
    from core import menu_pub

    if not can_see_cash(request.user):
        raise PermissionDenied("هذه الصفحة لصاحب المحل")
    s = CompanySettings.get()
    if request.method == "POST":
        a = request.POST.get("action")
        if a == "cfg":
            cats = {c.pk for c in menu_pub.categories()}
            shown = {int(x) for x in request.POST.getlist("cat") if x.isdigit()}
            s.menu_cfg = dict(s.menu_cfg or {}, hide=sorted(cats - shown), note=request.POST.get("note", "").strip()[:200])
            s.save(update_fields=["menu_cfg"])
        try:
            st = menu_pub.push(force=True)
            messages.success(request, "نُشر المنيو ✔ — يُنشر أي تغيير في الأسعار تلقائيًا كل 10 دقائق" if st == "sent"
                             else "تم الحفظ — سيُنشر فور اتصال الجهاز بالإنترنت")
        except Exception as e:  # noqa: BLE001
            messages.error(request, f"حُفظ لكنه لم يُنشر الآن: {e}")
        return redirect("menu_qr")
    link = menu_pub.link(s)
    d = menu_pub.data(s)
    last = menu_pub.last_sent()
    return render(request, "ui/menu_qr.html", dict(
        s=s, link=link, qr=_qr_svg(link) if link else "", cats=menu_pub.categories(), hide=menu_pub.hidden(s), data=d,
        n_items=sum(len(c["items"]) for c in d["cats"]), table=request.GET.get("print") == "1",
        last=timezone.datetime.fromtimestamp(last, tz=timezone.get_current_timezone()) if last else None))
