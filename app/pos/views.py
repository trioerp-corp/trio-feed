import json
import logging
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from core.models import CompanySettings

from . import services as P
from .models import Device


def _device(request):
    if not CompanySettings.get().pos_enabled:
        return None, JsonResponse({"error": "نقاط البيع غير مفعلة"}, status=403)
    tok = request.headers.get("X-Device-Token", "")
    dev = Device.objects.filter(token=tok, active=True, branch__active=True).select_related("branch", "branch__cashbox", "branch__warehouse").first()
    if not dev:
        return None, JsonResponse({"error": "مفتاح الجهاز غير صحيح"}, status=401)
    return dev, None


def app(request):
    if not CompanySettings.get().pos_enabled:
        raise Http404("نقاط البيع غير مفعلة")
    return render(request, "pos/app.html")


def sw(request):
    js = """const C='gg-pos-v1';self.addEventListener('install',e=>e.waitUntil(caches.open(C).then(c=>c.addAll(['/pos/']))));
self.addEventListener('fetch',e=>{const u=new URL(e.request.url);if(e.request.method!=='GET'||u.pathname.startsWith('/pos/api/'))return;
e.respondWith(fetch(e.request).then(r=>{const cp=r.clone();caches.open(C).then(c=>c.put(e.request,cp));return r}).catch(()=>caches.match(e.request)))});"""
    return HttpResponse(js, content_type="application/javascript")


def api_catalog(request):
    dev, err = _device(request)
    if err:
        return err
    return JsonResponse(P.catalog(dev))


@csrf_exempt
def api_sync(request):
    dev, err = _device(request)
    if err:
        return err
    if request.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)
    try:
        body = json.loads(request.body or "{}")
    except ValueError:
        return JsonResponse({"error": "bad json"}, status=400)
    accepted, errors = [], {}

    def each(rows, fn, done):
        """One bad paper never blocks the rest of the queue: it is reported (and logged) and the others go in."""
        for row in rows or []:
            uid = str((row or {}).get("uid", "?")) if isinstance(row, dict) else "?"
            try:
                with transaction.atomic():
                    fn(row)
                done.append(uid)
            except ValidationError as e:
                errors[uid] = "؛ ".join(e.messages)
            except Exception as e:  # noqa: BLE001 — a malformed row (missing field, bad number) or a fault in posting
                logging.getLogger("trio").exception("POS sync: %s refused", uid)
                errors[uid] = f"تعذّر التسجيل ({type(e).__name__}) — بلّغ الدعم"

    opened, moved, reqs, closed = [], [], [], []
    shifts = [sh for sh in body.get("shifts") or [] if isinstance(sh, dict)]
    each([sh for sh in shifts if not sh.get("closed_at")], lambda sh: P.ingest_shift(dev, sh), opened)
    sold = set()

    def take(o):
        order, _ = P.ingest_order(dev, o, renumber=True)
        sold.update(order.lines.values_list("item_id", flat=True))

    each(body.get("orders"), take, accepted)
    each(body.get("cash_moves"), lambda m: P.ingest_cash_move(dev, m), moved)
    each(body.get("requests"), lambda r: P.ingest_request(dev, r), reqs)
    each([sh for sh in shifts if sh.get("closed_at")], lambda sh: P.ingest_shift(dev, sh), closed)  # closes after its orders
    if dev.stuck != errors:
        dev.stuck = errors
        dev.save(update_fields=["stuck"])
    dev.last_sync = timezone.now()
    dev.save(update_fields=["last_sync"])
    low = []
    if sold:  # «حد الطلب» reached by these sales (recipe ingredients included) — the till shows it in a pop-up
        from inventory.reorder import low as low_items

        low = low_items(sold)
    return JsonResponse({"accepted": accepted, "cash_moves": moved, "requests": reqs, "errors": errors, "closed_shifts": closed, "low": low})


@csrf_exempt
def api_cashier(request):
    dev, err = _device(request)
    if err:
        return err
    from core import throttle as TH

    try:
        body = json.loads(request.body or "{}")
    except ValueError:
        body = {}
    name = str(body.get("username", ""))
    if TH.blocked(name, f"dev{dev.pk}"):
        return JsonResponse({"ok": False, "error": TH.MSG}, status=403)
    u = P.verify_cashier(name, body.get("password", ""))
    if not u:
        TH.fail(name, f"dev{dev.pk}")
        return JsonResponse({"ok": False, "error": "اسم الكاشير أو كلمة السر غير صحيحة"}, status=403)
    TH.clear(name)
    return JsonResponse({"ok": True, "name": u.first_name or u.username, "role": P.till_role(u)})


@csrf_exempt
def api_return_ok(request):
    """A return at the till: the owner / manager types his name + password on the till (online), the till gets a signed
    approval for this amount and puts it on the return; the sync refuses a return without it (pos.services.check_return)."""
    dev, err = _device(request)
    if err:
        return err
    if request.method != "POST":
        return JsonResponse({"error": "POST فقط"}, status=405)
    from django.contrib.auth import authenticate

    from core import throttle as TH

    from .voids import is_owner

    b = _tables_json(request)
    reason = str(b.get("reason") or "").strip()[:200]
    try:
        amount = Decimal(str(b.get("amount") or "0")).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        amount = Decimal(-1)
    if not reason:
        return JsonResponse({"error": "اكتب سبب المرتجع"}, status=400)
    if amount <= 0:
        return JsonResponse({"error": "مبلغ المرتجع غير صحيح"}, status=400)
    s = CompanySettings.get()
    if s.cashier_can_edit:
        return JsonResponse({"ok": True, "token": "", "by": ""})
    name = str(b.get("owner") or "")[:150]
    if TH.blocked(name, f"dev{dev.pk}"):
        return JsonResponse({"error": TH.MSG}, status=403)
    u = authenticate(username=name, password=str(b.get("password") or ""))
    if not (u and u.is_active and is_owner(u)):
        TH.fail(name, f"dev{dev.pk}")
        return JsonResponse({"error": "اسم المستخدم أو كلمة السر غير صحيحة، أو ليس له صلاحية اعتماد المرتجع"}, status=403)
    TH.clear(name)
    return JsonResponse({"ok": True, "token": P.return_token(dev, u, reason, amount), "by": u.first_name or u.username})


@csrf_exempt
def api_kitchen(request):
    """The kitchen ticket of a new order on the kitchen printer (1.22) — printed by the computer the program runs on."""
    dev, err = _device(request)
    if err:
        return err
    if request.method != "POST":
        return JsonResponse({"error": "POST فقط"}, status=405)
    s = CompanySettings.get()
    if s.kitchen_ticket != "printer":
        return JsonResponse({"error": "طابعة المطبخ غير مفعلة"}, status=409)
    try:
        body = json.loads(request.body or "{}")
        o = body.get("o") if isinstance(body.get("o"), dict) else {}
        if not str(o.get("uid", "")).startswith(f"{dev.branch.code}-{dev.code}-") or not isinstance(o.get("lines"), list):
            return JsonResponse({"error": "طلب غير صحيح"}, status=400)
        from . import kitchen

        kitchen.print_order(o, str(body.get("cashier") or "")[:60])
    except (ValueError, TypeError):
        return JsonResponse({"error": "طلب غير صحيح"}, status=400)
    except RuntimeError as e:
        return JsonResponse({"error": str(e)}, status=503)
    except OSError as e:  # noqa: BLE001 — the printer driver
        logging.getLogger(__name__).warning("kitchen printer: %s", e)
        return JsonResponse({"error": "تعذرت الطباعة على طابعة المطبخ"}, status=503)
    return JsonResponse({"ok": True})


def api_delivery(request):
    """1.24: the delivery orders still with their riders (this branch) — the till's «طلبات الدليفري» panel."""
    dev, err = _device(request)
    if err:
        return err
    from . import delivery as D

    if not D.cfg()["on"]:
        return JsonResponse({"orders": []})
    return JsonResponse({"orders": D.open_json(dev.branch)})


# ---------------------------------------------------------------- tables (1.23): the till asks the program, every waiter sees the same bill
def _tables_json(request):
    try:
        return json.loads(request.body or "{}")
    except ValueError:
        return {}


@csrf_exempt
def api_tables(request, tid=None, tab=None, op=None):
    from . import tables as T
    from .models import DiningTable, TableTab

    dev, err = _device(request)
    if err:
        return err
    s = CompanySettings.get()
    if s.business not in T.TABLE_BIZ:
        return JsonResponse({"error": "نظام الترابيزات للكافيهات والمطاعم"}, status=404)
    b = _tables_json(request) if request.method == "POST" else {}
    who = str(b.get("by") or "")[:60] or dev.code
    try:
        if tid is None and tab is None:
            if request.method == "POST" and op == "claim":  # customer orders to print on this till (kitchen ticket «same printer»)
                return JsonResponse({"prints": T.claim_prints() if s.kitchen_ticket == "same" else []})
            return JsonResponse({"tables": T.state(), "cfg": T.cfg(s)})
        if tid is not None:
            table = DiningTable.objects.filter(pk=tid).first()
            if not table:
                return JsonResponse({"error": "الترابيزة غير موجودة"}, status=404)
            if request.method == "GET":
                t = T.open_tab(table)
                return JsonResponse({"tab": T.tab_json(t) if t else None, "table": {"id": table.pk, "name": table.name}})
            if op == "add":
                t, n = T.add(table, b.get("lines") or [], who, note=b.get("note", ""))
                out = {"tab": T.tab_json(t), "batch": n}
                if s.kitchen_ticket == "printer":
                    try:
                        T.print_batch(table.name, T.batch_lines(t, n), b.get("note", ""), who)
                    except (RuntimeError, OSError) as e:
                        out["print_error"] = str(e)
                return JsonResponse(out)
        t = TableTab.objects.select_related("table").filter(pk=tab).first()
        if not t:
            return JsonResponse({"error": "الحساب غير موجود"}, status=404)
        if op == "close":
            uid = str(b.get("uid") or "")
            if not uid.startswith(f"{dev.branch.code}-{dev.code}-"):
                return JsonResponse({"error": "رقم الفاتورة غير صحيح"}, status=400)
            T.close(t, uid, who)
            return JsonResponse({"ok": True})
        if op == "part":  # 1.24 split bill by lines: one part paid as its own sale
            uid = str(b.get("uid") or "")
            if not uid.startswith(f"{dev.branch.code}-{dev.code}-"):
                return JsonResponse({"error": "رقم الفاتورة غير صحيح"}, status=400)
            t = T.pay_part(t, uid, b.get("lines") or [], who)
            return JsonResponse({"tab": T.tab_json(t), "closed": t.state == "paid"})
        if op == "move":
            to = DiningTable.objects.filter(pk=b.get("to") or 0).first()
            if not to:
                return JsonResponse({"error": "اختر الترابيزة"}, status=400)
            return JsonResponse({"tab": T.tab_json(T.move(t, to, who))})
        if op == "remove":
            ok = s.cashier_can_edit
            if not ok and b.get("owner"):  # the owner / manager types his password on the till
                from django.contrib.auth import authenticate

                from .voids import is_owner

                u = authenticate(username=str(b.get("owner"))[:150], password=str(b.get("password") or ""))
                ok = bool(u and is_owner(u))
                if not ok:
                    return JsonResponse({"error": "اسم المستخدم أو كلمة السر غير صحيحة، أو ليس له صلاحية الإلغاء"}, status=403)
            return JsonResponse({"tab": T.tab_json(T.remove(t, b.get("line") or 0, who, may_cancel_sent=ok, qty=b.get("qty")))})
        if op == "confirm":
            t = T.confirm(t, int(b.get("n") or 0), who)
            if s.kitchen_ticket == "printer":
                try:
                    T.print_on_kitchen(t, int(b.get("n") or 0))
                except (RuntimeError, OSError):
                    pass
            return JsonResponse({"tab": T.tab_json(t)})
        if op == "drop":
            return JsonResponse({"tab": T.tab_json(T.drop_batch(t, int(b.get("n") or 0), who))})
    except ValidationError as e:
        return JsonResponse({"error": "؛ ".join(e.messages)}, status=400)
    return JsonResponse({"error": "طلب غير معروف"}, status=400)
