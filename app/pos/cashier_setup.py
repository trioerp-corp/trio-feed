"""1.26.1 a cashier PC from one file: «Trio Cashier Setup - <device>.exe» = pos/bin/TrioCashier.exe (tools/cashier, Go) + a zip
(window/ = TrioWindow + WebView2 dlls, pair.json = shop, device key, this PC's name and LAN addresses) + its length + MAGIC.

Two ways to the cashier PC: over the shop network — the owner gets a 6-digit one-time code (15 minutes, single use) and the
cashier PC opens http://<this PC>:8000/c in Edge and types it; or a download carried on a USB flash. The file is a credential
(it holds the device key): «إيقاف» of the device in the settings stops it.
"""
import io
import json
import re
import secrets
import socket
import struct
import zipfile
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse
from django.utils import timezone

MAGIC = b"TRIOCSH1"
PORT = 8000
CODE_TTL = 15 * 60
IP_FAILS, ALL_FAILS = 5, 30  # wrong codes per address / from everyone in CODE_TTL — then /c waits


class NotReady(Exception):
    """This copy has no pos/bin/TrioCashier.exe (an old package or a developer copy)."""


def base():
    return Path(settings.BASE_DIR)


def data_dir():
    return base() / "data"


def stub_path():
    """pos/bin/ in the package (pos/ is a folder every updater already copies, also a 1.26.0 one); tools/cashier in a developer copy."""
    p = base() / "pos" / "bin" / "TrioCashier.exe"
    return p if p.exists() else base() / "tools" / "cashier" / "TrioCashier.exe"


def window_dir():
    return base() / "window"


def ready():
    return stub_path().exists()


def shop_id():
    """A random id of this shop (data/shop_id.txt): the launcher answers cashier PCs' network search only for it."""
    p = data_dir() / "shop_id.txt"
    try:
        v = p.read_text().strip()
        if re.fullmatch(r"[0-9a-f]{16}", v):
            return v
    except OSError:
        pass
    v = secrets.token_hex(8)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(v)
    return v


def _private(ip):
    a = [int(x) for x in ip.split(".")]
    return a[0] == 10 or (a[0] == 172 and 16 <= a[1] <= 31) or (a[0] == 192 and a[1] == 168) or (a[0] == 169 and a[1] == 254)


def lan_ips():
    """This PC's IPv4 addresses on the shop network (private ranges, primary first). No packet leaves the PC."""
    out = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("10.255.255.255", 1))  # picks the interface of the default route; UDP connect sends nothing
            out.append(s.getsockname()[0])
        finally:
            s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            out.append(info[4][0])
    except OSError:
        pass
    seen, res = set(), []
    for ip in out:
        if ip not in seen and not ip.startswith("127.") and _private(ip):
            seen.add(ip)
            res.append(ip)
    return res[:6]


def hostname():
    return (socket.gethostname() or "").split(".")[0]


def addresses():
    """The short addresses a cashier PC types in Edge: by this PC's name and by each LAN address."""
    out = []
    if hostname():
        out.append(f"http://{hostname()}:{PORT}/c")
    out += [f"http://{ip}:{PORT}/c" for ip in lan_ips()]
    return out


def pair_info(device):
    from core.models import CompanySettings
    from core.version import VERSION

    return {"v": 1, "shop": CompanySettings.get().name, "shop_id": shop_id(), "device": device.pk, "device_name": device.name,
            "branch": device.branch.name, "token": device.token, "host": hostname(), "ips": lan_ips(), "port": PORT,
            "made": timezone.now().isoformat(timespec="seconds"), "version": VERSION}


def build(device):
    """The setup file's bytes."""
    if not ready():
        raise NotReady
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("pair.json", json.dumps(pair_info(device), ensure_ascii=False))
        w = window_dir()
        if w.is_dir():
            for p in sorted(w.rglob("*")):
                if p.is_file() and p.suffix.lower() not in (".pdb", ".xml"):
                    z.write(p, "window/" + p.relative_to(w).as_posix())
    payload = buf.getvalue()
    return stub_path().read_bytes() + payload + struct.pack("<Q", len(payload)) + MAGIC


def read_pair(data):
    """pair.json back from a setup file (tests and support)."""
    assert data[-8:] == MAGIC
    n = struct.unpack("<Q", data[-16:-8])[0]
    with zipfile.ZipFile(io.BytesIO(data[-16 - n:-16])) as z:
        return json.loads(z.read("pair.json")), z.namelist()


def filename(device):
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', " ", device.name).strip() or f"Device {device.pk}"
    return f"Trio Cashier Setup - {name}.exe"


def response(device):
    from urllib.parse import quote

    r = HttpResponse(build(device), content_type="application/octet-stream")
    r["Content-Disposition"] = f"attachment; filename=\"Trio-Cashier-Setup.exe\"; filename*=UTF-8''{quote(filename(device))}"
    r["Cache-Control"] = "no-store"
    r["X-Content-Type-Options"] = "nosniff"
    return r


# ------------------------------------------------------------------ one-time codes (download over the shop network)
def issue_code(device):
    """A new 6-digit code for this device (the previous one stops working)."""
    old = cache.get(f"cpair:dev:{device.pk}")
    if old:
        cache.delete(f"cpair:code:{old}")
    for _ in range(50):
        code = f"{secrets.randbelow(1_000_000):06d}"
        if cache.add(f"cpair:code:{code}", device.pk, CODE_TTL):
            break
    cache.set(f"cpair:dev:{device.pk}", code, CODE_TTL)
    return code


def _count(key):
    try:
        return cache.incr(key)
    except ValueError:
        cache.set(key, 1, CODE_TTL)
        return 1


def blocked(ip):
    return (cache.get(f"cpair:fail:{ip}") or 0) >= IP_FAILS or (cache.get("cpair:fail:*") or 0) >= ALL_FAILS


def redeem(code, ip):
    """The device for a valid code (the code is then used up), else None. Wrong tries are counted per address and overall."""
    from .models import Device

    if blocked(ip):
        return None
    code = re.sub(r"\D", "", str(code or ""))[:6]
    pk = cache.get(f"cpair:code:{code}") if len(code) == 6 else None
    if pk is None or not cache.add(f"cpair:used:{code}", 1, CODE_TTL):  # add = only the first request wins
        _count(f"cpair:fail:{ip}")
        _count("cpair:fail:*")
        return None
    cache.delete(f"cpair:code:{code}")
    cache.delete(f"cpair:dev:{pk}")
    return Device.objects.filter(pk=pk, active=True, branch__active=True).select_related("branch").first()


def code_view(request):
    """/c — no login: the cashier PC types the code the owner sees in «أجهزة الكاشير» and gets its setup file."""
    from django.shortcuts import render

    ip = request.META.get("REMOTE_ADDR", "")
    ctx = {"hide_nav": True, "error": "", "locked": blocked(ip)}
    if request.method == "POST" and not ctx["locked"]:
        dev = redeem(request.POST.get("code"), ip)
        if dev:
            try:
                return response(dev)
            except NotReady:
                ctx["error"] = "ملف تجهيز جهاز الكاشير غير موجود في هذه النسخة — حدّث البرنامج على جهاز المحل."
        else:
            ctx["locked"] = blocked(ip)
            ctx["error"] = "الرمز غير صحيح أو انتهت صلاحيته أو استُخدم من قبل — اطلب من صاحب المحل رمزًا جديدًا."
    return render(request, "pos/cashier_get.html", ctx, status=429 if ctx["locked"] else 200)
