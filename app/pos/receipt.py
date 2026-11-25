"""One receipt look for the whole program (1.26): till receipt, table bill, kitchen ticket header, clinic and hall receipts.

The till builds its receipts in its script (templates/pos/app.html, data from pos.services.catalog: cur / paper / vat_reg);
the clinic and hall receipts are Django pages that use ctx() + templates/ui/_rc_head.html / _rc_foot.html / _rc_css.html.
Paper: 80 mm (72 mm printable, the default) or 58 mm (48 mm printable) — «الإعدادات» → «بيانات الإيصال»
(CompanySettings.menu_cfg["receipt"]["w"]).
"""
from core.models import CompanySettings

WIDTHS = ("80", "58")


def width(s=None):
    s = s or CompanySettings.get()
    w = str(((s.menu_cfg or {}).get("receipt") or {}).get("w") or "80")
    return w if w in WIDTHS else "80"


def with_width(s, w):
    """menu_cfg with the paper width set (an unknown value keeps the current one)."""
    cfg = dict(s.menu_cfg or {})
    if str(w or "") in WIDTHS:
        cfg["receipt"] = {**(cfg.get("receipt") or {}), "w": str(w)}
    return cfg


def tax_no(s=None):
    """The number printed on receipts — only for a shop registered for VAT."""
    s = s or CompanySettings.get()
    return (s.tax_registration or s.tax_file_no or "").strip() if s.vat_registered else ""


def ctx(branch=None, s=None):
    """What _rc_head / _rc_foot need: shop, branch address / phone, tax number, policy, footer, InstaPay QR, paper width."""
    from pos.models import Branch

    from . import instapay
    from .services import receipt_policy

    s = s or CompanySettings.get()
    br = branch or Branch.objects.order_by("pk").first()
    try:
        insta = instapay.till(s)
    except Exception:  # noqa: BLE001 — a receipt never breaks on the QR
        insta = {}
    return dict(rc_name=s.name, rc_logo=s.logo_svg, rc_address=(getattr(br, "address", "") or s.address or ""),
                rc_phone=(getattr(br, "phone", "") or s.phone or ""), rc_tax=tax_no(s), rc_policy=receipt_policy(s),
                rc_footer=(getattr(br, "receipt_footer", "") or "شكرًا لزيارتكم"), rc_w=width(s),
                rc_qr=insta.get("qr") if insta.get("rc", True) else "", rc_qr_label=insta.get("label", ""))
