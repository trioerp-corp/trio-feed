"""A client's own custom changes (1.26): one program for everyone, plus a small «custom pack» sent to that client only.

The pack comes like an update — a line TRIOCP.<payload>.<sig> in the command channel (with «u», the address of its zip) or an
e-mail «ACC-CUSTOM» with the zip attached — payload {to: [machine codes], r: revision, sha, n, u, notes, t}, signed with Trio's
key (or its certified update key: then it waits HOLD like a release). It is taken only when «to» names this machine and «r» is
newer than the last pack this copy handled. It lives in data/custom/ (data/ is never touched by program updates):

  templates/   override any program template (searched first)
  static/      files served before the program's own (/static/<path>)
  custom.json  free settings / flags: custom.get("key", default)
  extra/       optional Python package: extra/urls.py (urlpatterns, mounted under /custom/) and extra/apps.py (ready()).
               Loaded inside try/except: a broken pack is written to data/errors.log and the program runs without it.

Install = extract into data/custom.new, then swap (the previous pack is kept as data/custom.prev). «إلغاء التعديلات الخاصة»
moves the pack to data/custom.prev; «استعادة» brings it back.
"""
import datetime as dt
import hashlib
import importlib
import importlib.util
import io
import json
import os
import re
import shutil
import sys
import traceback
import zipfile
from pathlib import Path, PurePosixPath

from django.conf import settings

CP_RE = re.compile(r"TRIOCP(?:\.[A-Za-z0-9_-]+){2}(?:\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)?")
ALLOWED = ("templates", "static", "custom.json", "extra")
MAX_FILES, MAX_BYTES = 3000, 60 << 20
EXTRA_PKG = "trio_custom_extra"


def _data():
    p = Path(settings.BASE_DIR) / "data"
    p.mkdir(parents=True, exist_ok=True)
    return p


def folder():
    return _data() / "custom"


def prev_folder():
    return _data() / "custom.prev"


def _state_file():
    return _data() / "custom_state.json"


def state():
    try:
        return json.loads(_state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(**kw):
    st = state()
    st.update(kw)
    _state_file().write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    changed()
    return st


def _read_info(d):
    try:
        return json.loads((d / ".pack.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def info():
    """The installed pack: {r, notes, files, t, at} — {} when there is none."""
    d = folder()
    return _read_info(d) if d.is_dir() else {}


def prev_info():
    d = prev_folder()
    return _read_info(d) if d.is_dir() else {}


def installed_r():
    try:
        return int(info().get("r") or 0)
    except (TypeError, ValueError):
        return 0


def handled_r():
    """The newest revision this copy took (installed or cancelled since): an older or the same pack never comes back by itself."""
    try:
        return max(int(state().get("r") or 0), installed_r())
    except (TypeError, ValueError):
        return installed_r()


# ---------------------------------------------------------------- settings of the pack
_cache = {"key": None, "data": {}}


def get(key, default=None):
    """A value from the pack's custom.json (default when there is no pack, no file or no such key)."""
    p = folder() / "custom.json"
    try:
        st = p.stat()
    except OSError:
        return default
    k = (str(p), st.st_mtime_ns, st.st_size)
    if _cache["key"] != k:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        _cache.update(key=k, data=data if isinstance(data, dict) else {})
    return _cache["data"].get(key, default)


# ---------------------------------------------------------------- errors go to data/errors.log, never stop the program
def log_error(what):
    try:
        with open(_data() / "errors.log", "a", encoding="utf-8") as f:
            f.write(f"{dt.datetime.now().isoformat(timespec='seconds')} ERROR trio.custom: {what}\n{traceback.format_exc()}\n")
    except OSError:
        pass


# ---------------------------------------------------------------- receiving a pack
def for_me(p):
    """A signed pack payload meant for this machine and newer than the last one handled."""
    from . import license as LIC

    to = p.get("to")
    if not isinstance(to, list) or LIC.machine_code() not in to:  # a pack is always for named machines, never «*»
        return False
    try:
        r = int(p.get("r"))
    except (TypeError, ValueError):
        return False
    return r > handled_r()


def _names(z):
    """The pack's file names, checked: only the allowed top entries, nothing outside the pack folder."""
    out, total = [], 0
    for i in z.infolist():
        n = i.filename.replace("\\", "/")
        if n.endswith("/"):
            continue
        p = PurePosixPath(n)
        parts = p.parts
        if not parts or n.startswith("/") or ".." in parts or any(":" in x for x in parts) or parts[0] not in ALLOWED:
            raise ValueError(f"ملف غير مسموح به في حزمة التعديلات: {n}")
        if (parts[0] == "custom.json") != (len(parts) == 1):
            raise ValueError(f"ملف غير مسموح به في حزمة التعديلات: {n}")
        total += i.file_size
        out.append((i, parts))
    if len(out) > MAX_FILES or total > MAX_BYTES:
        raise ValueError("حزمة التعديلات أكبر من المسموح")
    return out


def install(p, data):
    """Verify and put the pack in place (the old one becomes data/custom.prev). Returns a message; ValueError on a bad pack."""
    if hashlib.sha256(data).hexdigest() != p.get("sha"):
        raise ValueError("وصل ملف التعديلات ناقصًا أو متغيّرًا")
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError("ملف التعديلات ليس ملف zip سليمًا")
    names = _names(z)
    tmp = _data() / "custom.new"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    base = tmp.resolve()
    try:
        for i, parts in names:
            out = tmp.joinpath(*parts)
            out.resolve().relative_to(base)  # never outside data/custom.new
            out.parent.mkdir(parents=True, exist_ok=True)
            with z.open(i) as src, open(out, "wb") as dst:
                shutil.copyfileobj(src, dst)
        if (tmp / "custom.json").exists():
            json.loads((tmp / "custom.json").read_text(encoding="utf-8"))  # a broken custom.json refuses the pack
        r = int(p["r"])
        (tmp / ".pack.json").write_text(json.dumps(dict(r=r, notes=str(p.get("notes") or ""), files=len(names), sha=p["sha"], t=p.get("t", ""),
                                                        at=dt.datetime.now().isoformat(timespec="seconds")), ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        shutil.rmtree(tmp, ignore_errors=True)
        raise ValueError("ملف التعديلات غير صالح") from e
    _swap_in(tmp)
    _save_state(r=r, on=True)
    return f"تم تثبيت التعديلات الخاصة (الإصدار {r}) ✔"


def _swap_in(new):
    cur, prev = folder(), prev_folder()
    if cur.exists():
        shutil.rmtree(prev, ignore_errors=True)
        cur.rename(prev)
    new.rename(cur)


def cancel():
    """«إلغاء التعديلات الخاصة»: the program runs as it is for everyone; the pack is kept in data/custom.prev."""
    cur = folder()
    if not cur.exists():
        return "لا توجد تعديلات خاصة مثبّتة"
    r = installed_r()
    shutil.rmtree(prev_folder(), ignore_errors=True)
    cur.rename(prev_folder())
    _save_state(r=max(r, int(state().get("r") or 0)), on=False)
    return "أُلغيت التعديلات الخاصة — يعمل البرنامج الآن بنسخته العامة"


def restore():
    """Bring back the pack kept in data/custom.prev (after a cancel, or the one before the newest)."""
    prev = prev_folder()
    if not prev.exists():
        return "لا توجد تعديلات سابقة للاستعادة"
    cur = folder()
    tmp = _data() / "custom.swap"
    shutil.rmtree(tmp, ignore_errors=True)
    if cur.exists():
        cur.rename(tmp)
    prev.rename(cur)
    if tmp.exists():
        tmp.rename(prev)
    _save_state(on=True)
    return f"استُعيدت التعديلات الخاصة (الإصدار {installed_r()}) ✔"


def from_codes(codes, fetch):
    """The command channel: the newest pack meant for this machine → downloaded and installed. Returns a message or ""."""
    from . import updates

    best = None
    for c in codes:
        if not c.startswith("TRIOCP."):
            continue
        try:
            p = updates.verify(c, "TRIOCP")
        except ValueError:
            continue
        if updates.held(p) or not p.get("u") or not for_me(p):
            continue
        if best is None or int(p["r"]) > int(best["r"]):
            best = p
    if not best:
        return ""
    try:
        data = fetch(best["u"])
        if isinstance(data, str):
            data = data.encode()
        return install(best, data)
    except ValueError as e:
        return f"تعذّر تثبيت التعديلات الخاصة: {e}"
    except Exception as e:  # noqa: BLE001
        return f"تعذّر تنزيل التعديلات الخاصة ({type(e).__name__})"


def check_mail(snd, imap=None):
    """The program mailbox: «ACC-CUSTOM» with TRIOCP and the zip attached."""
    from . import updates

    best = None
    for text, files in updates._messages(snd, "ACC-CUSTOM", imap=imap):
        for c in CP_RE.findall(text):
            try:
                p = updates.verify(c, "TRIOCP")
            except ValueError:
                continue
            if updates.held(p) or not for_me(p):
                continue
            z = next((d for fn, d in files if fn.lower().endswith(".zip")), None)
            if z is not None and (best is None or int(p["r"]) > int(best[0]["r"])):
                best = (p, z)
    if not best:
        return "لا توجد تعديلات خاصة جديدة"
    try:
        return install(*best)
    except ValueError as e:
        return f"تعذّر تثبيت التعديلات الخاصة: {e}"


# ---------------------------------------------------------------- using the pack in the running program
_seen = {"stamp": None}


def changed():
    """The pack changed (in this process): templates and settings are read again."""
    _cache.update(key=None, data={})
    _seen["stamp"] = _stamp()
    reset_templates()


def _stamp():
    try:
        st = _state_file().stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def reset_templates():
    try:
        from django.template import engines

        for e in engines.all():
            for ld in getattr(getattr(e, "engine", None), "template_loaders", []):
                if hasattr(ld, "reset"):
                    ld.reset()
    except Exception:  # noqa: BLE001
        pass


try:
    from django.template.loaders.filesystem import Loader as _FS
except Exception:  # noqa: BLE001
    _FS = object


class TemplateLoader(_FS):
    """First in TEMPLATES: the pack's templates/ (when a pack is installed) override the program's."""

    def get_dirs(self):
        try:
            d = folder() / "templates"
            return [d] if d.is_dir() else []
        except Exception:  # noqa: BLE001
            return []


class Middleware:
    """Before WhiteNoise: serves the pack's static files, and notices a pack installed by another process (the hourly check)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            s = _stamp()
            if s != _seen["stamp"]:
                changed()
            pre = "/" + settings.STATIC_URL.lstrip("/")
            if s and request.path.startswith(pre):
                r = self._static(request.path[len(pre):])
                if r is not None:
                    return r
        except Exception:  # noqa: BLE001
            log_error("static / reload")
        return self.get_response(request)

    @staticmethod
    def _static(rel):
        import mimetypes

        from django.http import FileResponse

        parts = PurePosixPath(rel).parts
        if not parts or ".." in parts or any(":" in x or x.startswith(".") for x in parts):
            return None
        base = (folder() / "static").resolve()
        p = base.joinpath(*parts)
        if not p.is_file():
            return None
        p.resolve().relative_to(base)
        ct, _ = mimetypes.guess_type(str(p))
        r = FileResponse(open(p, "rb"), content_type=ct or "application/octet-stream")
        r["Cache-Control"] = "no-cache"
        return r


def _extra_pkg():
    d = folder() / "extra"
    if not (d / "__init__.py").is_file():
        return None
    if EXTRA_PKG in sys.modules:
        return sys.modules[EXTRA_PKG]
    spec = importlib.util.spec_from_file_location(EXTRA_PKG, d / "__init__.py", submodule_search_locations=[str(d)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[EXTRA_PKG] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        _forget_extra()
        raise
    return mod


def _forget_extra():
    for k in [k for k in sys.modules if k == EXTRA_PKG or k.startswith(EXTRA_PKG + ".")]:
        sys.modules.pop(k, None)


def extra_urls():
    """urlpatterns of extra/urls.py, under /custom/ — [] when there is none or it is broken (written to data/errors.log)."""
    try:
        if not _extra_pkg() or not (folder() / "extra" / "urls.py").is_file():
            return []
        from django.urls import URLPattern, URLResolver, include, path

        mod = importlib.import_module(EXTRA_PKG + ".urls")
        pats = [x for x in getattr(mod, "urlpatterns", []) if isinstance(x, (URLPattern, URLResolver))]
        for x in pats:
            x.check()
        return [path("custom/", include((pats, "custom")))] if pats else []
    except BaseException as e:  # noqa: BLE001 — a broken pack never stops the program (even sys.exit() in it)
        if isinstance(e, KeyboardInterrupt):
            raise
        _forget_extra()
        log_error("extra/urls.py of the custom pack failed — the program runs without it")
        return []


def run_ready():
    """extra/apps.py ready() at start — errors are logged, never raised."""
    try:
        if not _extra_pkg() or not (folder() / "extra" / "apps.py").is_file():
            return False
        mod = importlib.import_module(EXTRA_PKG + ".apps")
        fn = getattr(mod, "ready", None)
        if callable(fn):
            fn()
        return True
    except BaseException as e:  # noqa: BLE001
        if isinstance(e, KeyboardInterrupt):
            raise
        _forget_extra()
        log_error("extra/apps.py of the custom pack failed — the program runs without it")
        return False


def files_count():
    d = folder()
    if not d.is_dir():
        return 0
    return sum(1 for _, _, fs in os.walk(d) for f in fs if f != ".pack.json")
