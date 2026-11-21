"""Licence for the shop edition (Trio).

- Every computer has a «كود الجهاز» derived from Windows' own machine id (it does not change with the program).
- Trio issues an «كود التفعيل» with Trio-License.exe: client name + machine code + expiry date, signed with Trio's
  private key. The program only holds the public key below, so a code cannot be forged or moved to another computer.
- No code yet: 14-day trial. 10 days before expiry a banner warns; after expiry a grace period (7 days by default) keeps
  everything working with a red banner; after that the program opens only the activation page until a new code is entered.
"""
import base64
import datetime as dt
import hashlib
import json
import platform
import uuid
from functools import lru_cache

PUBLIC_KEY = "tM+bQlw/40oF17B+DvmHJ1TxQd1oIs0oMTOJ3LxDs4c="
TRIAL_DAYS, WARN_DAYS = 14, 10
SUPPORT = "Trio · 01228827915 · 01121460640"


def _b64d(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# Codes Trio's phone app may sign (1.16): a «phone key» that Trio's own key certified (TRIOK use=admin) signs them, and the
# certificate rides at the end of the code: TAG.payload.sig.cert-payload.cert-sig. Never updates (TRIOUP) or key
# certificates / revocations (TRIOK) — those stay with Trio's own key on its computer.
ADMIN_TAGS = {"TRIO1", "TRIOM", "TRIOC", "TRIOD", "TRIOP"}


def _revoked_at(k):
    try:
        from .feed import state

        return state().get("revoked", {}).get(k)
    except Exception:  # noqa: BLE001
        return None


def signed(code, tag):
    """The payload of a code signed by Trio (its own key, or a certified phone key for ADMIN_TAGS). ValueError otherwise.
    A phone-signed payload carries «_k» = the phone key; a stopped phone key (TRIOK r=true) signs nothing after that moment."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    code = "".join(str(code).split())
    try:
        parts = code.split(".")
        assert parts[0] == tag
        main = Ed25519PublicKey.from_public_bytes(base64.b64decode(PUBLIC_KEY))
        if len(parts) == 3:
            main.verify(_b64d(parts[2]), parts[1].encode())
            return json.loads(_b64d(parts[1]).decode())
        assert len(parts) == 5 and (tag in ADMIN_TAGS or tag == "TRIOUP")
        _, payload, sig, kpayload, ksig = parts
        main.verify(_b64d(ksig), kpayload.encode())
        cert = json.loads(_b64d(kpayload).decode())
        # the phone's admin key signs licences / orders; a release (TRIOUP) needs a key certified for updates only
        assert cert.get("use") == ("update" if tag == "TRIOUP" else "admin") and cert.get("k")
        Ed25519PublicKey.from_public_bytes(base64.b64decode(cert["k"])).verify(_b64d(sig), payload.encode())
        d = json.loads(_b64d(payload).decode())
        r = _revoked_at(cert["k"])
        # a stopped admin key keeps what it signed before; a stopped update key kills its releases too (they wait 2 hours for this)
        stamp = d.get("at") if tag == "TRIOD" else d.get("t")  # a developer code's «t» is its type («dev»)
        assert not r or (tag != "TRIOUP" and stamp and str(stamp) < str(r))
        d["_k"] = cert["k"]
        return d
    except (ValueError, AssertionError, InvalidSignature, KeyError, TypeError, IndexError):
        raise ValueError("غير موقّع من Trio")


@lru_cache(maxsize=1)
def machine_code():
    raw = ""
    if platform.system() == "Windows":
        try:
            import winreg

            k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY)
            raw = winreg.QueryValueEx(k, "MachineGuid")[0]
        except OSError:
            raw = ""
    if not raw:
        for p in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
            try:
                raw = open(p).read().strip()
                break
            except OSError:
                pass
    raw = raw or str(uuid.getnode())
    h = base64.b32encode(hashlib.sha256(("acc-shop:" + raw).encode()).digest()).decode()[:16]
    return "-".join(h[i:i + 4] for i in range(0, 16, 4))


@lru_cache(maxsize=32)
def parse(code):
    """Returns the licence dict, or raises ValueError with an Arabic reason."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    code = "".join(str(code).split())
    try:
        parts = code.split(".")
        if parts[0] == "TRIOS":  # bought online: signed by a sales key that Trio's own key certified (TRIOK)
            tag, payload, sig, kpayload, ksig = parts
            main = Ed25519PublicKey.from_public_bytes(base64.b64decode(PUBLIC_KEY))
            main.verify(_b64d(ksig), kpayload.encode())
            cert = json.loads(_b64d(kpayload).decode())
            assert cert.get("use") == "sales" and cert.get("k")
            Ed25519PublicKey.from_public_bytes(base64.b64decode(cert["k"])).verify(_b64d(sig), payload.encode())
            lic = json.loads(_b64d(payload).decode())
            lic["_k"] = cert["k"]
            return lic
        lic = signed(code, "TRIO1")
    except (ValueError, AssertionError, InvalidSignature, KeyError, TypeError):
        raise ValueError("كود التفعيل غير صحيح أو ناقص — انسخه كاملًا كما أُرسل إليك")
    return lic


def revoked(lic):
    """A licence from a sales key Trio stopped (TRIOK r=true in the command channel) after that moment."""
    if not lic.get("_k"):
        return False
    try:
        from .feed import state

        r = state().get("revoked", {}).get(lic["_k"])
    except Exception:  # noqa: BLE001
        return False
    return bool(r) and str(lic.get("t", "")) >= str(r)


def status(settings_obj=None, today=None):
    from core.models import CompanySettings

    s = settings_obj or CompanySettings.get()
    if today is None:
        # the latest day this copy has seen: turning the computer's clock back never extends a trial or an expired licence
        # (and a dead clock battery that resets the date does not confuse the days either)
        today = dt.date.today()
        if s.clock_day and today < s.clock_day:
            today = s.clock_day
        elif s.clock_day != today and s.pk:
            CompanySettings.objects.filter(pk=s.pk).update(clock_day=today)
            s.clock_day = today
    mc = machine_code()
    if not s.license_code:
        from . import trialmark

        start = trialmark.earliest(s.trial_started or today)
        if s.trial_started != start and s.pk:
            s.trial_started = start
            s.save(update_fields=["trial_started"])
        s.trial_started = start
        end = s.trial_started + dt.timedelta(days=TRIAL_DAYS + (s.trial_extra or 0))
        left = (end - today).days
        return dict(state="trial" if left >= 0 else "expired", days_left=left, expires=end, client="", machine=mc,
                    message=f"نسخة تجريبية — متبقٍ {left} يوم" if left >= 0 else "الفترة التجريبية خلصت")
    try:
        lic = parse(s.license_code)
    except ValueError as e:
        return dict(state="invalid", days_left=-1, expires=None, client="", machine=mc, message=str(e))
    if revoked(lic):
        return dict(state="invalid", days_left=-1, expires=None, client=lic.get("c", ""), machine=mc,
                    message="تم إلغاء كود التفعيل هذا — تواصل مع Trio")
    if lic.get("m") != mc:
        return dict(state="invalid", days_left=-1, expires=None, client=lic.get("c", ""), machine=mc,
                    message="كود التفعيل هذا صادر لجهاز آخر — أرسل كود هذا الجهاز إلى Trio")
    extra = dict(client=lic.get("c", ""), plan=lic.get("p", ""), machine=mc, lifetime=bool(lic.get("l")), lifetime_updates=bool(lic.get("u")))
    if lic.get("l"):  # bought for life: never expires
        return _connected(dict(state="active", days_left=99999, expires=None, message="رخصة مدى الحياة ✔" + (" — والتحديثات مدى الحياة" if lic.get("u") else ""), **extra), s, today)
    exp = dt.date.fromisoformat(lic["e"])
    left = (exp - today).days
    grace = int(lic.get("g", 7))
    if left >= 0:
        st = "warning" if left <= WARN_DAYS else "active"
        msg = f"ينتهي الاشتراك بعد {left} يوم" if st == "warning" else f"مفعّل حتى {exp:%d/%m/%Y}"
    elif -left <= grace:
        st, msg = "grace", f"الاشتراك انتهى من {-left} يومًا — سيتوقف البرنامج بعد {grace + left} يوم"
    else:
        st, msg = "expired", "الاشتراك انتهى"
    if lic.get("u") and st == "active":
        msg += " — والتحديثات مدى الحياة"
    return _connected(dict(state=st, days_left=left, expires=exp, message=msg, **extra), s, today)


def _connected(st, s, today=None):
    """A licence with Trio's command channel must reach it now and then (forced stop, renewals, updates)."""
    if st["state"] not in ("active", "warning", "grace"):
        return st
    try:
        from .feed import offline

        off = offline(s, today)
    except Exception:  # noqa: BLE001
        return st
    if off is None:
        return st
    if off["blocked"]:
        return dict(st, state="offline", message=f"الجهاز غير متصل بالإنترنت منذ {off['days']} يومًا — صِله بالإنترنت دقيقة واحدة وسيعود البرنامج للعمل تلقائيًا")
    return dict(st, state="warning" if st["state"] == "active" else st["state"],
                message=f"الجهاز غير متصل بالإنترنت منذ {off['days']} يومًا — صِله بالإنترنت خلال {off['left']} يومًا حتى لا يتوقف البرنامج")


def blocked(st):
    return st["state"] in ("expired", "invalid", "offline")
