"""The shop's currency (1.26): ONE place for every money display — till, receipts, invoices, reports, phone page, PDFs.

The shop picks it in the first setup (default from its country) or in «الإعدادات»; it is CompanySettings.base_currency.
Only the DISPLAY changes: the symbol in the viewer's language (ar «ر.س» / en·fr «SAR») and the decimals (KWD 3, IQD 0).
Accounting stays numeric — no conversion, the ledger's internal «EGP» code just means «the shop's own currency».

    from core import currency as C
    C.fmt(12.5)          -> «12.50 ج.م» (ar) / «12.50 EGP» (en)
    C.num(12.5)          -> «12.50»   (the shop's decimals, no symbol)
    C.sym()              -> «ج.م»
    {% load gg %}{{ x|cur }}  {{ x|money }}  {{ CUR }} (context: the symbol)
"""
import contextvars
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

# code: (Arabic symbol, Latin symbol, decimals, Arabic name, English name)
CURRENCIES = {
    "EGP": ("ج.م", "EGP", 2, "جنيه مصري", "Egyptian pound"),
    "SAR": ("ر.س", "SAR", 2, "ريال سعودي", "Saudi riyal"),
    "AED": ("د.إ", "AED", 2, "درهم إماراتي", "UAE dirham"),
    "QAR": ("ر.ق", "QAR", 2, "ريال قطري", "Qatari riyal"),
    "KWD": ("د.ك", "KWD", 3, "دينار كويتي", "Kuwaiti dinar"),
    "BHD": ("د.ب", "BHD", 3, "دينار بحريني", "Bahraini dinar"),
    "OMR": ("ر.ع", "OMR", 3, "ريال عماني", "Omani rial"),
    "JOD": ("د.أ", "JOD", 3, "دينار أردني", "Jordanian dinar"),
    "IQD": ("د.ع", "IQD", 0, "دينار عراقي", "Iraqi dinar"),
    "MAD": ("د.م", "MAD", 2, "درهم مغربي", "Moroccan dirham"),
    "DZD": ("د.ج", "DZD", 2, "دينار جزائري", "Algerian dinar"),
    "TND": ("د.ت", "TND", 3, "دينار تونسي", "Tunisian dinar"),
    "LYD": ("د.ل", "LYD", 3, "دينار ليبي", "Libyan dinar"),
    "USD": ("$", "USD", 2, "دولار أمريكي", "US dollar"),
    "EUR": ("€", "EUR", 2, "يورو", "Euro"),
}
DEFAULT = "EGP"
BY_COUNTRY = {"EG": "EGP", "SA": "SAR", "AE": "AED", "QA": "QAR", "KW": "KWD", "BH": "BHD", "OM": "OMR", "JO": "JOD", "IQ": "IQD",
              "MA": "MAD", "DZ": "DZD", "TN": "TND", "LY": "LYD"}

_lang = contextvars.ContextVar("trio_cur_lang", default="ar")


def set_lang(lang):
    """Called by core.i18n_mw for every request: the symbol follows the viewer's language."""
    _lang.set(lang if lang in ("ar", "en", "fr") else "ar")


def lang():
    return _lang.get()


def code(s=None):
    """The shop's currency code (an unknown / empty value → EGP)."""
    if s is None:
        try:
            from .models import CompanySettings

            s = CompanySettings.get()
        except Exception:  # noqa: BLE001 — before the first migrate
            return DEFAULT
    c = (getattr(s, "base_currency", "") or "").upper()
    return c if c in CURRENCIES else DEFAULT


def for_country(country):
    """EG → EGP, SA → SAR …; a country we do not list → USD."""
    c = (country or "").upper()
    return BY_COUNTRY.get(c, "USD") if c else DEFAULT


def default_code(phone="", request=None):
    """The first-setup default: the site's download country (?cc= / cookie trio_cc / data/country.txt), else the phone's
    international prefix, else Egypt."""
    cc = ""
    if request is not None:
        cc = (request.GET.get("cc") or request.POST.get("cc") or request.COOKIES.get("trio_cc") or "").strip()[:2]
    if not cc:
        try:
            from pathlib import Path

            from django.conf import settings

            f = Path(settings.BASE_DIR) / "data" / "country.txt"
            cc = f.read_text(encoding="utf-8").strip()[:2] if f.exists() else ""
        except Exception:  # noqa: BLE001
            cc = ""
    if not cc and phone:
        from .modules import country_of

        cc = country_of(phone)
    return for_country(cc) if cc else DEFAULT


def clean(value, fallback=DEFAULT):
    v = (value or "").strip().upper()
    return v if v in CURRENCIES else fallback


def places(c=None):
    return CURRENCIES[c or code()][2]


def sym(c=None, lng=None):
    row = CURRENCIES[c or code()]
    return row[0] if (lng or lang()) == "ar" else row[1]


def name(c=None, lng=None):
    row = CURRENCIES[c or code()]
    return row[3] if (lng or lang()) == "ar" else row[4]


def choices(lng=None):
    """[(code, «ريال سعودي (ر.س)»)] for the setup / settings select."""
    ar = (lng or lang()) == "ar"
    return [(k, f"{v[3] if ar else v[4]} ({v[0] if ar else v[1]})") for k, v in CURRENCIES.items()]


def _dec(v):
    try:
        return Decimal(str(v if v not in (None, "") else 0).replace(",", ""))
    except (InvalidOperation, ValueError, TypeError):
        return None


def num(v, c=None, group=True):
    """The amount with the currency's decimals: 1,250.50 / 1,250.500 (KWD) / 1,251 (IQD)."""
    d = _dec(v)
    if d is None:
        return str(v)
    p = places(c)
    d = d.quantize(Decimal(1).scaleb(-p), rounding=ROUND_HALF_UP)
    return f"{d:,.{p}f}" if group else f"{d:.{p}f}"


def fmt(v, c=None, lng=None):
    """«1,250.50 ر.س» (ar) / «1,250.50 SAR» (en, fr) — the one money text of the program."""
    c = c or code()
    return f"{num(v, c)} {sym(c, lng)}"


def till(s=None):
    """What the till's script needs: code, symbol (in the till's language, per page) and decimals."""
    c = code(s)
    return {"c": c, "s": sym(c), "ar": CURRENCIES[c][0], "en": CURRENCIES[c][1], "d": places(c)}
