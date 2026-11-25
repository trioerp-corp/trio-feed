"""Install the staged update (run by the launcher while the server is stopped).

  acc_apply_update            backup the database, keep the current program files aside, put the new ones in place
  acc_apply_update --rollback put the old files and the database back (when migrate failed)
  acc_apply_update --done     the new version is running: remember it for the «تم التحديث» notice and clean up

A light update (1.26, ready.json delta=true) holds only the changed / new files and «_delta.json» {from, to, deleted}: it is
installed only over exactly the version «from»; each file it replaces or deletes is kept aside first, so --rollback puts them back.
data/ (the shop's data and its custom pack data/custom) is never touched by either kind.
"""
import hashlib
import json
import shutil
import zipfile
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

KEEP = {"data", "backups", "python", "client", ".venv", "staticfiles", ".acc-flash"}
ALLOWED_TOP = {"config", "core", "ledger", "treasury", "inventory", "pos", "gaming", "ui", "reports", "accruals", "templates", "static", "docs",
               "shop_kit", "manage.py", "requirements.txt", "requirements-server.txt", "Trio.exe", "Trio Cashier.exe", "backup_shop.bat"}


class Command(BaseCommand):
    help = "تثبيت التحديث الجاهز (يشغّله المُشغّل)"

    def add_arguments(self, p):
        p.add_argument("--rollback", action="store_true")
        p.add_argument("--done", action="store_true")

    def handle(self, rollback=False, done=False, **o):
        from core import updates as U
        from core.version import VERSION

        root = Path(settings.BASE_DIR)
        f = U.folder()
        if rollback:
            return self.rollback(root, f)
        if done:
            info = json.loads((f / "applying.json").read_text(encoding="utf-8")) if (f / "applying.json").exists() else {}
            rd = json.loads((f / "ready.json").read_text(encoding="utf-8")) if (f / "ready.json").exists() else {}
            (f / "applied.json").write_text(json.dumps(dict(v=info.get("to") or VERSION, notes=rd.get("notes", ""), old=info.get("from", ""),
                                                            at=timezone.now().isoformat()), ensure_ascii=False), encoding="utf-8")
            for n in ("ready.json", "applying.json", "now.flag", "failed.txt", "seen.txt"):
                (f / n).unlink(missing_ok=True)
            if rd.get("file"):
                (f / rd["file"]).unlink(missing_ok=True)
            for old in sorted((f / "rollback").glob("*"))[:-1] if (f / "rollback").exists() else []:
                shutil.rmtree(old, ignore_errors=True)
            self.stdout.write(f"تم التحديث إلى {info.get('to')} ✔")
            return
        if not (f / "ready.json").exists():
            self.stdout.write("لا يوجد تحديث جاهز")
            return
        rd = json.loads((f / "ready.json").read_text(encoding="utf-8"))
        zp = f / Path(str(rd["file"])).name  # only a file inside the update folder
        data = zp.read_bytes()
        if hashlib.sha256(data).hexdigest() != rd["sha"]:
            (f / "ready.json").unlink()
            raise CommandError("ملف التحديث متغيّر — تم حذفه")
        if rd.get("delta"):
            if str(rd.get("from")) != VERSION:  # made for another version: the next check downloads the full update
                (f / "ready.json").unlink()
                zp.unlink(missing_ok=True)
                raise CommandError("التحديث الخفيف لا يناسب هذا الإصدار — سيُنزَّل التحديث الكامل")
            return self.apply_delta(root, f, rd, zp)
        with zipfile.ZipFile(zp) as z:
            base = root.resolve()
            listing = set(z.namelist())

            def safe(n):
                """A program file inside the program folder: no «..», no drive letter, no absolute path, nothing outside."""
                p = Path(n.replace("\\", "/"))
                parts = p.parts
                if not parts or p.is_absolute() or p.drive or ".." in parts or any(":" in x for x in parts):
                    return False
                if parts[0] in KEEP or parts[0].startswith("_"):
                    return False
                try:
                    (base / p).resolve().relative_to(base)
                except ValueError:
                    return False
                # a new Django app of a later version comes as its own top folder with an apps.py
                new_app = len(parts) > 1 and f"{parts[0]}/apps.py" in listing and parts[0].isidentifier()
                return parts[0] in ALLOWED_TOP or new_app or (len(parts) == 1 and n.endswith(".txt"))  # notes (.txt) only at the top

            names = [n for n in z.namelist() if not n.endswith("/") and safe(n)]
            tops = {Path(n.replace("\\", "/")).parts[0] for n in names}
            from core.management.commands.backup_now import run_backup

            bk, _ = run_backup(force=True)
            rb = f / "rollback" / VERSION
            if rb.exists():
                shutil.rmtree(rb)
            rb.mkdir(parents=True)
            for t in sorted(tops):
                cur = root / t
                if cur.exists():
                    shutil.move(str(cur), str(rb / t))  # a running .exe can be moved on Windows, not overwritten
            (f / "applying.json").write_text(json.dumps(dict(**{"from": VERSION}, to=rd["v"], rollback=str(rb), backup=str(bk) if bk else "",
                                                             tops=sorted(tops)), ensure_ascii=False), encoding="utf-8")
            try:
                for n in names:
                    parts = Path(n.replace("\\", "/")).parts
                    if parts and parts[0] in tops:  # names were checked by safe(): never outside the folder
                        out = root.joinpath(*parts)
                        out.parent.mkdir(parents=True, exist_ok=True)
                        with z.open(n) as src, open(out, "wb") as dst:
                            shutil.copyfileobj(src, dst)
            except Exception:
                self.rollback(root, f)
                raise
        self.stdout.write(f"ملفات {rd['v']} في مكانها")

    def apply_delta(self, root, f, rd, zp):
        from core.version import VERSION

        base = root.resolve()
        with zipfile.ZipFile(zp) as z:
            listing = set(z.namelist())
            try:
                meta = json.loads(z.read("_delta.json").decode("utf-8"))
            except (KeyError, ValueError):
                (f / "ready.json").unlink()
                raise CommandError("التحديث الخفيف ناقص — سيُنزَّل التحديث الكامل")
            if str(meta.get("from")) != VERSION or str(meta.get("to")) != str(rd["v"]):
                (f / "ready.json").unlink()
                raise CommandError("التحديث الخفيف لا يناسب هذا الإصدار — سيُنزَّل التحديث الكامل")

            def safe(n):
                p = Path(str(n).replace("\\", "/"))
                parts = p.parts
                if not parts or p.is_absolute() or p.drive or ".." in parts or any(":" in x for x in parts):
                    return None
                if parts[0] in KEEP or parts[0].startswith("_"):
                    return None
                if len(parts) == 1:  # a top file: only the program's own (manage.py, Trio.exe…), never the notes
                    if parts[0] not in ALLOWED_TOP:
                        return None
                elif parts[0] not in ALLOWED_TOP and not (parts[0].isidentifier() and (f"{parts[0]}/apps.py" in listing or (root / parts[0] / "apps.py").exists())):
                    return None
                try:
                    (base / p).resolve().relative_to(base)
                except ValueError:
                    return None
                return "/".join(parts)

            put = sorted({x for x in (safe(n) for n in listing if not n.endswith("/")) if x})
            gone = sorted({x for x in (safe(n) for n in meta.get("deleted") or []) if x} - set(put))
            from core.management.commands.backup_now import run_backup

            bk, _ = run_backup(force=True)
            rb = f / "rollback" / VERSION
            if rb.exists():
                shutil.rmtree(rb)
            (rb / "files").mkdir(parents=True)
            kept, new = [], []
            for rel in put + gone:  # what is there now goes aside first (a running .exe can be moved, not overwritten)
                cur = root.joinpath(*rel.split("/"))
                if cur.is_file():
                    dst = rb.joinpath("files", *rel.split("/"))
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(cur), str(dst))
                    kept.append(rel)
                elif rel in put:
                    new.append(rel)
            (f / "applying.json").write_text(json.dumps(dict(**{"from": VERSION}, to=rd["v"], rollback=str(rb), backup=str(bk) if bk else "",
                                                             tops=[], delta=True, kept=kept, new=new), ensure_ascii=False), encoding="utf-8")
            try:
                for rel in put:
                    out = root.joinpath(*rel.split("/"))
                    out.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(rel) as src, open(out, "wb") as dst:
                        shutil.copyfileobj(src, dst)
            except Exception:
                self.rollback(root, f)
                raise
        self.stdout.write(f"ملفات {rd['v']} في مكانها (تحديث خفيف: {len(put)} ملف، حُذف {len(gone)})")

    def rollback(self, root, f):
        info = json.loads((f / "applying.json").read_text(encoding="utf-8")) if (f / "applying.json").exists() else None
        if not info:
            self.stdout.write("لا يوجد ما يمكن التراجع عنه")
            return
        rb = Path(info["rollback"])
        for rel in info.get("new", []) + info.get("kept", []) if info.get("delta") else []:  # a light update: file by file
            cur = root.joinpath(*rel.split("/"))
            if cur.is_file():
                try:
                    cur.unlink()
                except OSError:
                    cur.rename(cur.with_suffix(cur.suffix + ".failed"))
            old = rb.joinpath("files", *rel.split("/"))
            if old.is_file():
                cur.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(old), str(cur))
        for t in info["tops"]:
            new = root / t
            if new.exists():
                if new.is_dir():
                    shutil.rmtree(new, ignore_errors=True)
                else:
                    try:
                        new.unlink()
                    except OSError:
                        new.rename(new.with_suffix(".failed"))
            if (rb / t).exists():
                shutil.move(str(rb / t), str(new))
        if info.get("backup"):
            import zipfile as Z

            db = Path(settings.DATABASES["default"]["NAME"])
            with Z.ZipFile(info["backup"]) as z:
                n = z.namelist()[0]
                for ext in ("-wal", "-shm"):
                    Path(str(db) + ext).unlink(missing_ok=True)
                with z.open(n) as src, open(db, "wb") as dst:
                    shutil.copyfileobj(src, dst)
        (f / "failed.txt").write_text(f"{info['to']}", encoding="utf-8")
        (f / "applying.json").unlink(missing_ok=True)
        (f / "now.flag").unlink(missing_ok=True)
        self.stdout.write("تم التراجع عن التحديث — عاد البرنامج كما كان")
