"""Slows password guessing: after a few wrong tries the name (and the device guessing) waits some minutes. Kept in memory —
the program is one process — so a restart clears it, and it needs no table."""
from django.core.cache import cache

TRIES, WINDOW, IP_TRIES = 8, 600, 40  # wrong passwords per name / per address in 10 minutes


def _keys(name, ip):
    return [k for k in (f"thr:u:{(name or '').strip().lower()[:150]}" if name else "", f"thr:i:{ip}" if ip else "") if k]


def blocked(name, ip=""):
    ks = _keys(name, ip)
    return (bool(name) and (cache.get(ks[0]) or 0) >= TRIES) or (bool(ip) and (cache.get(f"thr:i:{ip}") or 0) >= IP_TRIES)


def fail(name, ip=""):
    for k in _keys(name, ip):
        try:
            cache.incr(k)
        except ValueError:
            cache.set(k, 1, WINDOW)


def clear(name):
    if name:
        cache.delete(f"thr:u:{name.strip().lower()[:150]}")


MSG = "محاولات خاطئة كثيرة — انتظر 10 دقائق وحاول مرة أخرى، أو اطلب من صاحب المحل إعادة تعيين كلمة السر من الإعدادات."


def login_view():
    from django.contrib.auth.views import LoginView

    class ThrottledLogin(LoginView):
        template_name = "registration/login.html"

        def _who(self):
            return self.request.POST.get("username", ""), self.request.META.get("REMOTE_ADDR", "")

        def post(self, request, *a, **k):
            if blocked(*self._who()):
                return self.render_to_response(self.get_context_data(form=self.get_form_class()(request), locked=MSG), status=429)
            return super().post(request, *a, **k)

        def form_invalid(self, form):
            fail(*self._who())
            return super().form_invalid(form)

        def form_valid(self, form):
            clear(self._who()[0])
            return super().form_valid(form)

    return ThrottledLogin.as_view()
