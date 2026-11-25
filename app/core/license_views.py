from django.contrib import messages
from django.shortcuts import redirect, render

from . import license as LIC
from .models import CompanySettings


def _box(st, s, pay_on):
    n = LIC.renew_notice(st)
    return dict(n, pay=pay_on, wa=LIC.renew_wa(s), phone=s.phone or s.owner_whatsapp or "") if n else None


def renew_pay(request, s):
    """«جدد الاشتراك» / «اشترك»: pay the yearly subscription by card (Paymob, through Trio's Worker) when Trio turned card
    payments on (TRIOP pay=true); otherwise ask on WhatsApp. The Worker prices it (3,000 + 500 per extra branch) and a
    renewal continues from the current expiry. Works from the activation page too, so an expired copy can renew."""
    from . import pay
    from .middleware import safe_next

    back = safe_next(request, "/license/")
    if not pay.enabled():
        messages.info(request, "الدفع بالبطاقة غير متاح الآن — تواصل مع Trio للتجديد: 01228827915")
        return redirect(LIC.renew_wa(s))
    if not request.POST.get("agree"):
        messages.error(request, "أكّد أنك قرأت الشروط والأحكام وسياسة الاسترجاع ووافقت عليها قبل الدفع")
        return redirect(back)
    from .legal import TERMS_VERSION

    def num(k, top=50):
        try:
            return max(0, min(top, int(request.POST.get(k) or 0)))
        except ValueError:
            return 0

    st = LIC.status(s)
    sel = dict(renew=bool(s.license_code) and request.POST.get("what") != "extra", branches=max(num("branches"), int(st.get("branches") or 0)), visit=bool(request.POST.get("visit")), gov=str(request.POST.get("gov") or "")[:4],
               custom=bool(request.POST.get("custom")), phone=(request.POST.get("phone") or s.phone or s.owner_whatsapp or "").strip(), agree=TERMS_VERSION)
    try:
        res = pay.checkout(sel, s, request.build_absolute_uri("/ui/shop/pay/"))
    except pay.PayError as e:
        messages.error(request, str(e))
        return redirect(back)
    return redirect(res["url"])


def page(request):
    s = CompanySettings.get()
    if request.method == "POST" and request.POST.get("action") == "renew":
        return renew_pay(request, s)
    if request.method == "GET":
        from . import feed, pay

        if feed.state().get("pay_order"):  # back from Paymob while the copy is stopped: install the paid licence here
            try:
                if "تم التفعيل" in (pay.poll(s) or ""):
                    messages.success(request, "تم الدفع ✔ — تم تجديد الاشتراك")
                    return redirect("/")
            except Exception:  # noqa: BLE001
                pass
    if request.method == "POST" and request.POST.get("action") == "check":
        from . import feed

        res = feed.check(s)
        (messages.success if "✔" in res else messages.error)(request, res if "✔" in res else "مقدرتش أوصل لـ Trio — تأكد من اتصال الجهاز بالإنترنت وحاول مرة أخرى")
        return redirect("/license/")
    if request.method == "POST" and request.POST.get("action") == "claim":
        from . import pay

        try:
            code = pay.claim(request.POST.get("order", ""), request.POST.get("phone", ""), s)
            for lvl, msg in pay.activate(code, s):
                getattr(messages, lvl)(request, msg)
            return redirect("/")
        except (pay.PayError, ValueError) as e:
            messages.error(request, str(e))
    elif request.method == "POST":
        from . import pay

        try:
            for lvl, msg in pay.activate(request.POST.get("code", ""), s):
                getattr(messages, lvl)(request, msg)
            return redirect("/")
        except ValueError as e:
            messages.error(request, str(e))
    from . import feed
    import datetime as dt

    ok = feed.state().get("ok")
    from urllib.parse import quote

    wa = "https://wa.me/201228827915?text=" + quote(f"أرغب في الاشتراك في Trio\nالمحل: {s.name}\nكود الجهاز: {LIC.machine_code()}")
    from . import pay
    from .modules import shop_prices

    st = LIC.status(s)
    return render(request, "core/license.html", dict(st=st, renew=_box(st, s, pay.enabled()), pay_on=pay.enabled(), **shop_prices(s, st.get('branches') or 0),
                                                     renew_wa=LIC.renew_wa(s), support=LIC.SUPPORT, hide_nav=True, feed_url=feed.feed_url(s), wa=wa,
                                                     feed_ok=dt.datetime.fromisoformat(ok) if ok else None))
