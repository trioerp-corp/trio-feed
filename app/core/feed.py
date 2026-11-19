"""The ACC's command channel: one public file of signed codes that every copy reads hourly — no password on the client.

The licence code carries the channel's address (field «f», put there by Trio License once the channel is set up), so a copy
is connected the moment its licence is pasted: from the flash drive or downloaded online, the same way. The file holds:
  TRIOC  — the forced-stop list (the newest applies),
  TRIOM  — the newest order for each machine (plan / parts, and a new licence: renewal, lifetime),
  TRIOUP — the newest release with the address of its zip («u»), for the machines that may take it («to»).
Everything is signed with Trio's private key, so a copy of the file cannot be changed or forged.

A licensed copy that has not reached the channel for OFFLINE_DAYS stops (after a warning) until it is online once — so a
forced stop, a renewal or an update can always reach it.
"""
import datetime as dt
import hashlib
import json
import re
import urllib.request
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from . import license as LIC

OFFLINE_DAYS = 30
WARN_DAYS = 7
CODE_RE = re.compile(r"TRIO(?:C|M|UP|P|K)(?:\.[A-Za-z0-9_-]+){2}(?:\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)?")


def _state_file():
    p = Path(settings.BASE_DIR) / "data" / "feed.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def state():
    p = _state_file()
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(**kw):
    st = state()
    st.update(kw)
    _state_file().write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    return st


# the GitHub account / repository were renamed (accerp-corp/acc-feed → trioerp-corp/trio-feed): licences and update codes
# signed before that still carry the old address — GitHub forwards it today, but only until someone takes the old name.
MOVED = {"/accerp-corp/acc-feed/": "/trioerp-corp/trio-feed/"}


def moved(url):
    for a, b in MOVED.items():
        url = url.replace(a, b)
    return url


def feed_url(s=None):
    from .models import CompanySettings

    s = s or CompanySettings.get()
    try:
        f = LIC.parse(s.license_code).get("f", "") if s.license_code else ""
    except ValueError:
        f = ""
    if not f:  # a downloaded copy (trial): the channel address that came with the download
        p = Path(settings.BASE_DIR) / "client" / "acc_feed.txt"
        try:
            f = p.read_text(encoding="utf-8").strip() if p.exists() else ""
        except OSError:
            f = ""
    f = moved(f)
    return f if f.startswith("https://") or f.startswith("http://127.0.0.1") else ""


def _get(url, limit=4 << 20, timeout=25):
    sep = "&" if "?" in url else "?"
    req = urllib.request.Request(f"{url}{sep}t={int(timezone.now().timestamp()) // 300}", headers={"User-Agent": "Trio"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(limit)


def offline(s=None, today=None):
    """None when connected (or no channel in the licence); else dict(days, left, blocked)."""
    if not feed_url(s):
        return None
    st = state()
    if not st.get("since"):  # the channel was just added: the count starts now
        st = _save(since=timezone.now().isoformat())
    last = st.get("ok") or st.get("since")
    try:
        last_day = dt.datetime.fromisoformat(last).date()
    except (TypeError, ValueError):
        return None
    today = today or timezone.localdate()
    days = (today - last_day).days
    left = OFFLINE_DAYS - days
    if left > WARN_DAYS:
        return None
    return dict(days=days, left=left, blocked=left < 0)


def check(s=None, fetch=None):
    """Hourly from the launcher (with the mailbox check). No internet → nothing changes."""
    from . import control, updates
    from .models import CompanySettings

    s = s or CompanySettings.get()
    url = feed_url(s)
    if not url:
        return "لا توجد قناة أوامر في الرخصة"
    try:
        raw = fetch(url) if fetch else _get(url)
        text = raw.decode("utf-8", "ignore") if isinstance(raw, bytes) else raw
    except Exception as e:  # noqa: BLE001
        _save(failed=timezone.now().isoformat(), error=type(e).__name__)
        return f"قناة الأوامر: تعذّر الاتصال ({type(e).__name__})"
    codes = CODE_RE.findall(text)  # one code per line
    out = []
    # 0) first: keys Trio stopped (sales / phone / update key) — what they signed is refused from this round on
    rev = dict(state().get("revoked", {}))
    for c in codes:
        if c.startswith("TRIOK."):
            try:
                kp = updates.verify(c, "TRIOK")
            except ValueError:
                continue
            if kp.get("r") and kp.get("k") and kp["k"] not in rev:
                rev[kp["k"]] = str(kp.get("t", "")) or "0"
    if rev:
        _save(revoked=rev)
    # 1) forced stop / resume
    best = control.newest([c for c in codes if c.startswith("TRIOC.")])
    if best:
        out.append("الإيقاف: " + control.apply(best, s))
    # 2) plan / parts / licence for this machine
    me, order = LIC.machine_code(), None
    for c in codes:
        if c.startswith("TRIOM."):
            try:
                p = updates.verify(c, "TRIOM")
            except ValueError:
                continue
            if p.get("m") == me and (order is None or p["t"] > order["t"]):
                order = p
    if order:
        out.append("الترقيات: " + updates.apply_modules(order, CompanySettings.get()))
    # 3) a newer release meant for this machine (and the newest release at all: «متاح إصدار أحدث»)
    rel, latest = None, None
    for c in codes:
        if c.startswith("TRIOUP."):
            try:
                p = updates.verify(c, "TRIOUP")
            except ValueError:
                continue
            if updates.held(p):
                continue
            if latest is None or updates.newer(p.get("v", "0"), latest.get("v", "0")):
                latest = p
            if p.get("u") and updates.newer(p.get("v", "0")) and updates._for_me(p.get("to", "*")) and (rel is None or updates.newer(p["v"], rel["v"])):
                rel = p
    if rel:
        st = updates.state()
        if st["ready"] and not updates.newer(rel["v"], st["ready"]["v"]):
            out.append(f"التحديثات: {st['ready']['v']} جاهز")
        else:
            try:
                data = fetch(moved(rel["u"])) if fetch else _get(moved(rel["u"]), limit=200 << 20, timeout=300)
                if isinstance(data, str):
                    data = data.encode()
                updates.stage(rel, data)
                out.append(f"التحديثات: {rel['v']} تم التنزيل ✔")
            except Exception as e:  # noqa: BLE001
                out.append(f"التحديثات: تعذّر تنزيل {rel['v']} ({type(e).__name__})")
    if latest:
        _save(latest=latest.get("v", ""), latest_notes=latest.get("notes", ""))
    # 4) Trio's presence address: this copy says once a day which version it runs (shown in Trio License)
    for c in codes:
        if c.startswith("TRIOP."):
            try:
                pp = updates.verify(c, "TRIOP")
            except ValueError:
                continue
            if pp.get("_k") and pp.get("u", "").rstrip("/") != state().get("api", ""):
                continue  # the phone app may switch payments / AI on and off, never move Trio's address
            if pp.get("u"):
                _save(api=pp["u"].rstrip("/"), api_pay=bool(pp.get("pay")), api_ai=bool(pp.get("ai")))  # Trio's online address: presence + card payments
            out.append(ping(pp.get("u", ""), s))
            if not s.license_code:  # the trial belongs to this computer (1.22.2)
                from . import trialmark

                out.append(trialmark.sync(pp.get("u", ""), s))
            break
    _save(ok=timezone.now().isoformat(), error="")
    if state().get("pay_order"):  # paid by card but the licence did not come back yet (closed the page, no internet…)
        from . import pay

        try:
            r = pay.poll(s)
            if r:
                out.append("الدفع: " + r)
        except Exception:  # noqa: BLE001
            pass
    s.control_checked = timezone.now()
    s.save(update_fields=["control_checked"])
    return "قناة الأوامر ✔ " + (" — ".join(out) if out else "لا جديد")


def ping(url, s=None, force=False):
    """Once a day (or when the version changed): machine, version, plan, licence state → Trio's presence address."""
    from .models import CompanySettings
    from .version import VERSION

    if not url:
        return ""
    st = state()
    today = timezone.localdate().isoformat()
    if not force and st.get("ping_day") == today and st.get("ping_v") == VERSION:
        return ""
    s = s or CompanySettings.get()
    try:
        lic = LIC.status(s)
        body = json.dumps(dict(m=LIC.machine_code(), v=VERSION, p=s.plan, b=s.business, s=lic.get("state", ""), n=(s.name or "")[:80], ph=(s.phone or "")[:30])).encode()
        req = urllib.request.Request(url.rstrip("/") + "/ping", data=body, headers={"Content-Type": "application/json", "User-Agent": "Trio"})
        with urllib.request.urlopen(req, timeout=20) as r:
            r.read(100)
        _save(ping_day=today, ping_v=VERSION)
        return "الحضور ✔"
    except Exception as e:  # noqa: BLE001
        return f"الحضور: ({type(e).__name__})"


def newer_available():
    """The newest version in Trio's channel when it is newer than this copy (else "")."""
    from .version import newer

    v = state().get("latest", "")
    return v if v and newer(v) else ""
