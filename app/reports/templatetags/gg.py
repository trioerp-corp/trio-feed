from decimal import Decimal, ROUND_HALF_UP

from django import template
from django.utils.html import format_html

register = template.Library()


@register.filter
def money(v, places=None):
    """The amount with the shop's currency decimals (1.26: KWD 3, IQD 0 — core/currency.py); negatives in brackets."""
    if v is None or v == "":
        return ""
    if places is None:
        from core.currency import places as _places

        places = _places()
    v = Decimal(v).quantize(Decimal(1).scaleb(-int(places)), rounding=ROUND_HALF_UP)
    s = f"{abs(v):,.{int(places)}f}"
    if v < 0:
        return format_html('<span class="neg">({})</span>', s)
    return s


@register.filter
def cur(v):
    """«1,250.50 ج.م» / «1,250.50 EGP» — the shop's currency in the viewer's language (core/currency.fmt)."""
    if v is None or v == "":
        return ""
    from core.currency import fmt

    return fmt(v)


@register.simple_tag
def cur_sym():
    from core.currency import sym

    return sym()


@register.filter
def neg(v):
    return -v if v is not None else v


@register.filter
def sub(a, b):
    try:
        return Decimal(a or 0) - Decimal(b or 0)
    except Exception:
        return ""


@register.filter
def get(d, k):
    try:
        return d.get(k)
    except AttributeError:
        return None


@register.filter
def hm(v):
    """Minutes as the hall says them: «10 ساعات»، «ساعة و30 دقيقة»، «45 دقيقة»."""
    try:
        m = int(v or 0)
    except (TypeError, ValueError):
        return v
    h, r = divmod(abs(m), 60)
    hs = "" if not h else "ساعة" if h == 1 else "ساعتان" if h == 2 else f"{h} ساعات" if h <= 10 else f"{h} ساعة"
    out = " و".join(x for x in (hs, f"{r} دقيقة" if r else "") if x) or "0 دقيقة"
    return ("-" if m < 0 else "") + out
