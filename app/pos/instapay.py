"""InstaPay (1.25): the shop's InstaPay link / address, its QR on every receipt and the customer screen, and «InstaPay» as a pay kind.

- Settings live in CompanySettings.menu_cfg["insta"] = {"link": "https://ipn.eg/S/…" | "name@instapay", "rc": True/False}
  (rc = print the QR on the receipt; default on).
- Money paid by InstaPay is a bank transfer, never cash: it goes to the «إنستاباي» CashBox account (1201030103 in the shop chart),
  stays out of the drawer's expected cash, and has its own column on the shift report and the day close.
- InstaPay links do not carry an amount, so the QR is the same for every bill; the receipt prints the amount next to it.
"""
import re
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError

HOSTS = ("ipn.eg", "instapay.eg")
IPA = re.compile(r"^[a-z0-9][a-z0-9._-]{1,48}@instapay$")
CODE = "1201030103"
HELP = "انسخ رابط الدفع من تطبيق InstaPay (يبدأ بـ https://ipn.eg) أو اكتب عنوانك مثل name@instapay"


def clean(value):
    """The owner's text → the normalized link / address ('' = none). Raises ValidationError with a clear Arabic message."""
    v = str(value or "").strip().translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    if not v:
        return ""
    if len(v) > 300 or any(c.isspace() for c in v):
        raise ValidationError("رابط InstaPay غير صحيح — " + HELP)
    low = v.lower()
    if "@" in low and "/" not in low:
        low = low.lstrip("@")
        if not IPA.match(low):
            raise ValidationError("عنوان InstaPay غير صحيح — يكون مثل name@instapay (حروف إنجليزية وأرقام)")
        return low
    if not low.startswith(("http://", "https://")):
        v, low = "https://" + v, "https://" + low
    try:
        u = urlsplit(v)
    except ValueError:
        raise ValidationError("رابط InstaPay غير صحيح — " + HELP)
    host = (u.hostname or "").lower()
    if not any(host == h or host.endswith("." + h) for h in HOSTS):
        raise ValidationError("هذا ليس رابط InstaPay — " + HELP)
    if u.username or u.password or u.port or not u.path.strip("/"):
        raise ValidationError("رابط InstaPay ناقص — " + HELP)
    return "https://" + host + u.path + (("?" + u.query) if u.query else "")


def cfg(s=None):
    from core.models import CompanySettings

    s = s or CompanySettings.get()
    c = (s.menu_cfg or {}).get("insta") or {}
    return {"link": str(c.get("link") or ""), "rc": c.get("rc", True) is not False}


def label(link):
    """What the receipt prints under the QR: the address itself, or the user name inside an ipn.eg link."""
    if not link:
        return ""
    if "@" in link and "/" not in link:
        return link
    parts = [p for p in urlsplit(link).path.split("/") if p]
    if len(parts) >= 2 and parts[0].upper() == "S":
        return parts[1] + "@instapay"
    return (urlsplit(link).hostname or "") + "/" + "/".join(parts)[:40]


def qr_svg(data):
    """A black QR (thermal printers) that scales to its box."""
    import io

    import segno

    buf = io.BytesIO()
    segno.make(data, error="m").save(buf, kind="svg", scale=1, border=1, dark="#000", xmldecl=False, svgns=True, omitsize=True)
    return buf.getvalue().decode()


def box(create=False):
    """The «إنستاباي» CashBox (made with its account when the chart has none)."""
    from django.db import transaction

    from ledger.models import Account
    from treasury.models import CashBox

    b = CashBox.objects.filter(account__code=CODE).first() or CashBox.objects.filter(name__in=["إنستاباي", "InstaPay"]).first()
    if b or not create:
        return b
    with transaction.atomic():
        acc = Account.objects.filter(code=CODE).first()
        if acc is None:
            parent = Account.objects.get(code="12010301")
            acc = Account.objects.create(code=CODE, name="إنستاباي", parent=parent, level=parent.level + 1, is_postable=True,
                                         account_type="asset", normal_balance="debit", report="balance_sheet", cash_flow_category="cash")
        return CashBox.objects.create(name="إنستاباي", kind="wallet", account=acc, currency="EGP", order=2)


def account():
    """Where an InstaPay payment at the till goes."""
    return box(create=True).account


def on(s=None):
    """The till offers «InstaPay» when the shop entered its link or turned the InstaPay wallet on."""
    b = box()
    return bool(cfg(s)["link"]) or bool(b and b.active)


def save(link, rc=True, s=None):
    """Saves the link (validated) and turns the InstaPay wallet on, so it shows at the till and in the treasury."""
    from core.models import CompanySettings

    s = s or CompanySettings.get()
    link = clean(link)
    mc = dict(s.menu_cfg or {})
    mc["insta"] = {"link": link, "rc": bool(rc)}
    s.menu_cfg = mc
    s.save(update_fields=["menu_cfg"])
    if link:
        b = box(create=True)
        if not b.active:
            b.active = True
            b.save(update_fields=["active"])
    return link


def till(s=None):
    """For the till's catalog: {"on", "link", "label", "qr", "rc"}."""
    c = cfg(s)
    out = {"on": on(s), "link": c["link"], "rc": c["rc"]}
    if c["link"]:
        out.update(label=label(c["link"]), qr=qr_svg(c["link"]))
    return out
