"""Trio developer interface: user «Trio», password = a one-time code e-mailed to Trio.

- The code goes ONLY to DEV_EMAIL (fixed in the program, the client cannot change it), valid 10 minutes, 5 tries, one new code a minute.
- The e-mail is sent with Trio sender mailbox packed with the program (client/acc_mail.bin), or the SMTP of the settings.
- No internet at the client? The screen shows a request code; Trio-License.exe (Trio's private key) turns it into a
  developer code that only this computer accepts, today only.
"""
import base64
import datetime as dt
import hashlib
import hmac
import json
import secrets
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from . import license as LIC
from . import modules as M
from . import setup_service as S
from .models import CompanySettings, DevOTP

DEV_EMAIL = "trioerp.corp@gmail.com"
VALID_MIN, MAX_TRIES, RESEND_SEC = 10, 5, 60
SESSION_KEY = "acc_dev_ok"
_MASK = b"acc-erp/sender/v1"


def _h(code):
    return hmac.new(settings.SECRET_KEY.encode(), code.encode(), hashlib.sha256).hexdigest()


def pack_sender(cfg):
    """Used by the packaging tool: the sender mailbox is not kept as plain text inside the client's folder."""
    raw = json.dumps(cfg).encode()
    key = hashlib.sha256(_MASK).digest()
    return base64.b64encode(bytes(b ^ key[i % len(key)] for i, b in enumerate(raw))).decode()


def _sender_file():
    return Path(settings.BASE_DIR) / "client" / "acc_mail.bin"


def sender():
    """The mailbox the codes are sent FROM. Gmail only sends for a signed-in account, so the program needs one."""
    p = _sender_file()
    if p.exists():
        try:
            raw = base64.b64decode(p.read_text().strip())
            key = hashlib.sha256(_MASK).digest()
            return json.loads(bytes(b ^ key[i % len(key)] for i, b in enumerate(raw)).decode())
        except Exception:  # noqa: BLE001
            pass
    s = CompanySettings.get()
    if s.smtp_user and s.smtp_password:
        return dict(host=s.smtp_host or "smtp.gmail.com", port=s.smtp_port or 587, user=s.smtp_user, password=s.smtp_password)
    return None


def save_sender(cfg):
    p = _sender_file()
    p.parent.mkdir(exist_ok=True)
    p.write_text(pack_sender(cfg))


def smtp_send(snd, msg):
    """Send through Gmail (587 STARTTLS or 465 SSL) with clear Arabic reasons when it fails."""
    host, port = snd.get("host") or "smtp.gmail.com", int(snd.get("port") or 587)
    try:
        if port == 465:
            srv = smtplib.SMTP_SSL(host, port, timeout=20, context=ssl.create_default_context())
        else:
            srv = smtplib.SMTP(host, port, timeout=20)
            srv.ehlo()
            if srv.has_extn("starttls"):
                srv.starttls(context=ssl.create_default_context())
                srv.ehlo()
        with srv:
            if snd.get("password"):
                try:
                    srv.login(snd["user"], snd["password"].replace(" ", ""))
                except smtplib.SMTPException as e:
                    raise smtplib.SMTPAuthenticationError(535, str(e))
            srv.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        raise ValidationError("Gmail رُفض الدخول — يلزم «كلمة سر التطبيقات» (App Password) من 16 حرفًا، وليست كلمة المرور العادية، "
                              "ويجب تفعيل التحقق بخطوتين على حساب الإرسال")
    except (OSError, smtplib.SMTPException) as e:
        raise ValidationError(f"لم يُرسل البريد — تأكد من اتصال الجهاز بالإنترنت ({type(e).__name__})")


def code_message(code, request, sender_user):
    s = CompanySettings.get()
    msg = EmailMessage()
    msg["Subject"] = f"Trio — كود دخول المطور: {code}"
    msg["From"], msg["To"] = sender_user, DEV_EMAIL
    msg.set_content(f"كود الدخول: {code}\nصالح {VALID_MIN} دقايق ولمرة واحدة.\n\n"
                    f"الجهاز: {LIC.machine_code()}\nالعميل: {s.name if s.setup_done else '(نسخة جديدة لم تُثبّت بعد)'}\n"
                    f"IP: {request.META.get('REMOTE_ADDR', '')}\nالوقت: {timezone.localtime():%Y-%m-%d %H:%M}\n\n"
                    "إن لم تطلب هذا الرمز، فهناك من يحاول فتح واجهة المطور على هذا الجهاز.")
    return msg


def send_mail(code, request):
    snd = sender()
    if not snd:
        raise ValidationError("هذه النسخة ليس بها «بريد إرسال» بعد — ادخل برمز Trio License (لا يوجد إنترنت؟) وجهّزه من واجهة المطور")
    smtp_send(snd, code_message(code, request, snd["user"]))


def new_code(request, send=True):
    last = DevOTP.objects.first()
    if last and (timezone.now() - last.created).total_seconds() < RESEND_SEC and not last.used:
        raise ValidationError("انتظر دقيقة قبل طلب رمز جديد")
    if DevOTP.objects.filter(created__gte=timezone.now() - dt.timedelta(hours=1)).count() >= 6:
        raise ValidationError("طُلبت رموز كثيرة — انتظر ساعة")  # keeps guessing the 6 digits hopeless
    code = f"{secrets.randbelow(10 ** 6):06d}"
    challenge = "-".join(secrets.token_hex(2).upper() for _ in range(2))
    otp = DevOTP.objects.create(code_hash=_h(code), challenge=challenge, ip=request.META.get("REMOTE_ADDR", "")[:60])
    if send:
        try:
            send_mail(code, request)
        except ValidationError:
            otp.delete()  # nothing was sent: let him ask again at once
            raise
    return otp, code


def dev_code_ok(text, challenge):
    """Offline developer code from Trio-License.exe: signed {t:dev, m:machine, n:challenge, e:date}."""
    from cryptography.exceptions import InvalidSignature

    try:
        d = LIC.signed(text, "TRIOD")
    except (ValueError, AssertionError, InvalidSignature):
        return False
    return (d.get("t") == "dev" and d.get("m") == LIC.machine_code() and d.get("n", "").upper() == challenge.upper()
            and dt.date.fromisoformat(d.get("e", "2000-01-01")) >= timezone.localdate())


def verify(code):
    otp = DevOTP.objects.filter(used=False).first()
    if not otp or (timezone.now() - otp.created).total_seconds() > VALID_MIN * 60:
        raise ValidationError("انتهت صلاحية الرمز — اطلب رمزًا جديدًا")
    if otp.attempts >= MAX_TRIES:
        raise ValidationError("محاولات خاطئة كثيرة — اطلب رمزًا جديدًا")
    code = "".join(str(code).split())
    ok = hmac.compare_digest(otp.code_hash, _h(code)) if code.isdigit() else dev_code_ok(code, otp.challenge)
    if not ok:
        otp.attempts += 1
        otp.save(update_fields=["attempts"])
        raise ValidationError(f"الرمز غير صحيح — متبقٍ {MAX_TRIES - otp.attempts} محاولات")
    otp.used = True
    otp.save(update_fields=["used"])
    return True


def _act_names():
    from .activities import LIBRARY

    return [p["name"] for p in LIBRARY]


def _on_this_machine(request):
    """The browser runs on the program's own computer (not another device on the network, not through the online tunnel)."""
    m = request.META
    return (m.get("REMOTE_ADDR") in ("127.0.0.1", "::1") and not m.get("HTTP_X_FORWARDED_FOR") and not m.get("HTTP_CF_CONNECTING_IP")
            and not m.get("HTTP_CF_RAY"))


def is_dev(request):
    u = getattr(request, "user", None)
    return bool(u and u.is_authenticated and u.username == S.DEV_USER and u.is_superuser and request.session.get(SESSION_KEY))


@require_http_methods(["GET", "POST"])
def login_page(request):
    if is_dev(request):
        return redirect("/trio/setup/")
    s = CompanySettings.get()
    ctx = dict(hide_nav=True, dev_email=DEV_EMAIL[:3] + "•••" + DEV_EMAIL[DEV_EMAIL.index("@"):], machine=LIC.machine_code(), s=s,
               sent=False, challenge="", has_sender=bool(sender()), act_library=_act_names())
    ctx["self_open"] = not s.setup_done
    if request.method == "POST" and request.POST.get("action") in ("self", "self_request", "self_claim", "demo") and not s.setup_done:
        if not _on_this_machine(request):  # a fresh copy is set up from its own computer, not from another device on the network
            messages.error(request, "التثبيت الأول يتم من الجهاز نفسه المثبت عليه البرنامج")
            return render(request, "core/acc_login.html", ctx)
        if request.POST.get("action") == "demo":
            return demo_start(request, ctx)
        return self_setup_start(request, s, ctx)
    if request.method == "POST":
        user = (request.POST.get("username") or "").strip()
        ctx["username"] = user
        try:
            if user.lower() != S.DEV_USER.lower():
                raise ValidationError("تُفتح واجهة المطور باسم Trio بس")
            if request.POST.get("action") == "send":
                otp, _ = new_code(request)
                ctx.update(sent=True, challenge=otp.challenge)
                messages.success(request, f"تم إرسال رمز من 6 أرقام إلى {ctx['dev_email']}")
            elif request.POST.get("action") == "offline":
                last = DevOTP.objects.filter(used=False).first()
                otp = last if last and (timezone.now() - last.created).total_seconds() < VALID_MIN * 60 else new_code(request, send=False)[0]
                ctx.update(sent=True, challenge=otp.challenge, offline=True)
            else:
                verify(request.POST.get("code", ""))
                u = S.dev_user()
                login(request, u, backend="django.contrib.auth.backends.ModelBackend")
                request.session[SESSION_KEY] = True
                request.session["gg_2fa_ok"] = True
                request.session.set_expiry(2 * 3600)
                from . import audit

                try:
                    audit.log("login", summary="دخول واجهة المطور برمز لمرة واحدة", model="developer", object_id=S.DEV_USER)
                except Exception:  # noqa: BLE001
                    pass
                return redirect("/trio/setup/")
        except ValidationError as e:
            messages.error(request, e.messages[0])
            last = DevOTP.objects.filter(used=False).first()
            if last and request.POST.get("action") != "send" and request.POST.get("code") is not None:
                ctx.update(sent=True, challenge=last.challenge)
    return render(request, "core/acc_login.html", ctx)


def demo_start(request, ctx):
    """The free 14-day trial of a downloaded copy: the basic plan for the client's business, one owner user — then the
    owner picks his plan in «⭐ اشترك» and the licence code he gets turns the same copy (and its data) into the paid one."""
    P = request.POST
    from . import activities as A

    act = A.lookup(request, P.get("activity", "")) if (P.get("activity") or "").strip() else None
    business = act["base"] if act else (P.get("business") if P.get("business") in M.BUSINESS else "market")
    username, password = (P.get("username") or "").strip(), (P.get("password") or "").strip()
    def n(k, d, lo=0, hi=60):
        try:
            return min(max(int(float(P.get(k) or d)), lo), hi)
        except ValueError:
            return d

    def rate(k, d):
        try:
            return str(max(float(P.get(k) or d), 1))
        except ValueError:
            return str(d)

    stations = [x for x in [dict(kind="ps5", prefix="جهاز", count=n("ps_count", 3), rate=rate("ps_rate", 40)),
                            dict(kind="billiard", prefix="ترابيزة", count=n("bl_count", 1), rate=rate("bl_rate", 50))] if x["count"]]
    data = dict(name=(P.get("shop") or "").strip(), phone=(P.get("phone") or "").strip(), address=(P.get("address") or "").strip(), business=business,
                plan="basic", activity=act, modules=list(M.defaults("basic", business)), vat_rate="14", devices=n("devices", 1, 1, 10),
                users=[dict(username=username, first_name=(P.get("owner") or "").strip() or "صاحب المحل", password=password, role="owner", active=True)],
                stations=stations if business == "gaming" else [])
    if business == "gaming":  # the billing block chosen in the wizard; the minimum charge = one block, 5 minutes grace
        blk = P.get("g_round") if P.get("g_round") in ("15", "30", "60") else "15"
        data.update(g_round=blk, g_min=blk, g_grace="5")
    try:
        if not data["name"]:
            raise ValidationError("اكتب اسم المحل")
        cfg, first = S.apply(data)
    except ValidationError as e:
        messages.error(request, e.messages[0])
        ctx.update(demo_open=True, demo=P)
        return render(request, "core/acc_login.html", ctx)
    from django.contrib.auth import get_user_model

    u = get_user_model().objects.get(username=username)
    login(request, u, backend="django.contrib.auth.backends.ModelBackend")
    request.session["gg_2fa_ok"] = True
    LIC.status()  # the 14 days start now
    if P.get("samples") == "1":
        A.add_samples(cfg["activity"])
    messages.success(request, f"أهلًا بك في Trio 👋 — هذه نسخة تجريبية لمدة 14 يومًا بكل الإمكانيات. أكمل تجهيز محلك مرة واحدة هنا.")
    return redirect("/ui/start/")


SELF_KEY = "acc_self"


def self_lic(request):
    """The licence of an owner who bought online and sets the program up himself (None otherwise)."""
    code = request.session.get(SELF_KEY)
    if not code or CompanySettings.get().setup_done:
        return None
    try:
        lic = LIC.parse(code)
    except ValueError:
        return None
    return lic if lic.get("s") and lic.get("m") == LIC.machine_code() else None


def self_setup_start(request, s, ctx):
    P = request.POST
    if P.get("action") == "self_request":
        name, phone = P.get("shop", "").strip()[:100], P.get("phone", "").strip()[:40]
        text = "\n".join([f"طلب كود تثبيت (شراء عبر الإنترنت)", f"المحل: {name}", f"التليفون: {phone}", f"الباقة المطلوبة: {P.get('plan', '')}",
                          f"النشاط: {P.get('business', '')}", f"كود الجهاز: {LIC.machine_code()}"])
        try:
            from .reports_send import send_mail_to

            send_mail_to(s, DEV_EMAIL, f"TRIO-REQUEST — تثبيت عبر الإنترنت — {name}", text)
            messages.success(request, "تم إرسال طلبك إلى Trio ✔ — بعد الدفع سيصلك «كود التفعيل» على واتساب، الصقه هنا.")
        except Exception:  # noqa: BLE001
            from urllib.parse import quote

            ctx["wa"] = f"https://wa.me/201008030459?text={quote(text)}"
            messages.error(request, "لا يوجد إنترنت أو البريد غير مُجهّز — أرسل الطلب عبر واتساب من الزر")
        ctx["self_tab"] = True
        return render(request, "core/acc_login.html", ctx)
    code = "".join(P.get("code", "").split())
    if P.get("action") == "self_claim":  # paid by card on the sales page: the order number brings the licence
        from . import pay

        try:
            code = "".join(pay.claim(P.get("order", ""), P.get("phone", ""), s).split())
        except pay.PayError as e:
            messages.error(request, str(e))
            ctx.update(self_tab=True, claim=P)
            return render(request, "core/acc_login.html", ctx)
    try:
        lic = LIC.parse(code)
        if lic.get("m") != LIC.machine_code():
            raise ValueError("هذا الرمز صادر لجهاز آخر — أرسل كود الجهاز الظاهر هنا")
        if not lic.get("s"):
            raise ValueError("هذا رمز تفعيل عادي — اطلب من Trio «كود تثبيت ذاتي»")
    except ValueError as e:
        messages.error(request, str(e))
        ctx["self_tab"] = True
        return render(request, "core/acc_login.html", ctx)
    request.session[SELF_KEY] = code
    request.session.set_expiry(3 * 3600)
    s.license_code = code
    s.save(update_fields=["license_code"])
    return redirect("/trio/setup/")


def _form_data(request):
    P = request.POST
    users = []
    for i in range(int(P.get("user_rows") or 0)):
        users.append(dict(username=P.get(f"u{i}_username", ""), first_name=P.get(f"u{i}_name", ""), password=P.get(f"u{i}_password", ""),
                          role=P.get(f"u{i}_role", "cashier"), active=P.get(f"u{i}_active", "1") == "1", branch=P.get(f"u{i}_branch", ""),
                          title=P.get(f"u{i}_title", ""), caps=P.getlist(f"u{i}_caps"), email=P.get(f"u{i}_email", "")))
    branches = []
    for i in range(int(P.get("branch_rows") or 0)):
        branches.append(dict(i=P.get(f"b{i}_key", i), name=P.get(f"b{i}_name", ""), address=P.get(f"b{i}_address", ""), phone=P.get(f"b{i}_phone", ""),
                             devices=P.get(f"b{i}_devices", "1"), m_name=P.get(f"b{i}_m_name", ""), m_username=P.get(f"b{i}_m_username", ""),
                             m_password=P.get(f"b{i}_m_password", ""), m_email=P.get(f"b{i}_m_email", "")))
    stations = []
    for i in range(int(P.get("station_rows") or 0)):
        stations.append(dict(kind=P.get(f"s{i}_kind"), prefix=P.get(f"s{i}_prefix"), count=P.get(f"s{i}_count"), rate=P.get(f"s{i}_rate"),
                             rate_multi=P.get(f"s{i}_rate_multi")))
    from . import activities as A

    act = A.lookup(request, P["activity"]) if (P.get("activity") or "").strip() else None
    return dict(activity=act, name=P.get("name"), name_en=P.get("name_en"), phone=P.get("phone"), address=P.get("address"), business=P.get("business"),
                owner_whatsapp=P.get("owner_whatsapp"), email_to=P.get("email_to"),
                plan=P.get("plan"), modules=P.getlist("modules"), vat=P.get("vat") == "1", vat_rate=P.get("vat_rate"), users=users,
                stations=stations, branches=branches, force_change=P.get("force_change") == "1", license_code=P.get("license_code"),
                g_round=P.get("g_round"),
                g_min=P.get("g_min"), g_grace=P.get("g_grace"))


@require_http_methods(["GET", "POST"])
def setup_page(request):
    lic = None if is_dev(request) else self_lic(request)
    if not is_dev(request) and not lic:
        return redirect("/trio/")
    from django.contrib.auth import get_user_model

    s = CompanySettings.get()
    if request.method == "POST":
        f = request.FILES.get("logo")
        data = _form_data(request)
        if lic:  # bought online: plan / business / parts are what was paid for
            business = lic.get("b") or data.get("business") or "market"
            data.update(plan=lic.get("p") or "basic", business=business, license_code=request.session[SELF_KEY],
                        modules=list(set(M.defaults(lic.get("p") or "basic", business)) | set(lic.get("x") or [])))
        try:
            cfg, first = S.apply(data, f.read() if f else None, f.name if f else "")
        except ValidationError as e:
            messages.error(request, e.messages[0])
        else:
            if lic:
                request.session.pop(SELF_KEY, None)
                try:  # connect to Trio's command channel right away (stop / renewals / updates)
                    from . import feed

                    feed.check()
                except Exception:  # noqa: BLE001
                    pass
                try:
                    from .reports_send import send_mail_to

                    send_mail_to(s, DEV_EMAIL, f"TRIO-REQUEST — عميل ثبّت البرنامج بنفسه — {cfg['name']}",
                                 f"{cfg['name']} — {cfg['phone']}\nكود الجهاز: {LIC.machine_code()}\nالباقة: {cfg['plan']}\nيحتاج إلى زيارة التدريب والتشغيل.")
                except Exception:  # noqa: BLE001
                    pass
            if first or request.POST.get("finish") == "1":
                logout(request)
                messages.success(request, f"تثبيت «{cfg['name']}» انتهى ✔ — سجّل الدخول باسم المستخدم وكلمة السر الخاصة بك")
                return redirect("/login/")
            messages.success(request, "تم حفظ التعديلات")
            return redirect("/trio/setup/")
    from gaming.models import Station

    staff = get_user_model().objects.exclude(username=S.DEV_USER).filter(is_superuser=False).order_by("id")
    from pos import branches as BR
    from pos.models import Branch

    existing = []
    for u in staff:
        st = BR.staff_of(u)
        role = st.role if st else next((k for k, (g, _) in S.ROLES.items() if g and u.groups.filter(name=g).exists()), "cashier")
        existing.append(dict(username=u.username, name=u.first_name, role=role, active=u.is_active, branch=st.branch.code if st and st.branch_id else "",
                             title=st.title if st else "", caps=st.caps if st else [], email=st.email if st else ""))
    ctx = dict(hide_nav=True, s=s, first=not s.setup_done, always=M.ALWAYS, self_lic=lic,
               self_plan=M.PLANS.get((lic or {}).get("p") or "", ("",))[0], self_extra=[M.MODULES[x][0] for x in (lic or {}).get("x") or [] if x in M.MODULES], branches_existing=list(Branch.objects.filter(active=True).order_by("id")), modules=list(M.MODULES.items()), plans=list(M.PLANS.items()), business=list(M.BUSINESS.items()),
               roles=list(S.ROLES.items()), act_library=_act_names(), act_value=(s.activity or {}).get("text") or (s.activity or {}).get("name", "") if s.setup_done else "",
               enabled=M.enabled(s) if s.setup_done else set(M.defaults("standard", "market")), machine=LIC.machine_code(),
               lic=LIC.status(s) if s.setup_done else None, existing=existing, mail=sender(), control_link=__import__("core.control", fromlist=["x"]).url(s), stations=Station.objects.all(), kinds=Station.KINDS,
               js=dict(plans={k: v[1] for k, v in M.PLANS.items()}, business={k: v[1] for k, v in M.BUSINESS.items()},
                       always=list(M.ALWAYS), only={k: list(v) for k, v in M.FOR_BUSINESS.items()}, existing=existing,
                       caps=BR.CAPS, templates={k: [v[0], v[1]] for k, v in BR.TEMPLATES.items()},
                       branches=[dict(code=b.code, name=b.name) for b in Branch.objects.filter(active=True).order_by("id")]))
    if lic:
        plan = lic.get("p") or "basic"
        ctx["js"]["self"] = sorted({m for b in M.BUSINESS for m in M.defaults(plan, b)} | set(lic.get("x") or []) | set(M.ALWAYS))
        ctx["plans"] = [(k, v) for k, v in ctx["plans"] if k == plan]
        if lic.get("b"):
            ctx["business"] = [(k, v) for k, v in ctx["business"] if k == lic["b"]]
        ctx["first"] = True
    return render(request, "core/acc_setup.html", ctx)


def stations_save(request):
    """Developer interface: edit the rate / name / on-off of existing devices."""
    if not is_dev(request) or request.method != "POST":
        return redirect("/trio/")
    from gaming.models import Station

    for st in Station.objects.all():
        if f"st{st.pk}_rate" in request.POST:
            try:
                st.name = (request.POST.get(f"st{st.pk}_name") or st.name)[:40]
                st.rate = S.dec(request.POST.get(f"st{st.pk}_rate"))
                st.rate_multi = S.dec(request.POST.get(f"st{st.pk}_rate_multi"), "0") or None
                st.active = request.POST.get(f"st{st.pk}_active") == "1"
                st.save()
            except ValidationError as e:
                messages.error(request, e.messages[0])
    messages.success(request, "تم حفظ الأجهزة")
    return redirect("/trio/setup/#stations")


def dev_logout(request):
    logout(request)
    return redirect("/login/")


def mail_setup(request):
    """Developer interface: the mailbox the codes are sent from — saved only after a test message really arrives."""
    if not is_dev(request) or request.method != "POST":
        return redirect("/trio/")
    cfg = dict(host="smtp.gmail.com", port=587, user=(request.POST.get("mail_user") or "").strip(),
               password=(request.POST.get("mail_password") or "").replace(" ", ""))
    try:
        if "@" not in cfg["user"] or len(cfg["password"]) < 8:
            raise ValidationError("اكتب بريد الإرسال و«كلمة سر التطبيقات» الخاصة به")
        msg = EmailMessage()
        msg["Subject"], msg["From"], msg["To"] = "Trio — تجربة بريد الإرسال ✔", cfg["user"], DEV_EMAIL
        msg.set_content(f"تم تجهيز بريد الإرسال على جهاز {LIC.machine_code()} — ستصلك أكواد دخول المطور من هنا.")
        smtp_send(cfg, msg)
        save_sender(cfg)
        messages.success(request, f"تمام ✔ تم إرسال رسالة تجربة إلى {DEV_EMAIL} — ستصل الرموز من {cfg['user']}")
    except ValidationError as e:
        messages.error(request, e.messages[0])
    return redirect("/trio/setup/#s6")


def control_page(request):
    """Developer interface: stop / resume this copy, and the remote control link."""
    if not is_dev(request) or request.method != "POST":
        return redirect("/trio/")
    from . import control

    s = CompanySettings.get()
    a = request.POST.get("action")
    if a == "stop":
        control.stop(request.POST.get("note", "").strip() or "البرنامج موقوف مؤقتًا — برجاء التواصل مع Trio", "dev", s)
        messages.success(request, "تم إيقاف البرنامج لدى العميل ✔ — كل البيانات محفوظة")
    elif a == "resume":
        control.resume(s)
        messages.success(request, "البرنامج رجع يشتغل ✔")
    elif a == "url":
        s.control_url = request.POST.get("control_url", "").strip()[:400]
        s.save(update_fields=["control_url"])
        messages.success(request, "مراجعة أوامر الإيقاف: " + control.check(s))
    return redirect("/trio/setup/#control")


def activity_preview(request):
    """«ما نشاطك؟» → what the program will set up (JSON for the live preview)."""
    from django.http import JsonResponse

    from . import activities as A

    s = CompanySettings.get()
    owner = request.user.is_authenticated and (request.user.is_superuser or request.user.groups.filter(name="مدير المحل").exists())
    if s.setup_done and not is_dev(request) and not owner:
        return JsonResponse({"error": "غير مسموح"}, status=403)
    text = (request.GET.get("q") or "").strip()[:200]
    if len(text) < 2:
        return JsonResponse({"profile": None})
    return JsonResponse({"profile": A.view(A.lookup(request, text))})
