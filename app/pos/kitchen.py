"""Kitchen ticket on a separate printer (1.22). The till sends the order to the program, and the program prints it on the
kitchen printer installed on the computer it runs on (Windows GDI through ctypes — no extra library; Windows shapes the
Arabic text itself). The same ticket on the cashier's own printer is printed by the till page (no server needed)."""
import ctypes
import sys
from ctypes import wintypes

IS_WIN = sys.platform == "win32"


def lines(o, cashier="", station=None, keep=None):
    """The ticket as [(text, size_pt, bold, align)] — align: "c" centre, "r" right; ("-", …) = a rule.
    station: the kitchen station's name on top (1.24); keep: indexes of the main lines this station prints (None = all)."""
    import datetime as dt

    from django.utils.dateparse import parse_datetime

    t = parse_datetime(str(o.get("created_at") or "")) or dt.datetime.now()
    from django.utils import timezone

    if timezone.is_aware(t):
        t = timezone.localtime(t)
    dl = o.get("delivery") if isinstance(o.get("delivery"), dict) else None
    where = str(o["table"])[:40] if o.get("table") else ("دليفري" if dl else ("تيك أواي" if o.get("away") else ""))  # «away»: the till has tables on
    out = [(f"طلب للمطبخ — {station}" if station else "طلب للمطبخ", 12, True, "c"), (f"رقم {o.get('shift_seq') or ''}", 26, True, "c"),
           *([(where, 18, True, "c")] if where else []),
           *([(f"العنوان: {str(dl.get('address') or '')[:120]}", 12, True, "r")] if dl and dl.get("address") else []),
           (f"{t:%H:%M} — {cashier or ''}".strip(" —"), 10, False, "c"), ("-", 0, False, "c")]
    from .restaurant import is_extra

    ls = o.get("lines") or []
    for ix, ln in enumerate(ls):
        if ln.get("of") is not None or is_extra(ln.get("item_code")) or (keep is not None and ix not in keep):
            continue
        q = ln.get("shown_qty", ln.get("qty"))
        q = int(float(q)) if float(q or 0) == int(float(q or 0)) else q
        out.append((f"{q} × {str(ln.get('name') or '')[:60]}", 14, True, "r"))
        for a in ls:
            if a.get("of") == ix:
                out.append((f"    + {str(a.get('name') or '')[:50]}", 12, False, "r"))
        if ln.get("note"):
            out.append((f"    ملاحظة: {str(ln['note'])[:120]}", 12, True, "r"))
    if o.get("note"):
        out += [("-", 0, False, "c"), (f"ملاحظة الطلب: {str(o['note'])[:200]}", 13, True, "r")]
    out.append(("-", 0, False, "c"))
    return out


def printers():
    """Installed printers (local + network connections), Windows only."""
    if not IS_WIN:
        return []
    winspool = ctypes.WinDLL("winspool.drv")
    flags, level = 2 | 4, 4  # PRINTER_ENUM_LOCAL | PRINTER_ENUM_CONNECTIONS, PRINTER_INFO_4

    class PI4(ctypes.Structure):
        _fields_ = [("pPrinterName", wintypes.LPWSTR), ("pServerName", wintypes.LPWSTR), ("Attributes", wintypes.DWORD)]

    need, n = wintypes.DWORD(), wintypes.DWORD()
    winspool.EnumPrintersW(flags, None, level, None, 0, ctypes.byref(need), ctypes.byref(n))
    if not need.value:
        return []
    buf = (ctypes.c_byte * need.value)()
    if not winspool.EnumPrintersW(flags, None, level, buf, need, ctypes.byref(need), ctypes.byref(n)):
        return []
    arr = ctypes.cast(buf, ctypes.POINTER(PI4))
    return sorted({arr[i].pPrinterName for i in range(n.value) if arr[i].pPrinterName})


class _DOCINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_int), ("lpszDocName", wintypes.LPCWSTR), ("lpszOutput", wintypes.LPCWSTR),
                ("lpszDatatype", wintypes.LPCWSTR), ("fwType", wintypes.DWORD)]


def print_lines(printer, rows, title="Trio kitchen"):
    """Prints the rows on `printer` (its name in Windows). Raises RuntimeError with a readable message."""
    if not IS_WIN:
        raise RuntimeError("الطباعة على طابعة المطبخ تعمل على ويندوز فقط")
    if not printer:
        raise RuntimeError("لم تُختر طابعة المطبخ — اخترها من الإعدادات")
    g = ctypes.WinDLL("gdi32")
    u = ctypes.WinDLL("user32")
    g.CreateDCW.restype = ctypes.c_void_p
    g.CreateDCW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p]
    g.CreateFontW.restype = ctypes.c_void_p
    g.SelectObject.restype = ctypes.c_void_p
    g.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    for f in ("StartPage", "EndPage", "EndDoc", "DeleteDC"):
        getattr(g, f).argtypes = [ctypes.c_void_p]
    g.StartDocW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_DOCINFO)]
    g.GetDeviceCaps.argtypes = [ctypes.c_void_p, ctypes.c_int]
    g.DeleteObject.argtypes = [ctypes.c_void_p]
    g.PatBlt.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
    g.SetTextColor.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    u.DrawTextW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.c_int, ctypes.POINTER(wintypes.RECT), wintypes.UINT]
    hdc = g.CreateDCW("WINSPOOL", printer, None, None)
    if not hdc:
        raise RuntimeError(f"الطابعة «{printer}» غير موجودة على هذا الجهاز")
    try:
        w, h, dpi = g.GetDeviceCaps(hdc, 8), g.GetDeviceCaps(hdc, 10), g.GetDeviceCaps(hdc, 90)  # HORZRES, VERTRES, LOGPIXELSY
        di = _DOCINFO(ctypes.sizeof(_DOCINFO), title, None, None, 0)
        if g.StartDocW(hdc, ctypes.byref(di)) <= 0:
            raise RuntimeError(f"تعذر بدء الطباعة على «{printer}»")
        g.StartPage(hdc)
        margin, y = max(4, w // 40), max(4, dpi // 10)
        DT = 0x10 | 0x800 | 0x20000  # WORDBREAK | NOPREFIX | RTLREADING
        for text, pt, bold, align in rows:
            if text == "-":
                g.PatBlt(hdc, margin, y + dpi // 30, w - 2 * margin, max(1, dpi // 90), 0x42)  # BLACKNESS
                y += dpi // 12
                continue
            font = g.CreateFontW(-int(pt * dpi / 72), 0, 0, 0, 700 if bold else 400, 0, 0, 0, 1, 0, 0, 5, 0, "Tahoma")
            old = g.SelectObject(hdc, font)
            r = wintypes.RECT(margin, y, w - margin, y + 10)
            flags = DT | (1 if align == "c" else 2)  # CENTER / RIGHT
            u.DrawTextW(hdc, text, -1, ctypes.byref(r), flags | 0x400)  # CALCRECT: the height it needs
            if r.bottom > h - margin:
                g.EndPage(hdc)
                g.StartPage(hdc)
                y = margin
                r = wintypes.RECT(margin, y, w - margin, y + (r.bottom - r.top))
            r.left, r.right = margin, w - margin
            u.DrawTextW(hdc, text, -1, ctypes.byref(r), flags)
            y = r.bottom + dpi // 40
            g.SelectObject(hdc, old)
            g.DeleteObject(font)
        g.EndPage(hdc)
        g.EndDoc(hdc)
    finally:
        g.DeleteDC(hdc)


def order_parts(o):
    """[(station or None, set of main-line indexes)] — each kitchen station gets only its lines (1.24)."""
    from .restaurant import is_extra, item_cats, route

    ls = o.get("lines") or []
    main = [ix for ix, ln in enumerate(ls) if ln.get("of") is None and not is_extra(ln.get("item_code"))]
    cats = item_cats({str(ls[ix].get("item_code")) for ix in main if not ls[ix].get("cat")})
    return [(st, set(ixs)) for st, ixs in route(main, lambda ix: ls[ix].get("cat") or cats.get(str(ls[ix].get("item_code")), ""))]


def print_order(o, cashier=""):
    from core.models import CompanySettings

    from .restaurant import printer_of

    s = CompanySettings.get()
    for st, keep in order_parts(o) or [(None, None)]:
        print_lines(printer_of(st, s), lines(o, cashier, st["name"] if st else None, keep))


def test_page():
    from core.models import CompanySettings

    s = CompanySettings.get()
    print_lines(s.kitchen_printer, [("Trio", 14, True, "c"), ("تجربة طابعة المطبخ ✔", 14, True, "c"), ("-", 0, False, "c"),
                                    ("2 × شاورما فراخ — كبير", 14, True, "r"), ("    + جبنة زيادة", 12, False, "r"), ("-", 0, False, "c")])
