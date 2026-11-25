"""Remote updates and remote upgrades, through the program mailbox (no server needed).

Update  (Trio License «نشر تحديث»): an e-mail «ACC-UPDATE <v>» with the update zip attached and a code
        TRIOUP.<payload>.<sig> — payload {v, notes, sha (sha256 of the zip), n (bytes), to ("*" or [machine codes]), t}, signed with
        Trio's private key. The client checks the mailbox hourly (with the remote-stop check), verifies the signature and the
        hash, and stages the zip in data/update/. The launcher installs it at a safe time (night, or when the owner presses
        «حدّث الآن», or at the next start): backup → replace program files → migrate → rollback if anything fails.
Upgrade (Trio License «ترقية عميل»): an e-mail «ACC-MODULES» with TRIOM.<payload>.<sig> — {m: machine, p: plan, x: [extra modules],
        b: business (optional), t} — the newest order for this machine is applied (plan + modules), once.
"""
import base64
import datetime as dt
import hashlib
import json
import re
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from . import license as LIC
from .version import VERSION, newer

UPD_RE = re.compile(r"TRIOUP(?:\.[A-Za-z0-9_-]+){2}(?:\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)?")  # Trio's key, or its certified update key
HOLD = 2 * 3600  # seconds a release published from the phone (update key) waits before copies install it — time to stop it


def held(p):
    """A release signed with the phone's update key is installed only HOLD after it was signed (Trio gets an e-mail at once and
    can stop the key in the meantime). Releases from Trio License (main key) go at once."""
    if not p.get("_k"):
        return False
    import datetime as dt

    try:
        t = dt.datetime.fromisoformat(str(p.get("t", "")).replace("Z", "+00:00"))
    except ValueError:
        return True
    return (dt.datetime.now(dt.timezone.utc) - t).total_seconds() < HOLD
MOD_RE = re.compile(r"TRIOM(?:\.[A-Za-z0-9_-]+){2}(?:\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)?")


def folder():
    p = Path(settings.BASE_DIR) / "data" / "update"
    p.mkdir(parents=True, exist_ok=True)
    return p


def verify(code, tag):
    """Payload of a code signed by Trio — a certified phone key is fine for the tags in license.ADMIN_TAGS."""
    return LIC.signed(code, tag)


def _for_me(to):
    return to in ("*", "", None) or LIC.machine_code() in (to if isinstance(to, list) else [to])


def _messages(snd, subject, imap=None, limit=8):
    """(body text, [(filename, bytes)]) of the newest messages with this subject — INBOX, then «All Mail»."""
    import email
    import imaplib

    m = imap or imaplib.IMAP4_SSL(snd.get("imap_host") or "imap.gmail.com", 993, timeout=40)
    out = []
    try:
        m.login(snd["user"], snd["password"].replace(" ", ""))
        boxes = ["INBOX"]
        try:
            typ, lst = m.list()
            boxes += [l.decode().rsplit(' "/" ', 1)[-1] for l in (lst or []) if isinstance(l, bytes) and b"\\All" in l]
        except Exception:  # noqa: BLE001
            pass
        for box in boxes:
            m.select(box, readonly=True)
            typ, data = m.search(None, f'(SUBJECT "{subject}")')
            ids = (data[0] or b"").split()[-limit:]
            for i in reversed(ids):
                typ, msg = m.fetch(i, "(RFC822)")
                raw = msg[0][1] if msg and msg[0] else b""
                mm = email.message_from_bytes(raw)
                text, files = "", []
                for part in (mm.walk() if mm.is_multipart() else [mm]):
                    fn = part.get_filename()
                    if fn:
                        files.append((fn, part.get_payload(decode=True) or b""))
                    elif part.get_content_type() == "text/plain":
                        text += (part.get_payload(decode=True) or b"").decode("utf-8", "ignore")
                out.append(("".join(text.split()), files))
            if out:
                break
    finally:
        try:
            m.logout()
        except Exception:  # noqa: BLE001
            pass
    return out


def state():
    f = folder()
    rd = json.loads((f / "ready.json").read_text(encoding="utf-8")) if (f / "ready.json").exists() else None
    ap = json.loads((f / "applied.json").read_text(encoding="utf-8")) if (f / "applied.json").exists() else None
    return dict(version=VERSION, ready=rd, applied=ap, seen=(f / "seen.txt").read_text().strip() if (f / "seen.txt").exists() else "",
                failed=(f / "failed.txt").read_text(encoding="utf-8") if (f / "failed.txt").exists() else "")


def stage(payload, data):
    """Keep a verified update for the launcher."""
    if hashlib.sha256(data).hexdigest() != payload["sha"]:
        raise ValueError("وصل ملف التحديث ناقصًا")
    f = folder()
    zp = f / f"trio-{payload['v']}.zip"
    zp.write_bytes(data)
    (f / "ready.json").write_text(json.dumps(dict(v=payload["v"], notes=payload.get("notes", ""), file=zp.name, sha=payload["sha"],
                                                  at=timezone.now().isoformat()), ensure_ascii=False), encoding="utf-8")
    return zp


def delta_for_me(p):
    """A release with a light update made from exactly the version this copy runs (1.26): {from, ud (address), sd (sha256)}."""
    return bool(p.get("ud") and p.get("sd") and p.get("from") and str(p["from"]) == VERSION)


def stage_delta(payload, data):
    """Keep a verified light update (only the changed files + the list of deleted ones) for the launcher."""
    if hashlib.sha256(data).hexdigest() != payload["sd"]:
        raise ValueError("وصل ملف التحديث ناقصًا")
    f = folder()
    zp = f / f"trio-{payload['v']}-from-{payload['from']}.zip"
    zp.write_bytes(data)
    (f / "ready.json").write_text(json.dumps(dict(v=payload["v"], notes=payload.get("notes", ""), file=zp.name, sha=payload["sd"], delta=True,
                                                  **{"from": payload["from"]}, at=timezone.now().isoformat()), ensure_ascii=False), encoding="utf-8")
    return zp


def check_updates(snd, imap=None):
    best = None
    for text, files in _messages(snd, "ACC-UPDATE", imap=imap):
        for code in UPD_RE.findall(text):
            try:
                p = verify(code, "TRIOUP")
            except ValueError:
                continue
            if held(p):
                continue
            if not newer(p.get("v", "0")) or not _for_me(p.get("to", "*")):
                continue
            z = next((d for fn, d in files if fn.lower().endswith(".zip")), None)
            if z is None:
                continue
            if best is None or newer(p["v"], best[0]["v"]):
                best = (p, z)
    if not best:
        return "لا يوجد تحديث جديد"
    st = state()
    if st["ready"] and not newer(best[0]["v"], st["ready"]["v"]):
        return f"تحديث {st['ready']['v']} جاهز ومستني يتركّب"
    stage(*best)
    return f"تحديث {best[0]['v']} تم التنزيل ✔ — سيُثبَّت الليلة أو من «حدّث الآن»"


def apply_modules(p, s=None):
    from . import modules as M
    from .models import CompanySettings

    s = s or CompanySettings.get()
    if p.get("m") != LIC.machine_code():
        return "ليس لهذا الجهاز"
    if s.modules_stamp and p["t"] <= s.modules_stamp:
        return "قديم"
    lic_note = ""
    if p.get("lic"):  # a new licence (renewal, lifetime, lifetime updates) sent with the order
        try:
            new = LIC.parse(p["lic"])
            if new.get("m") == LIC.machine_code():
                s.license_code = "".join(p["lic"].split())
                lic_note = "رخصة مدى الحياة" if new.get("l") else f"جُدّدت الرخصة حتى {new.get('e', '')}"
                if new.get("u"):
                    lic_note += " + التحديثات مدى الحياة"
        except ValueError:
            pass
    if not any(k in p for k in ("p", "x", "n")):  # only a new licence: the parts stay as they are
        s.modules_stamp = p["t"]
        s.save()
        f = folder()
        (f / "upgraded.json").write_text(json.dumps(dict(plan=M.PLANS.get(s.plan, ("",))[0], added=[], removed=[], licence=lic_note,
                                                         at=timezone.now().isoformat()), ensure_ascii=False), encoding="utf-8")
        (f / "seen-upgrade.txt").unlink(missing_ok=True)
        LIC.parse.cache_clear()
        return lic_note or "لا جديد"
    business = s.business if s.setup_done or p.get("b") not in M.BUSINESS else p.get("b")  # fixed after set-up (1.23)
    plan = p.get("p") if p.get("p") in M.PLANS else s.plan
    mods = (set(M.defaults(plan, business)) | {x for x in p.get("x", []) if x in M.MODULES}) - (set(p.get("n") or []) - set(M.ALWAYS))
    mods = [m for m in M.MODULES if m in mods and M.fits(m, business)]
    before = M.enabled(s)
    added = [M.MODULES[m][0] for m in mods if m not in before]
    removed = [M.MODULES[m][0] for m in before if m not in mods]
    s.plan, s.business, s.modules, s.modules_stamp = plan, business, mods, p["t"]
    s.report_whatsapp, s.report_telegram, s.report_email = "report_whatsapp" in mods, "report_telegram" in mods, "report_email" in mods
    s.save()
    f = folder()
    (f / "upgraded.json").write_text(json.dumps(dict(plan=M.PLANS[plan][0], added=added, removed=removed, licence=lic_note,
                                                     at=timezone.now().isoformat()), ensure_ascii=False), encoding="utf-8")
    (f / "seen-upgrade.txt").unlink(missing_ok=True)
    LIC.parse.cache_clear()
    return "تم تفعيل: " + "، ".join([x for x in ["، ".join(added), lic_note] if x] or [M.PLANS[plan][0]]) + (f" — أُغلق: {'، '.join(removed)}" if removed else "")


def check_modules(snd, imap=None):
    best = None
    for text, _ in _messages(snd, "ACC-MODULES", imap=imap):
        for code in MOD_RE.findall(text):
            try:
                p = verify(code, "TRIOM")
            except ValueError:
                continue
            if p.get("m") == LIC.machine_code() and (best is None or p["t"] > best["t"]):
                best = p
    return apply_modules(best) if best else "لا توجد ترقيات"


def check(imap_factory=None):
    """Hourly (with the remote-stop check): updates + upgrades. No mailbox / no internet → nothing happens."""
    from .devaccess import sender

    snd = sender()
    if not snd:
        return "لا يوجد بريد البرنامج"
    from . import custom

    out = []
    for name, fn in (("التحديثات", check_updates), ("الترقيات", check_modules), ("التعديلات الخاصة", custom.check_mail)):
        try:
            out.append(f"{name}: {fn(snd, imap=imap_factory() if imap_factory else None)}")
        except Exception as e:  # noqa: BLE001
            out.append(f"{name}: تعذّرت المراجعة ({type(e).__name__})")
    return " — ".join(out)


def apply_licence(lic, s=None):
    """A pasted licence that carries the plan (p), business (b) and extra parts (x): the copy runs exactly what was paid for
    (a demo becoming a paid copy, or a new licence pasted by hand)."""
    from . import modules as M
    from .models import CompanySettings

    s = s or CompanySettings.get()
    if lic.get("p") not in M.PLANS:
        return ""
    business = s.business if s.setup_done or lic.get("b") not in M.BUSINESS else lic.get("b")  # fixed after set-up (1.23)
    before = M.enabled(s)
    mods = set(M.defaults(lic["p"], business)) | {x for x in lic.get("x") or [] if x in M.MODULES}
    mods = [m for m in M.MODULES if m in mods and M.fits(m, business)]
    added = [M.MODULES[m][0] for m in mods if m not in before]
    s.plan, s.business, s.modules = lic["p"], business, mods
    s.report_whatsapp, s.report_telegram, s.report_email = "report_whatsapp" in mods, "report_telegram" in mods, "report_email" in mods
    s.save()
    f = folder()
    (f / "upgraded.json").write_text(json.dumps(dict(plan=M.PLANS[lic["p"]][0], added=added, removed=[], licence="",
                                                     at=timezone.now().isoformat()), ensure_ascii=False), encoding="utf-8")
    (f / "seen-upgrade.txt").unlink(missing_ok=True)
    return M.PLANS[lic["p"]][0]
