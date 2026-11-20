"""«تابع محلك من الهاتف»: the shop copy sends a small summary to Trio's Worker every few minutes, and the owner opens his own
link (an icon on his phone's home screen, «Trio») — no router ports, nothing of the shop's PC is reachable from outside.

The same per-shop secret (CompanySettings.mobile_key) links the owner's Telegram to Trio's bot and addresses the daily summary
the Worker mails or sends on Telegram, so the owner never needs a bot token or an app password.
"""
import datetime as dt
import hashlib
import json
import secrets
import threading
import time
import urllib.error
import urllib.request
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from . import license as LIC
from .models import CompanySettings

EVERY = 600  # seconds between two checks while the program runs
FORCE_AFTER = 1800  # resend unchanged data so the phone shows «online»
_state = {"started": False}


def key(s=None):
    s = s or CompanySettings.get()
    if not s.mobile_key:
        s.mobile_key = secrets.token_urlsafe(18)[:24].replace("-", "x").replace("_", "y")
        s.save(update_fields=["mobile_key"])
    return s.mobile_key


def on(s=None):
    from .modules import enabled

    return "mobile" in enabled(s or CompanySettings.get())


def api():
    from .pay import api_url

    return api_url(refresh=False)


def link(s=None):
    base = api()
    return f"{base}/m/{key(s)}" if base else ""


def info(refresh=False):
    """What Trio turned on in the Worker (Telegram bot name, mail) — cached in the feed state for a day."""
    from . import feed

    st = feed.state()
    cached = st.get("w_info") or {}
    if not refresh and cached.get("at", "") > (timezone.now() - dt.timedelta(days=1)).isoformat():
        return cached
    base = api()
    if not base:
        return cached
    try:
        with urllib.request.urlopen(urllib.request.Request(base + "/info", headers={"User-Agent": "Trio"}), timeout=15) as r:
            d = json.loads(r.read(1 << 20).decode() or "{}")
        cached = dict(tg=d.get("tg", ""), mail=bool(d.get("mail")), mobile=bool(d.get("mobile")), at=timezone.now().isoformat())
        feed._save(w_info=cached)
    except Exception:  # noqa: BLE001 — offline: keep what we knew
        pass
    return cached


def telegram_link(s=None):
    bot = info().get("tg", "")
    return f"https://t.me/{bot}?start={key(s)}" if bot else ""


def post(path, body, timeout=25):
    base = api()
    if not base:
        raise ConnectionError("البرنامج لا يعرف عنوان خادم Trio لم يتم بعد — تأكد من اتصال الجهاز بالإنترنت")
    req = urllib.request.Request(base + path, data=json.dumps(body, ensure_ascii=False, default=str).encode(),
                                 headers={"Content-Type": "application/json", "User-Agent": "Trio"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read(1 << 20).decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read(4096).decode()).get("error", "")
        except Exception:  # noqa: BLE001
            msg = ""
        raise ConnectionError(msg or f"سيرفر Trio رد بخطأ ({e.code})")


def ident(s):
    return dict(k=key(s), m=LIC.machine_code(), lic=s.license_code or "")


# ---------------------------------------------------------------- the summary the phone shows
def _f(x):
    return float(Decimal(x or 0).quantize(Decimal("0.01")))


def snapshot(now=None):
    from inventory.models import Item
    from pos.money import owner_balance
    from pos.models import Branch, Order, OrderLine, Shift
    from treasury import services as T
    from treasury.models import CashBox
    from ui.shop import _cogs, _sales, expiring

    s = CompanySettings.get()
    now = now or timezone.localtime()
    today = now.date()
    orders = list(Order.objects.filter(created_at_device__date=today).select_related("device__branch"))
    t = _sales(orders)
    month = _sales(Order.objects.filter(created_at_device__date__gte=today.replace(day=1), created_at_device__date__lte=today).only(
        "total", "vat", "paid_cash", "paid_card", "paid_credit", "is_return", "cancelled"))
    drawer = CashBox.objects.filter(active=True, kind="cash").first()
    top = {}
    for ln in OrderLine.objects.filter(order__in=[o for o in orders if not o.cancelled and not o.is_return]).select_related("item"):
        r = top.setdefault(ln.item.name, [Decimal(0), Decimal(0)])
        r[0] += ln.qty or 0
        r[1] += (ln.qty or 0) * (ln.price or 0) - (ln.discount or 0)
    low = [i for i in Item.objects.filter(active=True, reorder_level__isnull=False).order_by("name") if i.qty_on_hand <= i.reorder_level]
    exp = expiring(14)
    recent = [dict(n=(o.shift_seq or o.pk), t=timezone.localtime(o.created_at_device).strftime("%H:%M"), v=_f(o.total),
                   pay="آجل" if o.paid_credit else "مع الطيار" if o.paid_rider else ("بطاقة" if o.paid_card and not o.paid_cash else "نقدي"), r=o.is_return, x=o.cancelled,
                   d=o.device.name if o.device_id else "")
              for o in sorted(orders, key=lambda o: o.created_at_device, reverse=True)[:12]]
    shifts = [dict(c=sh.cashier, at=timezone.localtime(sh.opened_at).strftime("%H:%M"), d=sh.device.name)
              for sh in Shift.objects.filter(closed_at__isnull=True).select_related("device")[:10]]
    data = dict(
        shop=s.name, at=now.isoformat(timespec="seconds"), day=today.isoformat(),
        today=dict(net=_f(t["net"]), n=t["n"], cash=_f(t["cash"]), card=_f(t["card"]), credit=_f(t["credit"]), returns=_f(t["returns"]),
                   profit=_f(t["net_ex_vat"] - _cogs(orders))),
        month=dict(net=_f(month["net"]), n=month["n"]),
        drawer=_f(T.cashbox_balance(drawer)) if drawer else 0,
        top=[dict(n=k, q=_f(v[0]), v=_f(v[1])) for k, v in sorted(top.items(), key=lambda kv: -kv[1][1])[:6]],
        low=[dict(n=i.name, q=_f(i.qty_on_hand), r=_f(i.reorder_level)) for i in low[:15]], low_n=len(low),
        exp=[dict(n=r["item"].name, d=r["days"]) for r in exp[:10]], exp_n=len(exp),
        recent=recent, shifts=shifts,
    )
    try:
        data["owner"] = _f(owner_balance())
    except Exception:  # noqa: BLE001
        pass
    branches = list(Branch.objects.filter(active=True))
    if len(branches) > 1:
        data["branches"] = [dict(n=b.name, v=_f(_sales([o for o in orders if o.device.branch_id == b.pk])["net"]),
                                 cash=_f(T.cashbox_balance(b.cashbox))) for b in branches]
    if s.business == "gaming":
        from gaming.models import PlaySession

        data["play"] = [dict(n=p.station.name if p.station_id else "تيك أواي", at=timezone.localtime(p.opened_at).strftime("%H:%M"),
                             m=int((now - p.opened_at).total_seconds() // 60))
                        for p in PlaySession.objects.filter(state="open").select_related("station")[:20]]
        from django.db.models import Sum as _S

        closed = PlaySession.objects.filter(state="closed", closed_at__date=today)
        disc = closed.filter(discount__gt=0)
        data["hall"] = dict(disc=_f(disc.aggregate(s=_S("discount"))["s"]), disc_n=disc.count(),
                            void=PlaySession.objects.filter(state="void", closed_at__date=today).count())
    if s.business == "clinic":
        try:
            data["clinic"] = _clinic(today)
        except Exception:  # noqa: BLE001 — the phone summary never fails because of one part
            pass
    if s.owner_whatsapp:
        data["wa"] = "".join(ch for ch in s.owner_whatsapp if ch.isdigit())
    return data


def _clinic(today):
    """The clinic on the owner's phone: today's visits and queue, no-shows, each doctor's share, what the insurers owe."""
    from django.db.models import Sum

    from pos.clinic import services as C
    from pos.clinic.models import Appointment, Doctor, InsuranceCompany, Visit

    t = C.today_summary(today)
    appts = Appointment.objects.filter(date=today)
    docs = []
    for d in Doctor.objects.filter(active=True):
        paid = Visit.objects.filter(doctor=d, state="paid", opened_at__date=today)
        n = paid.count()
        if n or appts.filter(doctor=d, state__in=["waiting", "inside"]).exists():
            docs.append(dict(n=str(d), v=n, s=_f(paid.aggregate(s=Sum("doctor_share"))["s"]),
                             w=appts.filter(doctor=d, state="waiting").count()))
    ins = [(i.name, C._balance(i.partner)) for i in InsuranceCompany.objects.filter(partner__isnull=False).select_related("partner")]
    return dict(visits=t["visits"], revenue=_f(t["revenue"]), shares=_f(t["shares"]), waiting=t["waiting"], inside=t["inside"],
                booked=t["booked"], noshow=appts.filter(state="noshow").count(), docs=docs[:12],
                ins=_f(sum((b for _, b in ins), Decimal(0))), ins_list=[dict(n=n, v=_f(b)) for n, b in ins if b][:10])


# ---------------------------------------------------------------- sending it
def _mark_file():
    return Path(settings.BASE_DIR) / "data" / "mobile.json"


def _mark():
    try:
        return json.loads(_mark_file().read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def push(force=False, now=None):
    """Sends the summary when it changed (or every half hour so the phone shows the shop online). Returns a short status."""
    s = CompanySettings.get()
    if not on(s) or s.suspended:
        return "off"
    if not api():
        return "no-api"
    data = snapshot(now)
    body = dict(data)
    body.pop("at", None)
    h = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
    mark = _mark()
    if not force and mark.get("h") == h and time.time() - mark.get("t", 0) < FORCE_AFTER:
        return "same"
    post("/snap", dict(ident(s), d=data, v=__import__("core.version", fromlist=["VERSION"]).VERSION))
    f = _mark_file()
    f.parent.mkdir(exist_ok=True)
    f.write_text(json.dumps(dict(h=h, t=time.time())), encoding="utf-8")
    return "sent"


def _loop():
    from django.db import close_old_connections

    time.sleep(20)
    while True:
        try:
            close_old_connections()
            push()
        except Exception:  # noqa: BLE001 — no internet / the Worker is down: try again next round
            pass
        try:
            from . import menu_pub

            menu_pub.push()  # cafés: the QR menu follows the prices
        except Exception:  # noqa: BLE001
            pass
        finally:
            close_old_connections()
        time.sleep(EVERY)


def start():
    """Started once by the web server process (first request): keeps the phone page fresh while the program runs."""
    if _state["started"]:
        return
    _state["started"] = True
    threading.Thread(target=_loop, name="acc-mobile", daemon=True).start()


# ---------------------------------------------------------------- daily summary through the Worker (Telegram bot + mail)
def notify(text, subject, telegram=True, email=""):
    """Returns {"tg": ok|error, "mail": ok|error} from the Worker."""
    s = CompanySettings.get()
    res = post("/notify", dict(ident(s), text=text, subject=subject, tg=bool(telegram), email=email, shop=s.name,
                               lic="".join((s.license_code or "").split()) if email else ""))
    if res.get("error"):
        raise ConnectionError(res["error"])
    return res


def telegram_status(s=None):
    """How many Telegram chats are linked to this shop (None when unknown / offline)."""
    s = s or CompanySettings.get()
    try:
        return int(post("/tg/status", ident(s), timeout=12).get("chats", 0))
    except Exception:  # noqa: BLE001
        return None
