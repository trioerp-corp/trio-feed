"""Trio in English / French without touching the templates (1.27).

The program stays written in Arabic. When the viewer's language is en/fr, this middleware translates the finished
HTML / JSON response: every Arabic segment (core/i18n_seg.SEG — the same tokenizer tools/i18n/extract.py used to
build locale/strings.json) is looked up in one dict. <html lang/dir> become lang=en|fr dir=ltr and static/ui/ltr.css
mirrors the few RTL-only rules. Arabic responses are returned untouched (no overhead).

Kept Arabic on purpose: value="…" attributes (what forms send back) and <textarea> contents — the shop's own data.
Segments with no translation are appended once to data/i18n_missing.txt (python tools/i18n/extract.py then
translate, or add them to locale/strings.json by hand).

Language: the user's choice (data/i18n_prefs.json, set by /lang/?l=en) → the trio_lang cookie (login page) →
the shop default (same file, «shop») → ar.
"""
import hashlib
import json
import os
import re
import threading
from pathlib import Path

from django.conf import settings
from django.http import HttpResponseRedirect
from django.utils.http import url_has_allowed_host_and_scheme

from .i18n_seg import SEG

LANGS = ("ar", "en", "fr")
ROOT = Path(settings.BASE_DIR)
STRINGS = ROOT / "core" / "locale" / "strings.json"
PREFS = Path(os.environ.get("GG_I18N_PREFS") or ROOT / "data" / "i18n_prefs.json")  # GG_I18N_PREFS: a copy run on another database
MISSING = Path(os.environ.get("GG_I18N_MISSING") or ROOT / "data" / "i18n_missing.txt")
_lock = threading.Lock()
_cache = {"t": None, "m": 0, "p": None, "pm": 0, "miss": None}
PUNCT = str.maketrans({"؟": "?", "،": ",", "؛": ";", "٪": "%", "«": "“", "»": "”"})
BRAND = {"تريو": "Trio", "ج": "EGP"}  # brand; «ج» is the pound sign after amounts
# Kept exactly as they are: value="…" (what forms send back), URLs / ids / names / data-* attributes, <textarea>,
# and JS string literals the till compares (u==='شريط', case 'نقدي'). A submit/button input is translated whole.
_PROTECT = re.compile(
    r'(<input\b[^>]*?type="(?:submit|button)"[^>]*>)'
    r'|\b(?:value|href|src|action|name|id|for|class|data-[\w-]+)="[^"]*"'
    r"|<textarea\b.*?</textarea>|/\*.*?\*/|(?<![:'\"\w/\\])//[^\n]*"
    r"""|(?:[=!]==?|\bcase)\s*(?:'[^'\n<>]{0,60}'|"[^"\n<>]{0,60}")"""
    r"""|(?:'[^'\n<>]{0,60}'|"[^"\n<>]{0,60}")\s*[=!]==?""",
    re.S | re.I)
# JSON: only messages are translated; everything else (item / customer names, kinds, states the till compares) is data
JSON_KEYS = {"i18n_view", "error", "errors", "msg", "message", "messages", "warn", "warning", "answer", "detail", "hint", "notice", "info"}
_KEEP = {"أإآٱ", "اويه", "العربية"}  # letters the till's search normalises (code, not text)
_HTML = re.compile(r"<html\b[^>]*>", re.I)


def _mtime(p):
    try:
        return p.stat().st_mtime
    except OSError:
        return 0


def table():
    m = _mtime(STRINGS)
    if _cache["t"] is None or m != _cache["m"]:
        with _lock:
            try:
                raw = json.loads(STRINGS.read_text(encoding="utf-8"))
            except Exception:
                raw = {}
            t = {lg: dict(BRAND) for lg in LANGS[1:]}
            for ar, v in raw.items():
                for lg in LANGS[1:]:
                    if v.get(lg):
                        t[lg][ar] = v[lg]
            _cache["t"], _cache["m"] = t, m
    return _cache["t"]


def prefs():
    m = _mtime(PREFS)
    if _cache["p"] is None or m != _cache["pm"]:
        try:
            _cache["p"] = json.loads(PREFS.read_text(encoding="utf-8"))
        except Exception:
            _cache["p"] = {}
        _cache["pm"] = m
    return _cache["p"]


def save_pref(user_id=None, lang="ar", shop=False):
    with _lock:
        p = dict(prefs())
        if shop:
            p["shop"] = lang
        if user_id:
            p.setdefault("users", {})[str(user_id)] = lang
        PREFS.parent.mkdir(exist_ok=True)
        PREFS.write_text(json.dumps(p), encoding="utf-8")
        _cache["p"] = None


def lang_of(request):
    p = prefs()
    u = getattr(request, "user", None)
    if u is not None and u.is_authenticated:
        lg = p.get("users", {}).get(str(u.pk))
        if lg in LANGS:
            return lg
    lg = request.COOKIES.get("trio_lang")
    if lg in LANGS:
        return lg
    lg = p.get("shop")
    return lg if lg in LANGS else "ar"


def _missing(seen):
    if not seen:
        return
    with _lock:
        if _cache["miss"] is None:
            try:
                _cache["miss"] = set(MISSING.read_text(encoding="utf-8").splitlines())
            except OSError:
                _cache["miss"] = set()
        new = [s for s in seen if s not in _cache["miss"]]
        if new and len(_cache["miss"]) < 50000:
            _cache["miss"].update(new)
            try:
                with MISSING.open("a", encoding="utf-8") as f:
                    f.write("".join(s + "\n" for s in new))
            except OSError:
                pass


def _split(a, d):
    """A segment the dictionary does not know as a whole, e.g. «جرد <item>» or «أهلًا <name>»: the longest known first
    words, then the rest the same way; a part nobody knows (the shop's own names) stays as typed. None: nothing known."""
    w = a.split(" ")
    if len(w) < 2 or len(w) > 12:
        return None
    for k in range(len(w) - 1, 0, -1):
        head = d.get(" ".join(w[:k]))
        if head:
            rest = " ".join(w[k:])
            tail = d.get(rest) or _split(rest, d) or rest
            return head + " " + tail
    return None


def translate_text(s, lang, seen=None):
    d = table()[lang]

    def rep(m):
        a = m.group(0)
        t = d.get(a)
        if t is None:
            t = _split(a, d)
            if t is None:
                if seen is not None and len(a) > 1 and a not in _KEEP:
                    seen.add(a)
                return a
        return t
    return SEG.sub(rep, s.replace("ج.م", "EGP")).translate(PUNCT)


_PAGE_CACHE = {}  # (lang, md5 of the Arabic page) -> (translated page, missing segments); the till page repeats


def translate_html(html, lang, seen=None):
    key = (lang, hashlib.md5(html.encode("utf-8", "surrogatepass")).digest(), table() is not None and _cache["m"])
    hit = _PAGE_CACHE.get(key)
    if hit:
        if seen is not None:
            seen.update(hit[1])
        return hit[0]
    miss = set()
    res = _translate_html(html, lang, miss)
    if len(_PAGE_CACHE) > 64:
        _PAGE_CACHE.clear()
    _PAGE_CACHE[key] = (res, miss)
    if seen is not None:
        seen.update(miss)
    return res


def _translate_html(html, lang, seen=None):
    out, pos = [], 0
    for m in _PROTECT.finditer(html):
        out.append(translate_text(html[pos:m.start()], lang, seen))
        out.append(translate_text(m.group(1), lang, seen) if m.group(1) else m.group(0))  # group 1: a button
        pos = m.end()
    out.append(translate_text(html[pos:], lang, seen))
    html = "".join(out)
    html = _HTML.sub(lambda m: re.sub(r'\s(lang|dir)="[^"]*"', "", m.group(0))[:-1] + f' lang="{lang}" dir="ltr">', html, 1)
    html = html.replace('dir="rtl"', 'dir="ltr"').replace("'ar-EG-u-nu-latn'", "'en-GB'" if lang == "en" else "'fr-FR'")
    return html.replace("</head>", f'<link rel="stylesheet" href="{settings.STATIC_URL}ui/ltr.css"></head>', 1)


def _walk(o, lang, seen, on=False):
    if isinstance(o, str):
        return translate_text(o, lang, seen) if on else o
    if isinstance(o, list):
        return [_walk(x, lang, seen, on) for x in o]
    if isinstance(o, dict):
        return {k: _walk(v, lang, seen, on or k in JSON_KEYS) for k, v in o.items()}
    return o


class I18nMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        lang = request.trio_lang = lang_of(request)
        from .currency import set_lang

        set_lang(lang)  # 1.26: money symbols in the viewer's language (core/currency.py)
        if lang == "ar":
            resp = self.get_response(request)
        else:
            from django.utils import translation

            with translation.override(lang):  # Django's own words (form labels, validation errors, dates) in en / fr
                resp = self.get_response(request)
        if lang == "ar" or getattr(resp, "streaming", False) or resp.status_code in (204, 304):
            return resp
        ct = resp.get("Content-Type", "")
        seen = set()
        try:
            if ct.startswith("text/html"):
                resp.content = translate_html(resp.content.decode(resp.charset or "utf-8"), lang, seen).encode("utf-8")
            elif ct.startswith("application/json"):
                raw = resp.content
                if b"\\u06" not in raw and not re.search(rb"[\xd8-\xdb]", raw):
                    return resp  # no Arabic at all (e.g. most till API calls)
                data = json.loads(raw)
                new = _walk(data, lang, seen)
                if new == data:
                    return resp  # only data (names, kinds) — untouched
                resp.content = json.dumps(new, ensure_ascii=False).encode("utf-8")
            else:
                return resp
        except Exception:  # never break a page over a translation
            return resp
        if resp.has_header("Content-Length"):
            resp["Content-Length"] = str(len(resp.content))
        _missing(seen)
        return resp


def set_lang(request):
    """/lang/?l=en[&shop=1][&next=…] — the «ع | EN | FR» switch."""
    lg = request.GET.get("l", "ar")
    lg = lg if lg in LANGS else "ar"
    nxt = request.GET.get("next") or request.META.get("HTTP_REFERER") or "/"
    if not url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        nxt = "/"
    u = request.user
    if u.is_authenticated:
        shop = request.GET.get("shop") == "1" and (u.is_superuser or u.has_perm("core.change_companysettings"))
        save_pref(u.pk, lg, shop=shop)
    resp = HttpResponseRedirect(nxt)
    resp.set_cookie("trio_lang", lg, max_age=3600 * 24 * 365, samesite="Lax")
    return resp
