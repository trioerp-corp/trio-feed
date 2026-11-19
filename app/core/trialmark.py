"""The trial belongs to the computer, not to the program folder (1.22.2).

Reinstalling Trio (or installing it in another folder, or restoring a backup into a fresh copy) used to start a new 14-day
trial. Now the first day is kept in three places and the earliest one wins:
- the program's own database (CompanySettings.trial_started);
- a small marker outside the program folder (%ProgramData%\\Trio — removing the program does not remove it);
- Trio's server, by «كود الجهاز» (Windows' own machine id): sync() asks it every hour while the copy is on trial, and Trio can
  grant extra days from the phone panel.
"""
import datetime as dt
import json
import os
import urllib.request
from pathlib import Path


def _path():
    if os.environ.get("TRIO_TRIAL_MARK"):
        return Path(os.environ["TRIO_TRIAL_MARK"])
    base = os.environ.get("PROGRAMDATA") if os.name == "nt" else ""
    return Path(base) / "Trio" / "trial.json" if base else None


def read():
    p = _path()
    if not p:
        return None
    try:
        from .license import machine_code

        d = json.loads(p.read_text(encoding="utf-8"))
        if d.get("m") != machine_code():  # a marker copied from another computer means nothing here
            return None
        return dt.date.fromisoformat(d["t"])
    except Exception:  # noqa: BLE001 — missing / broken marker
        return None


def write(day):
    p = _path()
    if not p:
        return
    try:
        from .license import machine_code

        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(dict(m=machine_code(), t=day.isoformat())), encoding="utf-8")
    except Exception:  # noqa: BLE001 — no right to write there: the server still keeps the day
        pass


def earliest(day):
    """The first trial day of this computer: `day` (the database) or the marker, whichever is older; keeps the marker current."""
    mark = read()
    if mark and mark <= day:
        return mark
    write(day)
    return day


def sync(api, s, timeout=15):
    """While on trial: tell Trio's server the first day this copy knows, take back the computer's first day and extra days."""
    from .license import machine_code
    from .models import CompanySettings

    if not api or s.license_code:
        return ""
    start = earliest(s.trial_started or dt.date.today())
    try:
        body = json.dumps(dict(m=machine_code(), t=start.isoformat(), n=(s.name or "")[:80], b=s.business or "")).encode()
        req = urllib.request.Request(api.rstrip("/") + "/trial", data=body, headers={"Content-Type": "application/json", "User-Agent": "Trio"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read(2000).decode())
        first = dt.date.fromisoformat(d["t"])
        extra = max(0, min(int(d.get("x") or 0), 365))
    except Exception as e:  # noqa: BLE001 — no internet: the marker still holds
        return f"التجربة: ({type(e).__name__})"
    first = min(first, start)
    write(first)
    if s.trial_started != first or s.trial_extra != extra:
        CompanySettings.objects.filter(pk=s.pk).update(trial_started=first, trial_extra=extra)
        s.trial_started, s.trial_extra = first, extra
    return "التجربة ✔"
