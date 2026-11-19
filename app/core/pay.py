"""Paying by card (Paymob) — the licence comes back on its own.

The ACC's online address (a Cloudflare Worker, announced in the command channel by the TRIOP code) creates the Paymob
checkout and, when Paymob confirms the payment, signs the licence with Trio's *sales key* (certified by Trio's own
key — see license.parse «TRIOS»). This copy only:
  - asks for a checkout (what the owner picked + this machine + its current licence for upgrades) and opens Paymob,
  - asks for the order until it is paid, then installs the licence (the same as pasting it),
  - or claims an order paid on the sales page («رقم الطلب» + phone) for this machine.
The price is always worked out by the Worker from its own price list — nothing the browser sends can lower it.
"""
import json
import urllib.error
import urllib.request

from . import license as LIC


class PayError(Exception):
    pass


def api_url(s=None, refresh=True):
    from . import feed

    u = feed.state().get("api", "")
    if not u and refresh:
        try:
            feed.check(s)
        except Exception:  # noqa: BLE001
            pass
        u = feed.state().get("api", "")
    return u if u.startswith("https://") or u.startswith("http://127.0.0.1") else ""


def enabled():
    """The ACC turned card payments on (TRIOP code with pay=true) and this copy knows the address."""
    from . import feed

    st = feed.state()
    return bool(st.get("api_pay") and api_url(refresh=False))


def _call(path, body=None, s=None, timeout=30):
    base = api_url(s)
    if not base:
        raise PayError("الدفع الإلكتروني غير متاح الآن — تأكد من اتصال الجهاز بالإنترنت، أو اطلب عبر واتساب")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, headers={"Content-Type": "application/json", "User-Agent": "Trio"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read(1 << 20).decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read(4096).decode()).get("error", "")
        except Exception:  # noqa: BLE001
            msg = ""
        raise PayError(msg or f"الدفع الإلكتروني ردّ بخطأ ({e.code})")
    except (OSError, ValueError) as e:
        raise PayError(f"تعذّر الوصول إلى الدفع الإلكتروني — هل الجهاز متصل بالإنترنت؟ ({type(e).__name__})")


def checkout(sel, s, back):
    """sel: plan, extras, life (lifetime / lifetime_updates), renew. Returns dict(id, url, amount)."""
    body = dict(sel, m=LIC.machine_code(), business=s.business, name=(s.name or "")[:100], phone=(sel.get("phone") or s.phone or "")[:40],
                lic=s.license_code or "", back=back)
    res = _call("/checkout", body, s)
    if not res.get("url") or not res.get("id"):
        raise PayError(res.get("error") or "الدفع الإلكتروني غير متاح الآن")
    from . import feed

    feed._save(pay_order=res["id"])
    return res


def order(oid, s=None):
    return _call(f"/order?id={oid}&m={LIC.machine_code()}", None, s)


def claim(oid, phone, s=None):
    res = _call("/claim", dict(id=oid.strip().upper(), phone=phone.strip(), m=LIC.machine_code()), s)
    if not res.get("code"):
        raise PayError(res.get("error") or "هذا الطلب لم يُدفع بعد أو غير موجود")
    rep = res.get("rep") if isinstance(res.get("rep"), dict) else {}
    if rep.get("wa") or rep.get("email"):  # where the owner asked (on the order form) for his daily summary
        from .models import CompanySettings
        from .setup_service import wa_number

        cs = CompanySettings.get()
        cs.owner_whatsapp = wa_number(rep.get("wa")) or cs.owner_whatsapp
        cs.email_to = str(rep.get("email") or "").strip()[:300] or cs.email_to
        cs.save(update_fields=["owner_whatsapp", "email_to"])
    return res["code"]


def activate(code, s):
    """Installs a licence (pasted, or from a paid order). Returns [(level, message)]; raises ValueError."""
    from . import feed, updates

    code = "".join(code.split())
    lic = LIC.parse(code)
    if lic.get("m") != LIC.machine_code():
        raise ValueError("كود التفعيل هذا صادر لجهاز آخر")
    if LIC.revoked(lic):
        raise ValueError("تم إلغاء كود التفعيل هذا — تواصل مع Trio")
    out = []
    s.license_code = code
    s.save(update_fields=["license_code"])
    LIC.parse.cache_clear()
    out.append(("success", "تم تفعيل البرنامج ✔ " + ("— مدى الحياة" if lic.get("l") else f"حتى {lic.get('e')}")))
    if s.setup_done and lic.get("p"):
        plan = updates.apply_licence(lic, s)
        if plan:
            out.append(("success", f"باقتك الآن «{plan}» بالمميزات التي اشتركت فيها ✔"))
        if lic.get("t") and str(lic["t"]) > (s.modules_stamp or ""):  # an older order from the channel must not undo it
            s.modules_stamp = str(lic["t"])
            s.save(update_fields=["modules_stamp"])
    if lic.get("f"):
        res = feed.check(s)
        if "✔" in res:
            out.append(("success", "اتصل الجهاز بـ Trio ✔ (ستصله التحديثات والترقيات تلقائيًا)"))
        else:
            out.append(("warning", f"الجهاز غير متصل بالإنترنت الآن — يجب أن يتصل خلال {feed.OFFLINE_DAYS} يوم"))
    return out


def poll(s=None):
    """Hourly (and from the «الدفع» page): a paid order of this copy that is not installed yet → install it."""
    from . import feed
    from .models import CompanySettings

    s = s or CompanySettings.get()
    oid = feed.state().get("pay_order")
    if not oid:
        return ""
    try:
        res = order(oid, s)
    except PayError as e:
        return str(e)
    if res.get("status") == "paid" and res.get("code"):
        feed._save(pay_order="", pay_done=oid)
        if "".join(res["code"].split()) != s.license_code:
            activate(res["code"], s)
        return "الدفع ✔ تم التفعيل"
    if res.get("status") in ("failed", "missing"):
        feed._save(pay_order="")
    return res.get("status", "")
