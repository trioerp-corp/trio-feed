"""CI helper (stdlib only): the owner signs in, asks for a network code, a «cashier PC» opens /c and downloads its file.
    python cashier_ci.py <base url> <user> <password> <out .exe>   → prints the code; exit 1 on any failure"""
import http.cookiejar, re, sys, urllib.parse, urllib.request

base, user, pw, out = sys.argv[1:5]


def opener():
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()), urllib.request.ProxyHandler({}))


def csrf(o, path):
    html = o.open(base + path, timeout=60).read().decode("utf-8", "replace")
    return re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', html).group(1)


def post(o, path, data, ref):
    req = urllib.request.Request(base + path, urllib.parse.urlencode(data).encode(), headers={"Referer": base + ref})
    return o.open(req, timeout=120)


owner = opener()
post(owner, "/login/", {"csrfmiddlewaretoken": csrf(owner, "/login/"), "username": user, "password": pw, "next": "/"}, "/login/")
post(owner, "/ui/shop/settings/", {"csrfmiddlewaretoken": csrf(owner, "/ui/shop/settings/"), "action": "pair_code", "id": "new", "name": "CI كاشير"}, "/ui/shop/settings/")
page = owner.open(base + "/ui/shop/settings/", timeout=60).read().decode("utf-8")
m = re.search(r'letter-spacing:6px">(\d{6})<', page)
if not m:
    print("no code on the settings page"); sys.exit(1)
code = m.group(1)
addrs = re.findall(r'<code dir="ltr"[^>]*>(http://[^<]+/c)</code>', page)
print("code", code, "addresses", addrs)
lan = opener()  # the cashier PC: no login
r = post(lan, "/c/", {"csrfmiddlewaretoken": csrf(lan, "/c/"), "code": code}, "/c/")
data = r.read()
print("download", r.status, r.headers.get("Content-Disposition"), len(data))
if not (data[:2] == b"MZ" and data[-8:] == b"TRIOCSH1"):
    print("not a setup file"); sys.exit(1)
open(out, "wb").write(data)
again = post(lan, "/c/", {"csrfmiddlewaretoken": csrf(lan, "/c/"), "code": code}, "/c/").read()
if again[-8:] == b"TRIOCSH1":
    print("FAIL: the code worked twice"); sys.exit(1)
print("second use refused: ok")
