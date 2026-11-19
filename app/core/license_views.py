from django.contrib import messages
from django.shortcuts import redirect, render

from . import license as LIC
from .models import CompanySettings


def page(request):
    s = CompanySettings.get()
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

    wa = "https://wa.me/201008030459?text=" + quote(f"أرغب في الاشتراك في Trio\nالمحل: {s.name}\nكود الجهاز: {LIC.machine_code()}")
    return render(request, "core/license.html", dict(st=LIC.status(s), support=LIC.SUPPORT, hide_nav=True, feed_url=feed.feed_url(s), wa=wa,
                                                     feed_ok=dt.datetime.fromisoformat(ok) if ok else None))
