"""Daily summary to the shop owner: WhatsApp (a ready message opened by a button), Telegram and e-mail (sent by the program).

The channels are bought modules (report_telegram / report_email); the owner writes his e-mail and links his Telegram from
settings → «ملخص اليوم» (Trio's bot and mail service send — core/mobile.notify). Shops set up with their own bot / mailbox keep it.
Automatic sending: right after the final day close, or at the set hour if the day was not closed (the launcher checks hourly).
"""
import json
import smtplib
import ssl
import urllib.parse
import urllib.request
from email.message import EmailMessage

from django.utils import timezone

from .models import CompanySettings


def summary_text(day):
    from ui.shop import whatsapp_text

    from . import mobile

    text = whatsapp_text(day)
    if mobile.on() and mobile.link():
        text += f"\nتابع محلك لحظة بلحظة: {mobile.link()}"
    return text


def send_telegram(s, text):
    if not (s.telegram_token and s.telegram_chat):
        raise ValueError("اكتب توكن البوت ورقم المحادثة")
    for chat in [c.strip() for c in s.telegram_chat.split(",") if c.strip()]:
        data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
        with urllib.request.urlopen(f"https://api.telegram.org/bot{s.telegram_token}/sendMessage", data=data, timeout=20) as r:
            if not json.loads(r.read().decode()).get("ok"):
                raise ValueError("رفض تليجرام الرسالة — تأكد من التوكن ورقم المحادثة")


def telegram_chats(token):
    """After the owner opens the bot and presses Start, his chat id shows up here."""
    with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/getUpdates", timeout=20) as r:
        res = json.loads(r.read().decode()).get("result", [])
    out = {}
    for u in res:
        ch = (u.get("message") or u.get("my_chat_member") or {}).get("chat") or {}
        if ch.get("id"):
            out[str(ch["id"])] = ch.get("title") or " ".join(x for x in [ch.get("first_name"), ch.get("last_name")] if x) or ch.get("username", "")
    return out


def send_email(s, subject, text):
    if not (s.smtp_user and s.smtp_password and s.email_to):
        raise ValueError("اكتب بريد الإرسال وكلمة سر التطبيق والبريد المستقبِل")
    m = EmailMessage()
    m["Subject"], m["From"], m["To"] = subject, s.smtp_user, s.email_to
    m.set_content(text)
    with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=30) as smtp:
        smtp.starttls(context=ssl.create_default_context())
        smtp.login(s.smtp_user, s.smtp_password)
        smtp.send_message(m)


def send_mail_to(s, to, subject, text):
    """Branch managers' reports: the shop's mailbox if set, otherwise the program's mailbox."""
    if s.smtp_user and s.smtp_password:
        snd = dict(host=s.smtp_host, port=s.smtp_port, user=s.smtp_user, password=s.smtp_password)
    else:
        from .devaccess import sender

        snd = sender()
    if not snd:
        raise ValueError("لا يوجد بريد إرسال مُجهّز")
    from .devaccess import smtp_send

    m = EmailMessage()
    m["Subject"], m["From"], m["To"] = subject, snd["user"], to
    m.set_content(text)
    smtp_send(snd, m)


def _deliver(s, subject, text, out, label=""):
    """Telegram + e-mail to the owner. A shop with its own bot / mailbox (older set-ups) sends directly; otherwise Trio's
    Worker sends with Trio's bot and mail service — the owner only linked his Telegram and wrote his e-mail."""
    tag = f" — {label}" if label else ""
    via_tg = bool(s.report_telegram and not (s.telegram_token and s.telegram_chat))
    via_mail = bool(s.report_email and not (s.smtp_user and s.smtp_password) and s.email_to)
    if s.report_telegram and not via_tg:
        try:
            send_telegram(s, text)
            out.append((f"تليجرام{tag}", True, "أُرسل"))
        except Exception as e:  # noqa: BLE001
            out.append((f"تليجرام{tag}", False, str(e)))
    if s.report_email and not via_mail:
        if not s.email_to:
            out.append((f"البريد{tag}", False, "اكتب بريد صاحب المحل في الإعدادات ← ملخص اليوم"))
        else:
            try:
                send_email(s, subject, text)
                out.append((f"البريد{tag}", True, "أُرسل"))
            except Exception as e:  # noqa: BLE001
                out.append((f"البريد{tag}", False, str(e)))
    if via_tg or via_mail:
        from . import mobile

        try:
            res = mobile.notify(text, subject, telegram=via_tg, email=s.email_to if via_mail else "")
        except Exception as e:  # noqa: BLE001
            res = {k: str(e) for k, on in (("tg", via_tg), ("mail", via_mail)) if on}
        for k, name, on in (("tg", "تليجرام", via_tg), ("mail", "البريد", via_mail)):
            if on:
                ok = res.get(k) == "ok"
                out.append((f"{name}{tag}", ok, "تم الإرسال" if ok else str(res.get(k) or "لم يُرسل")))


def _owner_send(s, subject, text, out, label):
    _deliver(s, subject, text, out, label)


def send_branches(day, out):
    """Several branches: each manager gets his branch, the owner gets every branch in detail — daily, and the month / year once it ends."""
    from pos.models import Branch, Staff
    from ui import branches as UB

    s = CompanySettings.get()
    d1, d2, title = UB.period("day", day)
    rows, tot, _ = UB.all_report(d1, d2)
    _owner_send(s, f"تقرير الفروع — {s.name} — {day:%d/%m/%Y}", UB.text(rows, tot, f"تقرير الفروع {title}", s.name), out, "كل الفروع")
    managers = Staff.objects.filter(branch__isnull=False, user__is_active=True).exclude(email="").select_related("branch", "user")
    managers = [m for m in managers if "reports" in m.caps]
    for m in managers:
        r = next((x for x in rows if x["branch"].pk == m.branch_id), None)
        if r:
            try:
                send_mail_to(s, m.email, f"تقرير {m.branch.name} — {day:%d/%m/%Y}", UB.text([r], tot, f"{m.branch.name} — {title}", s.name, single=True))
                out.append((f"البريد {m.branch.name}", True, "أُرسل"))
            except Exception as e:  # noqa: BLE001
                out.append((f"البريد {m.branch.name}", False, str(e)))
    # the month / year that just ended
    first = day.replace(day=1)
    if day.day <= 7 and (s.last_month_report is None or s.last_month_report < first):
        prev = first - __import__("datetime").timedelta(days=1)
        p1, p2, ptitle = UB.period("month", prev)
        prow, ptot, _ = UB.all_report(p1, p2)
        _owner_send(s, f"تقرير الفروع الشهري — {ptitle}", UB.text(prow, ptot, f"التقرير الشهري لكل الفروع — {ptitle}", s.name), out, "شهري")
        for m in managers:
            r = next((x for x in prow if x["branch"].pk == m.branch_id), None)
            if r:
                try:
                    send_mail_to(s, m.email, f"التقرير الشهري — {m.branch.name} — {ptitle}", UB.text([r], ptot, f"{m.branch.name} — {ptitle}", s.name, single=True))
                except Exception as e:  # noqa: BLE001
                    out.append((f"شهري {m.branch.name}", False, str(e)))
        s.last_month_report = first
        if day.month == 1 and (s.last_year_report is None or s.last_year_report.year < day.year):
            y1, y2, ytitle = UB.period("year", prev)
            yrow, ytot, _ = UB.all_report(y1, y2)
            _owner_send(s, f"تقرير الفروع السنوي — {ytitle}", UB.text(yrow, ytot, f"التقرير السنوي لكل الفروع — {ytitle}", s.name), out, "سنوي")
            s.last_year_report = day
        s.save(update_fields=["last_month_report", "last_year_report"])


def send_daily(day=None, force=False):
    """Returns a list of (channel, ok, message)."""
    s = CompanySettings.get()
    day = day or timezone.localdate()
    if not force and s.last_report == day:
        return []
    out = []
    from pos.branches import branches_on

    if branches_on():
        send_branches(day, out)
        if any(ok for _, ok, _ in out):
            s.last_report = day
            s.save(update_fields=["last_report"])
        return out
    text = summary_text(day)
    _deliver(s, f"ملخص {s.name} — {day:%d/%m/%Y}", text, out)
    if any(ok for _, ok, _ in out):
        s.last_report = day
        s.save(update_fields=["last_report"])
    return out
