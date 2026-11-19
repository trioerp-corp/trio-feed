"""Forced stop of a client's copy by Trio (fees dispute, unpaid installation…). Nothing is deleted: when it is turned back
on, the program continues exactly where it stopped.

Two ways:
1) At the client (or online through the client's link): developer interface (/trio/, e-mailed code) → «إيقاف البرنامج».
2) From anywhere: Trio-License.exe (option 4) signs the list of machines to stop and e-mails it to the program's mailbox
   (trioerp.corp@gmail.com, subject TRIO-CONTROL). Every client reads that mailbox every hour (IMAP, with the same app password
   used for the codes) and applies the newest signed list. Only Trio's private key can make a valid list, so a client
   cannot forge a «resume». Remove the machine from the list (new message) → it runs again within the hour.
   (A public link to a signed file, control_url, still works as an alternative.)
"""
import base64
import datetime as dt
import json
import urllib.request
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from . import license as LIC
from .models import CompanySettings

TAG = "TRIOC"


def stop(note="", source="dev", s=None):
    s = s or CompanySettings.get()
    s.suspended, s.suspend_note, s.suspend_source, s.suspended_at = True, (note or "")[:200], source, timezone.now()
    s.save(update_fields=["suspended", "suspend_note", "suspend_source", "suspended_at"])
    _audit("stop", f"إيقاف البرنامج ({source}): {note}")


def resume(s=None, source="dev"):
    s = s or CompanySettings.get()
    s.suspended, s.suspend_source = False, ""
    s.save(update_fields=["suspended", "suspend_source"])
    _audit("resume", f"تشغيل البرنامج مرة أخرى ({source})")


def _audit(action, summary):
    try:
        from . import audit

        audit.log(action, summary=summary, model="control", object_id=LIC.machine_code())
    except Exception:  # noqa: BLE001
        pass


def parse(text):
    """Signed control file → dict(stop={machine: reason}, t=stamp). Raises ValueError if not signed by Trio."""
    from cryptography.exceptions import InvalidSignature

    try:
        d = LIC.signed(text, TAG)
        assert isinstance(d.get("stop"), dict) and d.get("t")
    except (ValueError, AssertionError, InvalidSignature):
        raise ValueError("ملف التحكم غير موقّع من Trio")
    return d


def apply(d, s=None):
    """Apply a verified control file. Older files than the last one applied are ignored (no replay of an old «resume»)."""
    s = s or CompanySettings.get()
    if s.control_stamp and d["t"] <= s.control_stamp:
        return "قديم"
    me = LIC.machine_code()
    s.control_stamp = d["t"]
    s.save(update_fields=["control_stamp"])
    if me in d["stop"]:
        if not s.suspended:
            stop(d["stop"][me] or "أُوقف من Trio", "remote", s)
        return "موقوف"
    if s.suspended and s.suspend_source == "remote":
        resume(s, "remote")
        return "تم التشغيل"
    return "شغال"


def url(s=None):
    s = s or CompanySettings.get()
    if s.control_url:
        return s.control_url
    p = Path(settings.BASE_DIR) / "client" / "acc_control.txt"
    return p.read_text(encoding="utf-8").strip() if p.exists() else ""


SUBJECT = "TRIO-CONTROL"
CODE_RE = __import__("re").compile(r"TRIOC(?:\.[A-Za-z0-9_-]+){2}(?:\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)?")


def mailbox_codes(snd, imap=None, limit=10):
    """Signed control lists found in the program's mailbox (newest messages first)."""
    import email
    import imaplib

    m = imap or imaplib.IMAP4_SSL(snd.get("imap_host") or "imap.gmail.com", 993, timeout=25)
    try:
        m.login(snd["user"], snd["password"].replace(" ", ""))
        boxes = ["INBOX"]
        try:  # Gmail may keep a message sent to yourself only in «All Mail» (its name depends on the account language)
            typ, lst = m.list()
            boxes += [l.decode().rsplit(' "/" ', 1)[-1] for l in (lst or []) if isinstance(l, bytes) and b"\\All" in l]
        except Exception:  # noqa: BLE001
            pass
        out = []
        for box in boxes:
            m.select(box, readonly=True)
            typ, data = m.search(None, f'(SUBJECT "{SUBJECT}")')
            ids = (data[0] or b"").split()[-limit:]
            if not ids:  # a server that does not decode the encoded subject: read the newest messages directly
                typ, data = m.search(None, "ALL")
                ids = (data[0] or b"").split()[-3 * limit:]
            out += _read(m, ids)
            if out:
                break
        return out
    finally:
        try:
            m.logout()
        except Exception:  # noqa: BLE001
            pass


def _read(m, ids):
    import email

    out = []
    if True:
        for i in reversed(ids):
            typ, msg = m.fetch(i, "(RFC822)")
            raw = msg[0][1] if msg and msg[0] else b""
            mm = email.message_from_bytes(raw)
            parts = [mm] if not mm.is_multipart() else [p for p in mm.walk() if p.get_content_type() == "text/plain"]
            for p in parts:
                body = (p.get_payload(decode=True) or b"").decode("utf-8", "ignore")
                out += CODE_RE.findall("".join(body.split()))
    return out


def newest(codes):
    best = None
    for c in codes:
        try:
            d = parse(c)
        except ValueError:
            continue
        if best is None or d["t"] > best["t"]:
            best = d
    return best


def check(s=None, fetch=None, imap=None):
    """Hourly from the launcher: the program's mailbox (and/or the control link). No internet → nothing changes."""
    s = s or CompanySettings.get()
    link = url(s)
    from .devaccess import sender

    snd = sender()
    if not link and not snd:
        return "لا يوجد بريد إرسال ولا رابط تحكم"
    try:
        codes = []
        if snd:
            codes += mailbox_codes(snd, imap=imap)
        if link:
            if fetch:
                codes.append(fetch(link))
            else:
                req = urllib.request.Request(link, headers={"User-Agent": "Trio"})
                with urllib.request.urlopen(req, timeout=20) as r:
                    codes.append(r.read(20000).decode("utf-8", "ignore"))
        best = newest(codes)
        if best is None:
            res = "تعذّرت المراجعة (أمر غير موقّع من Trio)" if codes else "لا توجد أوامر"
        else:
            res = apply(best, s)
    except Exception as e:  # noqa: BLE001
        res = f"تعذّرت المراجعة ({type(e).__name__})"
    s.control_checked = timezone.now()
    s.save(update_fields=["control_checked"])
    return res
