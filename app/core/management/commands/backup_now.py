"""Automatic backup: a consistent copy of the database (SQLite online backup), zipped, kept 30 days locally
(backups\\) and copied to the Google Drive folder when one is set (the Google Drive app uploads it online).

Usage: python manage.py backup_now [--if-older-than-hours 12]
"""
import datetime as dt
import shutil
import sqlite3
import zipfile
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection
from django.utils import timezone

from core.models import CompanySettings

KEEP_DAYS = 30


def drive_candidates():
    home = Path.home()
    c = [Path("G:/My Drive"), Path("G:/محرك Drive الخاص بي"), home / "Google Drive", home / "My Drive", Path("H:/My Drive")]
    return [p for p in c if p.exists()]


def run_backup(force=True, older_than_hours=0):
    s = CompanySettings.get()
    if not force and s.last_backup and timezone.now() - s.last_backup < dt.timedelta(hours=older_than_hours):
        return None, "آخر نسخة ما زالت حديثة"
    if connection.vendor != "sqlite":
        return None, "هذا النسخ التلقائي لقاعدة SQLite"
    db = Path(settings.DATABASES["default"]["NAME"])
    out_dir = Path(settings.BASE_DIR) / "backups"
    out_dir.mkdir(exist_ok=True)
    stamp = timezone.localtime().strftime("%Y-%m-%d_%H%M%S")  # seconds: a safety copy right after another never overwrites it
    tmp = out_dir / f"shop_{stamp}.sqlite3"
    src = sqlite3.connect(str(db))
    dst = sqlite3.connect(str(tmp))
    with dst:
        src.backup(dst)
    dst.close()
    src.close()
    z = out_dir / f"shop_{stamp}.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(tmp, tmp.name)
    tmp.unlink()
    cutoff = timezone.now().timestamp() - KEEP_DAYS * 86400
    for old in out_dir.glob("shop_*.zip"):
        if old.stat().st_mtime < cutoff:
            old.unlink()
    msg = f"نسخة على الجهاز: {z}"
    drive = Path(s.backup_drive_dir) if s.backup_drive_dir else None
    if drive:
        from core import backups as BK

        try:
            drive.mkdir(parents=True, exist_ok=True)
            out = drive / (z.stem + ".trio")  # encrypted: the Drive copy is readable only with the shop's recovery key
            tmp = out.with_suffix(".part")
            tmp.write_bytes(BK.encrypt(z.read_bytes(), BK.key(s)))
            tmp.replace(out)
            for old in list(drive.glob("shop_*.zip")) + list(drive.glob("shop_*.trio")):
                if old.stat().st_mtime < cutoff:
                    old.unlink()
            msg += f" — ونسخة مشفّرة على جوجل درايف: {out}"
        except OSError as e:
            msg += f" — جوجل درايف غير متاح ({e})"
        BK.escrow(s)
    s.last_backup = timezone.now()
    s.save(update_fields=["last_backup"])
    return z, msg


class Command(BaseCommand):
    help = "نسخة احتياطية (على الجهاز + جوجل درايف)"

    def add_arguments(self, p):
        p.add_argument("--if-older-than-hours", type=float, default=0)

    def handle(self, if_older_than_hours=0, **o):
        z, msg = run_backup(force=not if_older_than_hours, older_than_hours=if_older_than_hours)
        self.stdout.write(msg)
        from core import dbcare

        try:
            self.stdout.write("صيانة قاعدة البيانات: " + dbcare.maintain())
        except Exception as e:  # noqa: BLE001 — never stop the backup loop
            self.stdout.write(f"الصيانة متعملتش ({e})")
