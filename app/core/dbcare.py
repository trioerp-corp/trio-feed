"""Keeps the shop database healthy as it grows (runs with the hourly backup from the launcher):
- every day: SQLite quick_check — a damaged file is reported on the home page (restore from backups\\ before it spreads);
- every 30 days: VACUUM (gives back the space of deleted rows, re-packs the file) + PRAGMA optimize (fresh index statistics).
The database itself runs in WAL mode (config/settings.py): safer on a power cut, and the cashier never waits for a report."""
import time
from pathlib import Path

from django.conf import settings
from django.db import connection

DAY = 86400


def _marker(name):
    return Path(settings.BASE_DIR) / "data" / name


def _due(name, every):
    p = _marker(name)
    return not p.exists() or time.time() - p.stat().st_mtime > every


def _touch(name):
    p = _marker(name)
    p.parent.mkdir(exist_ok=True)
    p.write_text(time.strftime("%Y-%m-%d %H:%M"))


def problem():
    p = _marker("db_problem.txt")
    return p.read_text(encoding="utf-8") if p.exists() else ""


def maintain(force=False):
    if connection.vendor != "sqlite":
        return "ليست SQLite"
    done = []
    with connection.cursor() as c:
        if force or _due(".last_check", DAY - 600):
            c.execute("PRAGMA quick_check")
            res = [r[0] for r in c.fetchall()]
            bad = _marker("db_problem.txt")
            if res == ["ok"]:
                bad.unlink(missing_ok=True)
                done.append("الفحص سليم")
            else:
                bad.parent.mkdir(exist_ok=True)
                bad.write_text("توجد مشكلة في قاعدة البيانات — تواصل مع Trio قبل أي شيء: " + "; ".join(res[:5]), encoding="utf-8")
                done.append("في مشكلة")
            _touch(".last_check")
        if (force or _due(".last_vacuum", 30 * DAY)) and not problem():
            c.execute("PRAGMA optimize")
            c.execute("VACUUM")
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            _touch(".last_vacuum")
            done.append("ضُغطت ونُظّفت")
    return " — ".join(done) or "لم يحن وقتها بعد"
