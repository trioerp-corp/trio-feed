"""Customers order from the QR on their table (1.23, when the owner allows it in «الترابيزات»).

The phone sends the order to Trio's Worker (the shop's computer is not on the internet); this loop picks the orders up,
prices them from the program, puts them on the table (pos.tables.add, src «qr») and answers each one (taken / refused with
the reason) — the customer's page shows that answer. The Worker re-sends an order that was not answered, so every order id
is remembered here and never added twice.
"""
import json
import threading
import time
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError

from core import mobile
from core.models import CompanySettings

FAST, SLOW, IDLE = 10, 25, 120
_state = {"started": False, "last": 0.0}


def _seen_file():
    return Path(settings.BASE_DIR) / "data" / "qr-orders.json"


def _seen():
    try:
        return json.loads(_seen_file().read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _remember(seen):
    keep = dict(sorted(seen.items(), key=lambda x: x[1].get("t", 0))[-600:])
    f = _seen_file()
    f.parent.mkdir(exist_ok=True)
    f.write_text(json.dumps(keep, ensure_ascii=False), encoding="utf-8")


def ingest(o, seen=None):
    """One order from the Worker → (ok, message). Never twice for the same id."""
    from . import tables as T
    from .models import DiningTable

    own = seen is None
    seen = _seen() if own else seen
    qid = str(o.get("id") or "")[:40]
    if not qid:
        return False, "طلب ناقص"
    if qid in seen:
        return seen[qid]["ok"], seen[qid]["msg"]
    s = CompanySettings.get()
    c = T.cfg(s)
    try:
        if not (c["on"] and c["qr_order"]):
            raise ValidationError("الطلب من QR متوقف الآن — اطلب من الويتر")
        table = DiningTable.objects.filter(qr=str(o.get("t") or "")[:12], active=True).first()
        if not table:
            raise ValidationError("هذه الترابيزة غير موجودة — اطلب من الويتر")
        lines = []
        for x in (o.get("lines") or [])[:30]:
            if isinstance(x, dict):
                lines.append(dict(code=str(x.get("c") or ""), qty=x.get("q") or 1, adds=[str(a) for a in (x.get("a") or [])][:10], note=str(x.get("n") or "")[:120]))
        note = " — ".join(v for v in (str(o.get("name") or "")[:40], str(o.get("note") or "")[:150]) if v)
        tab, n = T.add(table, lines, "الزبون (QR)", src="qr", hold=c["qr_confirm"], note=note)
        if not c["qr_confirm"] and s.kitchen_ticket == "printer":
            try:
                T.print_on_kitchen(tab, n)
                b = next(b for b in tab.batches if b["n"] == n)
                b["printed"] = True
                tab.save(update_fields=["batches"])
            except (RuntimeError, OSError):
                pass  # the till prints it (claim) — or the waiter sees it on the table
        ok, msg = True, ("وصل طلبك — سيؤكده الويتر حالًا" if c["qr_confirm"] else "وصل طلبك إلى المطبخ ✔")
    except ValidationError as e:
        ok, msg = False, "؛ ".join(e.messages)
    seen[qid] = dict(ok=ok, msg=msg, t=time.time())
    if own:
        _remember(seen)
    return ok, msg


def pull():
    s = CompanySettings.get()
    from . import tables as T

    c = T.cfg(s)
    if not (c["on"] and c["qr_order"]) or s.suspended or not mobile.api():
        return IDLE
    seen = _seen()
    out = mobile.post("/menu/orders", dict(mobile.ident(s)), timeout=20)
    res = []
    for o in out.get("orders") or []:
        ok, msg = ingest(o, seen)
        res.append(dict(id=o.get("id"), ok=ok, msg=msg))
    if res:
        _remember(seen)
        mobile.post("/menu/orders", dict(mobile.ident(s), res=res), timeout=20)
        _state["last"] = time.time()
    return FAST if time.time() - _state["last"] < 900 else SLOW


def _loop():
    from django.db import close_old_connections

    time.sleep(20)
    while True:
        wait = SLOW
        try:
            close_old_connections()
            wait = pull()
        except Exception:  # noqa: BLE001 — offline / the Worker is down: next round
            wait = SLOW
        finally:
            close_old_connections()
        time.sleep(wait)


def start():
    if _state["started"]:
        return
    _state["started"] = True
    threading.Thread(target=_loop, name="trio-qr-orders", daemon=True).start()
