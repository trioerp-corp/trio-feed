"""1.25 alerts to the shop owner's WhatsApp.

Immediate: a cancelled / edited invoice, a big drawer shortage at a shift close, goods between branches (a request, a loss on
receipt). Daily digest: expiry soon, items at their reorder level, the day's shortages and cancellations, and the comparisons.

How it leaves: Trio's Worker POST /wa/send sends an approved WhatsApp Cloud API template from Trio's WhatsApp Business number
(Worker secrets WA_TOKEN / WA_PHONE_ID — see the Worker page /wa/guide). When WhatsApp is not set up on Trio's server yet, the
shop has no owner number, or WhatsApp refuses, the same text goes through Telegram (Trio's bot) and/or e-mail (core/mobile.notify),
so the owner is never left without the alert. Every alert is switched on/off by the owner in «الإعدادات» ← «تنبيهات صاحب المحل».

Sending never blocks the till or a page: it runs after the transaction commits, in a background thread (SYNC = True in tests).
"""
import datetime as dt
import logging
import threading
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

log = logging.getLogger(__name__)
SYNC = False  # tests: send in the caller's thread

KINDS = [  # key, label, on by default, immediate?
    ("void", "إلغاء أو تعديل فاتورة", True, True),
    ("short", "عجز كبير في درج عند تقفيل وردية", True, True),
    ("transfer", "تحويلات الفروع (طلب جديد / عجز عند الاستلام)", True, True),
    ("expiry", "أصناف قربت صلاحيتها (في الملخص اليومي)", True, False),
    ("low", "أصناف وصلت حد الطلب (في الملخص اليومي)", True, False),
    ("daily", "الملخص اليومي على واتساب", True, False),
]
LABEL = {k: v for k, v, _, _ in KINDS}
SHORT_MIN = Decimal(50)


def cfg(s=None):
    from .models import CompanySettings

    s = s or CompanySettings.get()
    raw = (s.menu_cfg or {}).get("alerts") or {}
    on = {k: bool(raw.get("on", {}).get(k, d)) for k, _, d, _ in KINDS}
    try:
        short_min = Decimal(str(raw.get("short_min", SHORT_MIN)))
    except Exception:  # noqa: BLE001
        short_min = SHORT_MIN
    return dict(on=on, short_min=short_min, last_digest=raw.get("last_digest", ""))


def save_cfg(on, short_min=None, s=None):
    from django.core.exceptions import ValidationError

    from .models import CompanySettings

    s = s or CompanySettings.get()
    c = dict((s.menu_cfg or {}).get("alerts") or {})
    c["on"] = {k: bool(k in on) for k, _, _, _ in KINDS}
    if short_min is not None:
        try:
            v = Decimal(str(short_min).strip().replace(",", ".") or "0")
        except Exception:  # noqa: BLE001
            raise ValidationError("حد العجز رقم بالجنيه")
        if v < 0:
            raise ValidationError("حد العجز لا يكون سالبًا")
        c["short_min"] = str(v)
    s.menu_cfg = {**(s.menu_cfg or {}), "alerts": c}
    s.save(update_fields=["menu_cfg"])
    return cfg(s)


def owner_number(s):
    """The owner's WhatsApp in international form (Egypt by default): 01001234567 → 201001234567."""
    d = "".join(ch for ch in (s.owner_whatsapp or "") if ch.isdigit())
    if d.startswith("00"):
        d = d[2:]
    if d.startswith("0") and len(d) == 11:
        d = "2" + d
    return d if 10 <= len(d) <= 15 else ""


def flat(text, limit=900):
    """A template parameter may not hold new lines / tabs / long runs of spaces (WhatsApp's rule) — lines become « • »."""
    parts = [" ".join(x.split()) for x in str(text or "").splitlines()]
    out = " • ".join(p for p in parts if p)
    return out[:limit]


# ------------------------------------------------------------------ sending
def deliver(kind, text, s=None):
    """Sends now; returns {"wa": ok|reason, "tg"/"mail": … when it fell back}. Never raises."""
    from . import mobile
    from .models import CompanySettings

    s = s or CompanySettings.get()
    head = f"{s.name}: {LABEL.get(kind, 'تنبيه')}"
    out = {}
    to = owner_number(s)
    if to:
        try:
            res = mobile.post("/wa/send", dict(mobile.ident(s), to=to, kind="daily" if kind == "daily" else "alert", shop=s.name[:60],
                                                 title=LABEL.get(kind, "تنبيه")[:60], text=flat(text),
                                                 lic="".join((s.license_code or "").split())), timeout=20)
            out["wa"] = res.get("wa") or res.get("error") or "لم يُرسل"
        except Exception as e:  # noqa: BLE001 — offline / Worker without /wa yet
            out["wa"] = str(e) or "لا يوجد اتصال"
    else:
        out["wa"] = "لا يوجد رقم واتساب لصاحب المحل"
    if out["wa"] != "ok" and kind != "daily":  # the fallback (the daily summary has its own Telegram / e-mail report): Trio's Telegram bot and / or e-mail, whatever the owner linked
        try:
            res = mobile.notify(f"{head}\n{text}", head, telegram=True, email=s.email_to or "")
            out.update({k: v for k, v in res.items() if k in ("tg", "mail")})
        except Exception as e:  # noqa: BLE001
            out["tg"] = str(e)
    log.info("alert %s → %s", kind, out)
    return out


def fire(kind, text, s=None):
    """An immediate alert (if the owner switched it on) — sent after the current transaction commits, in the background."""
    try:
        if not cfg(s)["on"].get(kind):
            return False
    except Exception:  # noqa: BLE001 — never break the caller (a sale, a shift close)
        return False

    def go():
        if SYNC:
            deliver(kind, text)
        else:
            threading.Thread(target=deliver, args=(kind, text), daemon=True, name=f"alert-{kind}").start()

    transaction.on_commit(go)
    return True


# ------------------------------------------------------------------ the hooks
def on_void(order, reason, editing=False):
    try:
        b = order.device.branch.name if order.device_id else ""
        fire("void", f"{'عُدّلت' if editing else 'أُلغيت'} الفاتورة {order.shift_seq or order.uid} بقيمة {order.total:,.2f} ج"
                     f"{' — ' + b if b else ''} — السبب: {str(reason).replace('تعديل:', '').strip()[:120]}")
    except Exception:  # noqa: BLE001
        log.exception("void alert")


def on_shift_close(sh):
    try:
        diff = (sh.counted_cash or 0) - (sh.expected_cash or 0)
        if diff >= 0 or -diff < cfg()["short_min"]:
            return False
        b = sh.device.branch.name if sh.device_id else ""
        return fire("short", f"عجز {-diff:,.2f} ج في درج {sh.cashier} ({sh.device.name}{' — ' + b if b else ''}) عند تقفيل الوردية: "
                             f"المتوقع {sh.expected_cash:,.2f} والمعدود {sh.counted_cash:,.2f}")
    except Exception:  # noqa: BLE001
        log.exception("short alert")
        return False


def on_transfer(t, what):
    try:
        from pos.transfers import branch_of

        src, dst = branch_of(t.from_warehouse), branch_of(t.to_warehouse)
        names = (src.name if src else t.from_warehouse.name, dst.name if dst else t.to_warehouse.name)
        if what == "request":
            items = "، ".join(f"{ln.item.name} {ln.qty_req:g}" for ln in t.lines.select_related("item")[:6])
            text = f"طلب تحويل {t.number} من {names[1]} إلى {names[0]}: {items}"
        else:
            lost = [ln for ln in t.lines.select_related("item") if ln.qty_lost]
            if not lost:
                return False
            v = sum((ln.qty_lost * ln.unit_cost for ln in lost), Decimal(0))
            text = (f"عجز عند استلام التحويل {t.number} ({names[0]} ← {names[1]}): " + "، ".join(f"{ln.item.name} {ln.qty_lost:g}" for ln in lost)
                    + f" — بالتكلفة {v:,.2f} ج على {names[0]}")
        return fire("transfer", text)
    except Exception:  # noqa: BLE001
        log.exception("transfer alert")
        return False


# ------------------------------------------------------------------ the daily digest
def digest_text(day=None):
    from pos.models import Order, Shift
    from ui.shop import _sales, expiring

    from .compare import comparisons

    day = day or timezone.localdate()
    c = cfg()
    orders = list(Order.objects.filter(created_at_device__date=day))
    t = _sales(orders)
    lines = [f"المبيعات: {t['net']:,.2f} ج ({t['n']} فاتورة)"]
    try:
        now = timezone.localtime() if day == timezone.localdate() else timezone.make_aware(dt.datetime.combine(day, dt.time(23, 59)))
        cm = comparisons(now)
        if cm["d"]["prev"]:
            lines.append(f"مقابل {cm['d']['label']}: {cm['d']['prev']:,.2f} ({'+' if (cm['d']['pct'] or 0) >= 0 else ''}{cm['d']['pct']}%)")
        if cm["m"]["prev"]:
            lines.append(f"الشهر حتى اليوم {cm['m']['now']:,.2f} مقابل {cm['m']['prev']:,.2f} ({'+' if (cm['m']['pct'] or 0) >= 0 else ''}{cm['m']['pct']}%)")
    except Exception:  # noqa: BLE001
        pass
    voids = [o for o in orders if o.cancelled]
    if voids:
        lines.append(f"فواتير ملغاة: {len(voids)} بقيمة {sum((o.total for o in voids), Decimal(0)):,.2f}")
    shorts = [sh for sh in Shift.objects.filter(closed_at__date=day, counted_cash__isnull=False, expected_cash__isnull=False)
              if (sh.counted_cash or 0) < (sh.expected_cash or 0)]
    if shorts:
        lines.append("عجز أدراج: " + "، ".join(f"{sh.cashier} {(sh.expected_cash - sh.counted_cash):,.2f}" for sh in shorts[:5]))
    if c["on"]["low"]:
        from pos.branches import branches_on
        from pos.models import Branch
        from ui.shop import _low

        for b in (Branch.objects.filter(active=True).order_by("id") if branches_on() else [None]):  # each branch's own shelf
            low = _low(b)
            if low:
                lines.append(f"نواقص{' ' + b.name if b else ''} ({len(low)}): " + "، ".join(i.name for i in low[:8]) + ("…" if len(low) > 8 else ""))
    if c["on"]["expiry"]:
        exp = expiring(14)
        if exp:
            lines.append(f"صلاحية خلال أسبوعين ({len(exp)}): " + "، ".join(f"{r['item'].name} ({r['days']} يوم)" for r in exp[:6]))
    return "\n".join(lines)


def digest(day=None, force=False):
    """Once a day (with the daily report): the digest to the owner's WhatsApp. Returns the delivery result or None."""
    from .models import CompanySettings

    s = CompanySettings.get()
    c = cfg(s)
    day = day or timezone.localdate()
    if not c["on"]["daily"] or (not force and c["last_digest"] == day.isoformat()):
        return None
    if not owner_number(s):
        return None
    res = deliver("daily", f"ملخص يوم {day:%d/%m/%Y}\n" + digest_text(day), s)
    if res.get("wa") == "ok" or any(v == "ok" for v in res.values()):
        s.refresh_from_db(fields=["menu_cfg"])
        a = dict((s.menu_cfg or {}).get("alerts") or {})
        a["last_digest"] = day.isoformat()
        s.menu_cfg = {**(s.menu_cfg or {}), "alerts": a}
        s.save(update_fields=["menu_cfg"])
    return res
