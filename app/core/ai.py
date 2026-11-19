"""The ACC's AI (Claude, through Trio's Worker — the API key never reaches the shop):
  profile(text)  an activity that is not in the library → a profile (checked again by activities.clean)
  ask(q, ctx)    the «؟ اسأل» assistant: how to do something in the program / where it is
No internet, or Trio did not turn it on → None, and the program falls back (library / local help)."""
import json
import urllib.request

from . import license as LIC


def _call(path, body, timeout=40):
    from .pay import api_url

    base = api_url(refresh=False)
    if not base:
        return None
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "User-Agent": "Trio"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read(1 << 20).decode() or "{}")
    except Exception:  # noqa: BLE001
        return None


def enabled():
    from . import feed

    return bool(feed.state().get("api_ai"))


def profile(text):
    res = _call("/profile", dict(text=str(text)[:200], m=LIC.machine_code()))
    return res.get("profile") if isinstance(res, dict) and isinstance(res.get("profile"), dict) else None


def ask(q, ctx):
    res = _call("/ask", dict(q=str(q)[:500], m=LIC.machine_code(), **ctx), timeout=45)
    if not isinstance(res, dict):
        return None
    if res.get("error"):
        return dict(error=str(res["error"])[:300])
    return dict(answer=str(res.get("answer", ""))[:3000], links=[l for l in res.get("links", []) if isinstance(l, str)][:4], left=res.get("left"))
