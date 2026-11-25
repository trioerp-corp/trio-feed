"""Backups the owner can read back: the Google Drive copy is encrypted (AES-256-GCM, key from the shop's «مفتاح الاسترجاع»),
the local copy in backups\\ stays a plain zip (it sits next to the database anyway). Restoring checks the file first (a real
Trio database, not damaged), takes a safety copy of today's data, swaps the data in place and brings it to this version.

The recovery key lives in the database, on a card the owner prints, and with Trio (the Worker keeps it per machine — sent
once), so a dead computer with a lost card still comes back from the Drive copy.
"""
import datetime as dt
import hashlib
import io
import os
import secrets
import sqlite3
import tempfile
import zipfile
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError

MAGIC = b"TRIOBK1"
ALPH = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I: read out over the phone
KEEP = ("license_code", "backup_key", "backup_key_sent", "mobile_key", "clock_day", "trial_started")  # this copy's identity survives a restore


def new_key():
    raw = "".join(secrets.choice(ALPH) for _ in range(24))
    return "-".join(raw[i:i + 4] for i in range(0, 24, 4))


def norm_key(k):
    k = "".join(ch for ch in (k or "").upper() if ch.isalnum()).replace("0", "O").replace("1", "I")
    return "-".join(k[i:i + 4] for i in range(0, len(k), 4))


def key(s=None):
    from core.models import CompanySettings

    s = s or CompanySettings.get()
    if not s.backup_key:
        s.backup_key, s.backup_key_sent = new_key(), False
        s.save(update_fields=["backup_key", "backup_key_sent"])
    return s.backup_key


def _kdf(k, salt):
    return hashlib.scrypt(norm_key(k).encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)


def encrypt(data, k):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    salt, nonce = os.urandom(16), os.urandom(12)
    return MAGIC + salt + nonce + AESGCM(_kdf(k, salt)).encrypt(nonce, data, MAGIC)


def decrypt(blob, k):
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if not blob.startswith(MAGIC):
        return blob
    salt, nonce, ct = blob[7:23], blob[23:35], blob[35:]
    try:
        return AESGCM(_kdf(k, salt)).decrypt(nonce, ct, MAGIC)
    except InvalidTag:
        raise ValidationError("مفتاح الاسترجاع غير صحيح لهذه النسخة (أو الملف تالف)")


def escrow(s=None):
    """Sends the recovery key to Trio once (best effort; retried with the next backup)."""
    from core import license as LIC
    from core import mobile
    from core.models import CompanySettings

    s = s or CompanySettings.get()
    k = key(s)
    if s.backup_key_sent:
        return True
    try:
        res = mobile.post("/bk", dict(m=LIC.machine_code(), key=k, shop=s.name, lic="".join((s.license_code or "").split())), timeout=8)
    except Exception:  # noqa: BLE001 — offline: next time
        return False
    if res.get("ok"):
        CompanySettings.objects.filter(pk=s.pk).update(backup_key_sent=True)
        return True
    return False


def folders():
    from core.models import CompanySettings

    out = [("الجهاز", Path(settings.BASE_DIR) / "backups")]
    s = CompanySettings.get()
    if s.backup_drive_dir:
        out.append(("جوجل درايف", Path(s.backup_drive_dir)))
    return out


def listing():
    rows = []
    for where, d in folders():
        try:
            files = list(d.glob("shop_*.zip")) + list(d.glob("shop_*.trio"))
        except OSError:
            continue
        for f in files:
            try:
                st = f.stat()
            except OSError:
                continue
            rows.append(dict(where=where, path=str(f), name=f.name, size=round(st.st_size / 1048576, 1),
                             at=dt.datetime.fromtimestamp(st.st_mtime), locked=f.suffix == ".trio"))
    return sorted(rows, key=lambda r: r["at"], reverse=True)[:60]


def allowed_path(p):
    """Only a file inside the backup folders can be picked from the list (never any path typed into the form)."""
    p = Path(p).resolve()
    return any(p.parent == d.resolve() for _, d in folders()) and p.name.startswith("shop_") and p.suffix in (".zip", ".trio")


def unpack(blob, k=""):
    """Backup file bytes (zip / encrypted) → the SQLite bytes, or a plain Arabic reason."""
    blob = decrypt(blob, k or key())
    try:
        zf = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile:
        if blob[:16] == b"SQLite format 3\x00":
            return blob
        raise ValidationError("هذا الملف ليس نسخة احتياطية من Trio")
    names = [n for n in zf.namelist() if n.endswith(".sqlite3")]
    if len(names) != 1:
        raise ValidationError("هذا الملف ليس نسخة احتياطية من Trio")
    return zf.read(names[0])


def inspect(db_bytes):
    """Saves the database to a waiting file and checks it → (path, facts for the owner to compare)."""
    tmpd = Path(settings.BASE_DIR) / "data" / "restore"
    tmpd.mkdir(parents=True, exist_ok=True)
    for old in tmpd.glob("*.sqlite3"):
        if old.stat().st_mtime < dt.datetime.now().timestamp() - 3600:
            old.unlink(missing_ok=True)
    fd, path = tempfile.mkstemp(suffix=".sqlite3", dir=tmpd)
    with os.fdopen(fd, "wb") as f:
        f.write(db_bytes)
    try:
        con = sqlite3.connect(path)
        if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValidationError("هذه النسخة تالفة — جرّب نسخة أقدم")
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"core_companysettings", "django_migrations", "auth_user"} <= tables:
            raise ValidationError("هذا الملف ليس نسخة احتياطية من Trio")
        name = (con.execute("SELECT name FROM core_companysettings LIMIT 1").fetchone() or [""])[0]
        facts = dict(shop=name, users=con.execute("SELECT COUNT(*) FROM auth_user").fetchone()[0])
        if "pos_order" in tables:
            facts["orders"], facts["last_sale"] = con.execute("SELECT COUNT(*), MAX(created_at_device) FROM pos_order").fetchone()
        if "ledger_journalentry" in tables:
            facts["last_entry"] = con.execute("SELECT MAX(date) FROM ledger_journalentry").fetchone()[0]
        con.close()
    except sqlite3.DatabaseError:
        Path(path).unlink(missing_ok=True)
        raise ValidationError("هذا الملف ليس قاعدة بيانات سليمة")
    except ValidationError:
        Path(path).unlink(missing_ok=True)
        raise
    return path, facts


def restore(path, user=None):
    """Puts the checked database in place of today's. Returns the safety copy made just before."""
    from django.core.management import call_command
    from django.db import connection, connections

    from core.management.commands.backup_now import run_backup
    from core.models import CompanySettings

    p = Path(path).resolve()
    if p.parent != (Path(settings.BASE_DIR) / "data" / "restore").resolve() or not p.exists():
        raise ValidationError("افحص النسخة الأول")
    if connection.vendor != "sqlite":
        raise ValidationError("الاسترجاع من هنا لقاعدة SQLite فقط")
    s = CompanySettings.get()
    keep = {f: getattr(s, f) for f in KEEP}
    if not keep["backup_key"]:
        keep["backup_key"] = key(s)
    safety, _ = run_backup(force=True)
    live = str(settings.DATABASES["default"]["NAME"])
    connections.close_all()
    src, dst = sqlite3.connect(str(p)), sqlite3.connect(live, timeout=30)
    try:
        with dst:
            src.backup(dst)
    finally:
        src.close()
        dst.close()
    p.unlink(missing_ok=True)
    connections.close_all()
    call_command("migrate", interactive=False, verbosity=0)  # an older backup gets this version's tables
    s = CompanySettings.get()
    if s.clock_day and keep["clock_day"] and s.clock_day > keep["clock_day"]:
        keep["clock_day"] = s.clock_day
    if s.trial_started and (not keep["trial_started"] or s.trial_started < keep["trial_started"]):
        keep["trial_started"] = s.trial_started  # a backup of a copy whose trial ended never starts a new trial
    for f, v in keep.items():
        setattr(s, f, v)
    s.save()
    from core import audit

    try:
        audit.log("restore", s, summary=f"استرجاع نسخة احتياطية (نسخة أمان قبلها: {safety.name if safety else '—'})")
    except Exception:  # noqa: BLE001 — the audit table must never stop a restore
        pass
    return safety
