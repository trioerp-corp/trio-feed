"""«هاتف صاحب المحل»: the owner, away from the shop, takes money out (recorded as his drawing), approves / refuses the cashiers'
requests and adds a purchase invoice he paid in the street — from the Trio icon on his phone.

Nothing can reach the shop's computer from outside: the phone leaves its order at Trio's Worker, and this program picks the
orders up (every minute; every 10 seconds while the phone app is open), does each one once and sends the answer back.

Trust: linking shows a one-time code on this computer (QR). The phone derives two values from it: one the Worker uses to
know the phone (pair hash), and a signing secret the Worker never sees. Every order carries an HMAC with that secret, so a
copy of the link, or anyone on Trio's server, cannot make the program record anything. The Worker also asks for the owner's
PIN before money orders (drawing, purchase).
"""
import datetime as dt
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from inventory.reorder import DEFAULT as REORDER_DEFAULT  # a new item bought from the phone starts with an editable limit
from django.db import transaction
from django.utils import timezone

from . import mobile
from .models import CompanySettings, OwnerPhone, PhoneCommand

ALPH = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
SLOW, FAST = int(os.environ.get("TRIO_PHONE_SLOW", 60)), 10  # seconds between two pulls (FAST while the owner has the app open)
_state = {"started": False, "fast_until": 0, "cat": "", "cat_at": 0}


def _derive(code, what):
    return hashlib.sha256(f"trio-{what}:{code.replace('-', '').upper()}".encode()).hexdigest()


def start_pair(user, name="هاتف صاحب المحل"):
    """→ (code shown on the screen, link with the code in its #fragment — the browser never sends it to any server)."""
    s = CompanySettings.get()
    if not mobile.on(s):
        raise ValidationError("ميزة «الموبايل والتقارير» غير مفتوحة لديك")
    raw = "".join(secrets.choice(ALPH) for _ in range(12))
    code = "-".join(raw[i:i + 4] for i in range(0, 12, 4))
    ph = _derive(code, "pair")
    exp = timezone.now() + dt.timedelta(minutes=15)
    mobile.post("/m/pair", dict(mobile.ident(s), ph=ph, exp=exp.isoformat(), owner=user.get_full_name() or user.get_username()), timeout=15)
    OwnerPhone.objects.filter(active=False).delete()
    OwnerPhone.objects.create(name=name, user=user, pair_hash=ph, secret=_derive(code, "sign"), expires=exp)
    return code, f"{mobile.link(s)}#pair={code}"


def unpair(phone):
    try:
        mobile.post("/m/unpair", dict(mobile.ident(CompanySettings.get()), ph=phone.pair_hash), timeout=15)
    except ConnectionError:
        pass  # the program refuses its orders anyway: its secret is gone
    phone.delete()


def _mac_ok(phone, c):
    want = hmac.new(phone.secret.encode(), f"{c['id']}\n{c['kind']}\n{c['body']}".encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, str(c.get("mac", "")))


def _dec(x, what):
    try:
        v = Decimal(str(x).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        raise ValidationError(f"{what} ليس رقمًا")
    if not v.is_finite():
        raise ValidationError(f"{what} ليس رقمًا")
    return v


def _do(phone, kind, b):
    from pos import money as M
    from pos import voids as V
    from pos.models import ChangeRequest
    from treasury import services as T
    from treasury.models import CashBox

    owner = phone.user
    who = f"{owner.get_username()} من الهاتف"
    if kind == "drawing":
        amount = _dec(b.get("amount"), "المبلغ")
        src = str(b.get("from", ""))
        if src == "card":
            from pos.services import card_clearing

            acc, label = card_clearing(), "أرصدة البطاقات"
        else:
            box = CashBox.objects.filter(pk=int(src or 0), active=True).first() if src.isdigit() else None
            if not box:
                raise ValidationError("هذه الخزينة غير موجودة")
            if T.cashbox_balance(box) < amount:
                raise ValidationError(f"رصيد {box.name} أقل من المبلغ ({T.cashbox_balance(box):,.2f})")
            acc, label = box.account, box.name
        M.post("drawing", amount, timezone.localdate(), cash_account=acc, note=(str(b.get("note", "")) or "سحب من الهاتف")[:120],
               uid=f"PH-{b['_id']}", by=who, user=owner)
        return f"تم تسجيل سحب {amount:,.2f} من {label} على حسابك"
    if kind == "decide":
        req = ChangeRequest.objects.filter(uid=str(b.get("req", ""))).first()
        if not req:
            raise ValidationError("هذا الطلب غير موجود")
        V.decide(req, bool(b.get("approve")), owner, str(b.get("note", ""))[:200])
        return ("تمت الموافقة على " if b.get("approve") else "رُفض ") + f"طلب {req.get_kind_display()} {req.target}"
    if kind == "purchase":
        return _purchase(owner, b)
    raise ValidationError("أمر غير معروف — حدّث البرنامج")


def _purchase(owner, b):
    from inventory import purchases as PUR
    from inventory.models import Item, ItemCategory
    from ledger.models import Partner
    from pos.models import Branch
    from treasury.models import CashBox

    br = Branch.objects.filter(active=True).order_by("pk").first()
    sup = Partner.objects.filter(pk=int(b["supplier"]), kind="supplier").first() if str(b.get("supplier", "")).isdigit() else None
    if not sup and str(b.get("new_supplier", "")).strip():
        sup = Partner.objects.filter(name=b["new_supplier"].strip()[:150], kind="supplier").first() or \
            Partner.objects.create(name=b["new_supplier"].strip()[:150], kind="supplier")
    if not sup:
        raise ValidationError("اختر المورد")
    rows = []
    for ln in (b.get("lines") or [])[:60]:
        it = Item.objects.filter(pk=int(ln["item"]), active=True).first() if str(ln.get("item", "")).isdigit() else None
        if not it and str(ln.get("new", "")).strip():
            name = ln["new"].strip()[:150]
            it = Item.objects.filter(name=name).first()
            if not it:
                n = Item.objects.filter(code__startswith="PH").count() + 1
                while Item.objects.filter(code=f"PH{n:04d}").exists():
                    n += 1
                it = Item.objects.create(code=f"PH{n:04d}", name=name, category=ItemCategory.objects.order_by("pk").first(), unit="قطعة",
                                         sale_price=_dec(ln.get("sale") or 0, "سعر البيع"), reorder_level=REORDER_DEFAULT)
        if not it:
            raise ValidationError("يوجد سطر بدون صنف")
        rows.append(dict(item=it, qty=_dec(ln.get("qty"), "الكمية"), unit_cost=_dec(ln.get("cost"), "السعر")))
    pay = b.get("pay") if b.get("pay") in ("cash", "credit") else "credit"
    box = None
    if pay == "cash":
        box = CashBox.objects.filter(pk=int(b["box"]), active=True).first() if str(b.get("box", "")).isdigit() else None
        if not box:
            raise ValidationError("اختر الخزينة التي دُفع منها")
    from inventory.models import Warehouse

    wh = br.warehouse if br and br.warehouse_id else Warehouse.objects.filter(active=True).first()
    _, _, msg = PUR.record(warehouse=wh, supplier=sup, date=timezone.localdate(), lines=rows, invoice=str(b.get("invoice", ""))[:30],
                           pay=pay, box=box, user=owner, note="من هاتف صاحب المحل")
    return msg


def run(c):
    """One order from the Worker → its answer. Done once: the same id again returns the first answer."""
    done = PhoneCommand.objects.filter(cid=str(c.get("id", ""))[:40]).first()
    if done:
        return done
    phone = OwnerPhone.objects.filter(pair_hash=str(c.get("ph", "")), active=True).select_related("user").first()
    rec = PhoneCommand(cid=str(c.get("id", ""))[:40], phone=phone, kind=str(c.get("kind", ""))[:20])
    try:
        if not phone or not _mac_ok(phone, c):
            raise ValidationError("هذا الأمر ليس من هاتف مربوط بالبرنامج — تم تجاهله")
        if not phone.user.is_active:
            raise ValidationError("حساب صاحب المحل هذا مُغلق")
        b = json.loads(c["body"])
        rec.body = b
        b["_id"] = rec.cid
        with transaction.atomic():
            rec.result = _do(phone, rec.kind, b)[:400]
            rec.ok = True
    except ValidationError as e:
        rec.result = " ".join(e.messages)[:400]
    except Exception as e:  # noqa: BLE001 — a fault never stops the other orders
        import logging

        logging.getLogger("trio").exception("phone order %s", rec.cid)
        rec.result = f"حدثت مشكلة في البرنامج ({type(e).__name__}) — بلّغ Trio"
    rec.save()
    try:
        from . import audit

        audit.log("phone", rec, summary=f"هاتف صاحب المحل: {rec.kind} — {rec.result}"[:300])
    except Exception:  # noqa: BLE001
        pass
    return rec


def _pending():
    from pos.models import ChangeRequest, Order

    out = []
    for r in ChangeRequest.objects.filter(state="pending").order_by("-created_at")[:20]:
        o = Order.objects.filter(uid=r.target).only("total").first()
        out.append(dict(uid=r.uid, kind=r.get_kind_display(), target=r.target, why=r.reason, by=r.requested_by,
                        at=timezone.localtime(r.created_at).strftime("%d/%m %H:%M"), v=float(o.total) if o else None))
    return out


def _lists():
    """What the phone needs to fill its forms: money boxes with balances, suppliers."""
    from ledger.models import Partner
    from treasury import services as T
    from treasury.models import CashBox

    boxes = [dict(i=b.pk, n=b.name, k=b.kind, v=float(T.cashbox_balance(b))) for b in CashBox.objects.filter(active=True).order_by("pk")]
    sup = [dict(i=p.pk, n=p.name) for p in Partner.objects.filter(kind="supplier", active=True).order_by("name")[:400]]
    return dict(boxes=boxes, sup=sup)


def _catalog():
    """Items for the phone's purchase form (sent when they change, at most every 30 minutes)."""
    from inventory.models import Item

    rows = [[i.pk, i.name, i.code, i.barcode or "", float(i.sale_price or 0)] for i in Item.objects.filter(active=True).order_by("name")[:5000]]
    body = json.dumps(rows, ensure_ascii=False)
    h = hashlib.sha256(body.encode()).hexdigest()[:16]
    if h == _state["cat"] and time.time() - _state["cat_at"] < 86400:
        return
    if time.time() - _state["cat_at"] < 1800 and _state["cat"]:
        return
    mobile.post("/m/cat", dict(mobile.ident(CompanySettings.get()), items=rows), timeout=30)
    _state.update(cat=h, cat_at=time.time())


def pull():
    """One round with the Worker: answers of the last round go up, new orders come down and are done. → seconds to wait."""
    s = CompanySettings.get()
    if not mobile.on(s) or s.suspended or not mobile.api():
        return SLOW * 5
    if not OwnerPhone.objects.exists():
        return SLOW * 5
    res = [dict(id=r.cid, ok=r.ok, msg=r.result) for r in PhoneCommand.objects.filter(reported=False)[:50]]
    out = mobile.post("/m/q", dict(mobile.ident(s), res=res, pend=_pending(), **_lists()), timeout=25)
    PhoneCommand.objects.filter(cid__in=[r["id"] for r in res]).update(reported=True)
    for p in out.get("phones") or []:  # phones that entered their code / were seen
        OwnerPhone.objects.filter(pair_hash=p.get("ph", "")).update(active=True, last_seen=timezone.now(), name=str(p.get("name") or "موبايل")[:80])
    OwnerPhone.objects.filter(active=False, expires__lt=timezone.now()).delete()
    ran = [run(c) for c in out.get("cmds") or []]
    if ran:
        mobile.push(force=True)  # the phone sees the new balance / the request gone
    try:
        _catalog()
    except Exception:  # noqa: BLE001
        pass
    return FAST if out.get("fast") or ran else SLOW


def _loop():
    from django.db import close_old_connections

    time.sleep(min(30, SLOW))
    wait = SLOW
    while True:
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
    threading.Thread(target=_loop, name="trio-owner-phone", daemon=True).start()
