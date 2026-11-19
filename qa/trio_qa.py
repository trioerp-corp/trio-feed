"""Trio QA — a «virtual user» that sets the program up from zero and uses it like a shop owner and a cashier, many times,
for every business type, and writes down everything that goes wrong or feels rough.

Each run = a brand-new copy (empty database in a temp folder, its own port):
  1. the real first-run wizard («جرّب 14 يومًا») with an activity picked for the run (supermarket, café, restaurant,
     PlayStation hall, clinic, pharmacy, clothes, spare parts…), random answers;
  2. every page of the side menu as the owner, at a desktop or a phone size: server errors, JS errors, pages that are slow,
     pages wider than the screen, «None / undefined / NaN» on the screen, colloquial words left, broken images;
  3. every form on those pages sent empty and with made-up values (never a 500);
  4. the till: open the shift, sell with cash / card / credit, a return, a sale while «offline», close the shift; the café
     tables (send to kitchen twice, pay) ; the PlayStation screen (start, order, pay);
  5. the books must agree (every journal entry balanced, stock value = the stock account).

Works on Windows (the real installed program: --app "C:\\Trio") and on Linux (the source folder).
  python trio_qa.py --app C:\\Trio --runs 60 --out C:\\TrioQA
Needs: pip install playwright && python -m playwright install chromium   (run_qa.bat does it).
The report: <out>\\report.html (+ report.json for an AI agent, + screenshots).
"""
import argparse
import contextlib
import html
import json
import os
import random
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import traceback
from collections import defaultdict
from pathlib import Path

ACTIVITIES = [  # (what the owner types, the business type we expect, short key)
    ("سوبر ماركت", "market", "market"), ("بقالة وتموين", "market", "market"), ("صيدلية", "retail", "pharmacy"),
    ("محل ملابس حريمي", "retail", "retail"), ("محل قطع غيار سيارات", "retail", "retail"), ("محل موبايلات واكسسوارات", "retail", "retail"),
    ("كافيه", "cafe", "cafe"), ("مطعم مشويات", "cafe", "cafe"), ("محل عصير", "cafe", "cafe"),
    ("صالة بلايستيشن", "gaming", "gaming"), ("بلياردو وبلايستيشن", "gaming", "gaming"),
    ("عيادة أسنان", "clinic", "clinic"), ("مركز طبي", "clinic", "clinic"),
]
COLLOQUIAL = re.compile(r"(?<![\w])(دلوقتي|دلوقت|عشان|علشان|إزاي|ازاي|إيه|ايه|بتاع|بتاعك|هيبقى|هتبقى|اللي|مفيش|ماتقدرش|مش)(?![\w])")
GARBAGE = re.compile(r"(?<![\w])(None|undefined|NaN|null|\[object Object\]|Traceback|TemplateSyntaxError)(?![\w])")
SKIP_FORM = re.compile(r"logout|restore|/trio/|backup|zero|reset|guide|/pos/|delete|export|download|\.pdf|lang", re.I)
SLOW = 4.0
PASSWORD = "Qa-Pass-2026!"


def free_port():
    with contextlib.closing(socket.socket()) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Issues:
    def __init__(self, out):
        self.out, self.items, self.n = out, {}, 0

    def add(self, kind, where, msg, run, page=None, sev="medium"):
        key = (kind, re.sub(r"\d{3,}", "#", where or ""), msg[:300])
        it = self.items.get(key)
        if not it:
            shot = ""
            if page is not None:
                self.n += 1
                shot = f"shots/issue-{self.n}.jpg"
                with contextlib.suppress(Exception):
                    page.screenshot(path=str(self.out / shot), full_page=False, type="jpeg", quality=55)
            it = self.items[key] = dict(kind=kind, where=where, msg=msg[:2000], sev=sev, runs=[], shot=shot)
        if run not in it["runs"]:
            it["runs"].append(run)
        if sev != "low":
            print(f"   ⚠ [{sev}] {kind} @ {where}: {msg[:160]}")


class Server:
    """A fresh copy of the program on its own database and port."""

    def __init__(self, app, py, tmp, port):
        self.app, self.py, self.tmp, self.port = app, py, tmp, port
        self.env = dict(os.environ, GG_SQLITE=str(tmp / "db.sqlite3"), GG_DEBUG="0", GG_SECRET_KEY="qa" * 30, DJANGO_SETTINGS_MODULE="config.settings",
                        PYTHONIOENCODING="utf-8", PYTHONUTF8="1", GG_ALLOWED_HOSTS="*", TRIO_TRIAL_MARK=str(tmp / "trial.json"), TRIO_QA="1")
        self.log = open(tmp / "server.log", "w", encoding="utf-8")
        self.proc = None

    def start(self):
        r = subprocess.run([self.py, "manage.py", "migrate", "--noinput"], cwd=self.app, env=self.env, capture_output=True, text=True, encoding="utf-8")
        if r.returncode:
            raise RuntimeError("migrate failed: " + (r.stderr or r.stdout)[-1500:])
        self.proc = subprocess.Popen([self.py, "-m", "waitress", f"--listen=127.0.0.1:{self.port}", "--threads=8", "config.wsgi:application"],
                                     cwd=self.app, env=self.env, stdout=self.log, stderr=subprocess.STDOUT)
        for _ in range(120):
            with contextlib.suppress(OSError), socket.create_connection(("127.0.0.1", self.port), 0.5):
                return
            time.sleep(0.25)
        raise RuntimeError("the server did not start")

    def stop(self):
        if self.proc:
            self.proc.terminate()
            with contextlib.suppress(Exception):
                self.proc.wait(10)
        self.log.close()

    def db(self):
        return sqlite3.connect(self.env["GG_SQLITE"])


class Run:
    def __init__(self, n, args, issues, browser):
        self.n, self.args, self.I, self.browser = n, args, issues, browser
        self.rnd = random.Random(args.seed * 1000 + n)
        pool = [a for a in ACTIVITIES if not args.only or a[2] == args.only or a[1] == args.only]
        self.act = pool[(n - 1) % len(pool)] if not args.only else self.rnd.choice(pool)
        self.phone = n % 3 == 0  # every third run on a phone-size screen
        self.base = ""
        self.steps = []
        self.times = {}

    # ----------------------------------------------------------------- plumbing
    def watch(self, page, label):
        def on_console(m):
            if m.type == "error" and "favicon" not in m.text and "net::ERR" not in m.text and "Failed to load resource" not in m.text:
                self.I.add("js-console", self.path(page), m.text, self.n, None, "low")

        page.on("pageerror", lambda e: self.I.add("js-error", self.path(page), str(e), self.n, page, "high"))
        page.on("console", on_console)
        page.on("response", lambda r: r.status >= 500 and r.url.startswith(self.base) and self.I.add("server-500", r.url.replace(self.base, ""), f"HTTP {r.status}", self.n, page, "high"))
        page.on("dialog", lambda d: (self.steps.append(f"dialog: {d.message[:120]}"), d.accept(PASSWORD if d.type == "prompt" else None) if d.type == "prompt" else d.accept()))

    def path(self, page):
        with contextlib.suppress(Exception):
            return page.url.replace(self.base, "") or "/"
        return "?"

    def step(self, name, fn, *a):
        t = time.time()
        try:
            fn(*a)
            self.steps.append(f"✔ {name} ({time.time() - t:.1f}s)")
            return True
        except Exception as e:  # noqa: BLE001 — a failed flow is a finding, not the end of the run
            self.steps.append(f"✖ {name}: {e}")
            self.I.add("flow", name, f"{type(e).__name__}: {str(e)[:400]}", self.n, self.page, "high")
            if self.args.verbose:
                traceback.print_exc()
            return False

    def page_checks(self, page, where, slow_ok=False):
        txt = page.inner_text("body") if page.url.startswith(self.base) else ""
        if "حدث خطأ" in txt and "الخادم" in txt or "Server Error" in txt:
            self.I.add("error-page", where, txt[:300], self.n, page, "high")
        for m in set(GARBAGE.findall(txt)):
            self.I.add("garbage-text", where, f"«{m}» ظاهرة على الشاشة", self.n, page, "medium")
        for m in set(COLLOQUIAL.findall(txt)):
            self.I.add("colloquial", where, f"كلمة عامية: «{m}»", self.n, None, "low")
        wide = page.evaluate("()=>{const e=document.scrollingElement;return e.scrollWidth-window.innerWidth}")
        if wide > 4:
            self.I.add("layout-overflow", where, f"الصفحة أعرض من الشاشة بـ {wide}px ({'هاتف' if self.phone else 'كمبيوتر'})", self.n, page, "medium")
        broken = page.evaluate("()=>[...document.images].filter(i=>i.complete&&i.naturalWidth===0&&i.src).map(i=>i.src).slice(0,3)")
        if broken:
            self.I.add("broken-image", where, ", ".join(broken), self.n, page, "low")
        empty = page.evaluate("()=>[...document.querySelectorAll('button,a.btn')].filter(b=>b.offsetParent&&!b.textContent.trim()&&!b.getAttribute('aria-label')&&!b.title).map(b=>b.outerHTML.slice(0,140))")
        if empty:
            self.I.add("unlabeled-button", where, f"{len(empty)} زر بدون نص أو وصف: {empty[0]}", self.n, None, "low")

    def goto(self, page, url, where=None):
        t = time.time()
        r = page.goto(self.base + url, wait_until="load", timeout=45000)
        dt = time.time() - t
        where = where or url
        self.times[where] = max(self.times.get(where, 0), dt)
        if dt > SLOW:
            self.I.add("slow-page", where, f"فتحت في {dt:.1f} ثانية", self.n, None, "medium")
        if r and r.status >= 400 and r.status != 404:
            self.I.add("http-error", where, f"HTTP {r.status}", self.n, page, "high" if r.status >= 500 else "medium")
        self.page_checks(page, where)
        return r

    # ----------------------------------------------------------------- 1. first run
    def wizard(self):
        p = self.page
        self.goto(p, "/", "/ (أول تشغيل)")
        if not p.is_visible("#wzForm"):
            with contextlib.suppress(Exception):
                p.click("#demo summary")
        p.fill("#actText", self.act[0])
        p.wait_for_function("()=>window.ACC_ACT", timeout=15000)
        got = p.evaluate("()=>window.ACC_ACT.base")
        if got != self.act[1]:
            self.I.add("activity-match", f"نشاط «{self.act[0]}»", f"تم اختيار النوع «{got}» والمتوقع «{self.act[1]}»", self.n, p, "medium")
        p.click("#wzNext")
        p.fill("[name=shop]", self.rnd.choice(["الأمير", "النور", "بيت الكرم", "Golden", "الحاج محمود وأولاده"]) + f" {self.n}")
        p.fill("[name=phone]", f"010{self.rnd.randint(10000000, 99999999)}")
        if self.rnd.random() < 0.5:
            p.fill("[name=address]", "شارع الجمهورية — المنصورة")
        p.click("#wzNext")
        p.select_option("[name=devices]", str(self.rnd.choice([1, 1, 2, 3])))
        if self.rnd.random() < 0.3:
            p.check("[name=multi][value='1']")
        if self.rnd.random() < 0.2:
            p.check("[name=samples][value='0']")
        p.click("#wzNext")
        p.fill("[name=owner]", "مالك تجريبي")
        p.fill("[name=username]", "owner")
        p.fill("[name=password]", PASSWORD)
        p.click("#wzNext")
        t = time.time()
        p.click("#wzGo")
        p.wait_for_load_state("load", timeout=90000)
        self.times["setup"] = time.time() - t
        if time.time() - t > 15:
            self.I.add("slow-setup", "تجهيز النسخة", f"التجهيز أخذ {time.time() - t:.0f} ثانية", self.n, None, "medium")
        self.page_checks(p, "بعد التجهيز")
        if "/ui" not in p.url and "/accounts" not in p.url:
            raise RuntimeError(f"after the wizard the page is {self.path(p)}")
        if "/accounts/login" in p.url:
            p.fill("[name=username]", "owner")
            p.fill("[name=password]", PASSWORD)
            p.click("button[type=submit]")
            p.wait_for_load_state("load")

    # ----------------------------------------------------------------- 2. every page
    def crawl(self):
        p = self.page
        self.goto(p, "/ui/", "الرئيسية")
        links = p.evaluate("()=>[...document.querySelectorAll('aside a[href^=\"/ui\"], nav a[href^=\"/ui\"], .side a[href^=\"/ui\"], a.tile[href^=\"/ui\"]')].map(a=>a.getAttribute('href'))")
        links = sorted({x.split("#")[0] for x in links if x and not SKIP_FORM.search(x)})
        if not links:
            links = ["/ui/"]
        self.links = links
        self.steps.append(f"pages in the menu: {len(links)}")
        for url in links:
            try:
                self.goto(p, url)
                if self.args.shots and self.n <= 2:
                    name = re.sub(r"[^a-z0-9]+", "_", url.strip("/").lower()) or "home"
                    p.screenshot(path=str(self.I.out / "pages" / f"{self.act[2]}-{'m' if self.phone else 'd'}-{name}.png"), full_page=True)
            except Exception as e:  # noqa: BLE001
                self.I.add("page-load", url, str(e)[:300], self.n, p, "high")

    # ----------------------------------------------------------------- 3. forms
    def forms(self):
        p = self.page
        sample = self.rnd.sample(self.links, min(len(self.links), self.args.forms))
        for url in sample:
            try:
                self.goto(p, url)
                n = p.evaluate("()=>[...document.forms].filter(f=>(f.method||'').toLowerCase()==='post'&&f.offsetParent).length")
                for k in range(min(n, 3)):
                    for mode in ("empty", "fill"):
                        self.goto(p, url)
                        fs = p.query_selector_all("form[method=post], form[method=POST]")
                        fs = [f for f in fs if f.is_visible()]
                        if k >= len(fs):
                            break
                        f = fs[k]
                        act = f.get_attribute("action") or url
                        if SKIP_FORM.search(act) or f.query_selector("input[type=file][required]"):
                            continue
                        if mode == "fill":
                            self.fill_form(f)
                        else:
                            p.evaluate("f=>{f.noValidate=true;f.querySelectorAll('input:not([type=hidden]):not([type=checkbox]):not([type=radio]),textarea').forEach(i=>i.value='')}", f)
                        btn = f.query_selector("button:not([type=button]), input[type=submit]")
                        if not btn or not btn.is_visible():
                            continue
                        with contextlib.suppress(Exception), p.expect_navigation(timeout=20000):
                            btn.click(timeout=5000)
                        self.page_checks(p, f"{url} ← نموذج {k + 1} ({'فارغ' if mode == 'empty' else 'بقيم'})")
            except Exception as e:  # noqa: BLE001
                self.I.add("form", url, str(e)[:300], self.n, p, "low")

    def fill_form(self, f):
        r = self.rnd
        for el in f.query_selector_all("input:not([type=hidden]):not([readonly]), textarea, select"):
            if not el.is_visible():
                continue
            t = (el.get_attribute("type") or "text").lower()
            name = (el.get_attribute("name") or "").lower()
            tag = el.evaluate("e=>e.tagName").lower()
            try:
                if tag == "select":
                    opts = [o for o in el.evaluate("e=>[...e.options].map(o=>o.value)") if o]
                    if opts:
                        el.select_option(r.choice(opts))
                elif t in ("checkbox", "radio"):
                    if r.random() < 0.5:
                        el.check()
                elif t == "number" or re.search(r"price|qty|cost|amount|rate|count|seats|discount|limit|float|المبلغ", name) or el.get_attribute("inputmode") in ("decimal", "numeric"):
                    el.fill(r.choice(["10", "2.5", "0", "-5", "100000", "1,5", "abc", "٣٠"]))
                elif t == "date":
                    el.fill(r.choice(["2026-11-19", "2026-01-01", "1999-12-31"]))
                elif t == "email" or "mail" in name:
                    el.fill(r.choice(["a@b.co", "not-an-email"]))
                elif t == "password":
                    el.fill(PASSWORD)
                elif t in ("file", "color", "range"):
                    continue
                else:
                    el.fill(r.choice(["اختبار", "صنف تجريبي " + str(r.randint(1, 999)), "<b>x</b>", "' OR 1=1 --", "أ" * 300, "Test 123", "  "]))
            except Exception:  # noqa: BLE001
                pass

    # ----------------------------------------------------------------- 3b. the owner adds his own products by hand
    def products(self):
        p, r = self.page, self.rnd
        if self.act[1] == "cafe":
            for k in range(3):
                self.goto(p, "/ui/shop/menu/", "المنيو")
                f = p.locator("form").filter(has=p.locator("[name=has_sizes]")).first
                f.locator("[name=name]").fill(r.choice(["شاورما", "برجر", "كشري", "وافل", "سحلب"]) + f" {k + 1}")
                f.locator("[name=new_category]").fill(r.choice(["ساندويتشات", "حلويات", "وجبات"]))
                if k == 0:
                    f.locator("[name=has_sizes]").check()
                    f.locator("[name=sprice_0]").fill("45")
                    f.locator("[name=sprice_2]").fill("70")
                else:
                    f.locator("[name=price]").fill(str(r.randint(20, 120)))
                f.locator("button").first.click()
                p.wait_for_load_state("load")
                self.page_checks(p, "المنيو ← إضافة منتج")
            return
        for k in range(3):
            self.goto(p, "/ui/shop/items/", "الأصناف")
            d = p.locator("details.card").first
            if not d.get_attribute("open"):
                d.locator("summary").click()
            f = d.locator("form")
            f.locator("[name=name]").fill(r.choice(["منتج", "صنف", "سلعة"]) + f" يدوي {k + 1}")
            if f.locator("[name=barcode]").count():
                f.locator("[name=barcode]").fill(str(r.randint(10 ** 11, 10 ** 12)))
            if f.locator("[name=price]").count():
                f.locator("[name=price]").fill(str(r.randint(5, 500)))
            f.locator("[name=reorder]").fill(r.choice(["0", "5"]))
            f.locator("button[name=new]").click()
            p.wait_for_load_state("load")
            self.page_checks(p, "الأصناف ← إضافة صنف")

    # ----------------------------------------------------------------- 4. the till
    def device_token(self):
        c = self.srv.db()
        try:
            return c.execute("SELECT token FROM pos_device ORDER BY id LIMIT 1").fetchone()[0]
        finally:
            c.close()

    def till(self):
        p = self.page
        self.goto(p, "/pos/", "الكاشير")
        if p.is_visible("#tok"):
            p.fill("#tok", self.device_token())
            p.click("#saveTok")
        p.wait_for_timeout(2500)
        self.page_checks(p, "الكاشير")
        if p.is_visible("#tview") and p.is_visible("#takeaway"):
            p.click("#takeaway")
        items = p.locator("#items .it").filter(has_not_text="بدون كود")
        if items.count() == 0:
            self.I.add("till-empty", "الكاشير", "لا توجد أصناف في الكاشير بعد التجهيز (لا يمكن البيع فورًا)", self.n, p, "medium")
            return
        self.open_shift()
        for k in range(self.rnd.randint(3, 8)):
            self.sale(k)
        if self.rnd.random() < 0.5:
            p.context.set_offline(True)
            self.sale(99, offline=True)
            p.context.set_offline(False)
            p.wait_for_timeout(1500)
        p.wait_for_timeout(9000)  # the till syncs
        if p.is_visible("#queue") and re.search(r"[1-9]", p.inner_text("#queue")):
            self.I.add("sync", "الكاشير", f"طلبات لم تُرسل بعد 9 ثوانٍ: {p.inner_text('#queue')}", self.n, p, "medium")

    def open_shift(self):
        p = self.page
        if not p.is_visible("#shiftBtn") or "فتح" not in p.inner_text("#shiftBtn"):
            return
        p.click("#shiftBtn")
        p.wait_for_timeout(500)
        if p.is_visible("#mUser"):
            with contextlib.suppress(Exception):
                p.select_option("#mUser", "owner")
            p.fill("#mPass", PASSWORD)
            if p.is_visible("#mFloat"):
                p.fill("#mFloat", str(self.rnd.choice([0, 200, 500])))
            p.click("#mOk")
            p.wait_for_timeout(1500)

    def add_item(self):
        p = self.page
        items = p.locator("#items .it").filter(has_not_text="بدون كود")
        items.nth(self.rnd.randrange(items.count())).click()
        p.wait_for_timeout(150)
        if p.is_visible("#szList"):
            b = p.locator("#szList button")
            b.nth(self.rnd.randrange(b.count())).click()
        if p.is_visible("#qtyModal") or p.is_visible("#wModal"):
            with contextlib.suppress(Exception):
                p.keyboard.type("1")
                p.keyboard.press("Enter")

    def sale(self, k, offline=False):
        p = self.page
        for _ in range(self.rnd.randint(1, 5)):
            self.add_item()
        if p.inner_text("#tot").strip() in ("0.00", "0"):
            return
        kind = self.rnd.choice(["cash", "cash", "cash", "card", "credit"])
        with contextlib.suppress(Exception):
            p.click(f"[data-pay={kind}]", timeout=2000)
        if kind == "cash" and self.rnd.random() < 0.5:
            p.fill("#cash", str(int(float(p.inner_text("#tot").replace(",", "") or 0)) + self.rnd.choice([0, 5, 50])))
        if kind == "credit":
            opts = p.evaluate("()=>[...document.querySelectorAll('#cust option')].map(o=>o.value).filter(Boolean)")
            if not opts or not p.is_visible("#cust"):
                with contextlib.suppress(Exception):
                    p.click("[data-pay=cash]", timeout=2000)
            else:
                p.select_option("#cust", opts[0])
        if k == 2 and p.is_visible("#isRet"):
            p.check("#isRet")
        t = time.time()
        p.click("#pay")
        p.wait_for_timeout(800)
        for _ in range(3):
            if p.is_visible("#lowModal"):
                self.I.add("ux-popup", "الكاشير", "نافذة «أصناف وصلت لحد الطلب» تظهر بعد البيع وتوقف الكاشير حتى يضغط «تمام»", self.n, None, "low")
                p.click("#lowOk")
            p.wait_for_timeout(200)
        if time.time() - t > 4:
            self.I.add("slow-sale", "الكاشير", f"التحصيل أخذ {time.time() - t:.1f} ثانية", self.n, None, "medium")
        if p.inner_text("#tot").strip() not in ("0.00", "0"):
            self.I.add("sale-stuck", "الكاشير", "بعد «تحصيل» بقيت الفاتورة كما هي" + (" (بدون إنترنت)" if offline else ""), self.n, p, "medium")
            with contextlib.suppress(Exception):
                p.click("#clear")

    def tables(self):
        p = self.page
        self.goto(p, "/ui/shop/tables/", "الترابيزات")
        p.fill("[name=count]", "4")
        p.click("form:has([name=op][value=add]) button")
        p.wait_for_load_state("load")
        self.goto(p, "/pos/", "الكاشير (ترابيزات)")
        p.wait_for_timeout(2500)
        if not p.is_visible("#tview"):
            p.click("#tablesBtn")
        p.locator(".tcard").first.click()
        p.wait_for_timeout(800)
        self.open_shift()
        for _ in range(2):
            for _ in range(self.rnd.randint(1, 3)):
                self.add_item()
            p.click("#sendK")
            p.wait_for_timeout(1200)
        if "حتى الآن" not in p.inner_text("#tabBox"):
            raise RuntimeError("the table shows no bill after «إرسال للمطبخ»")
        p.click("#pay")
        p.wait_for_timeout(2000)
        if not p.is_visible("#tview"):
            raise RuntimeError("after paying the table the till did not go back to the tables")

    def gaming(self):
        p = self.page
        self.goto(p, "/ui/play/", "صالة الألعاب")
        p.wait_for_timeout(1500)
        free = p.locator(".st.free")
        if free.count() == 0:
            self.I.add("gaming-empty", "صالة الألعاب", "لا توجد أجهزة بعد التجهيز", self.n, p, "medium")
            return
        if p.is_visible("#mShift"):
            p.click("#msGo")
            p.wait_for_timeout(800)
        if not p.is_visible("#mOpen"):
            free.first.click()
            p.wait_for_timeout(500)
        if p.is_visible("#mShift"):
            p.click("#msGo")
            p.wait_for_timeout(800)
            if not p.is_visible("#mOpen"):
                free.first.click()
        p.click("#moGo")
        p.wait_for_timeout(1500)
        if p.locator(".st.busy").count() == 0:
            raise RuntimeError("the device did not start")

    # ----------------------------------------------------------------- 5. the books
    def books(self):
        c = self.srv.db()
        try:
            bad = c.execute("SELECT entry_id, SUM(debit)-SUM(credit) d FROM ledger_journalline GROUP BY entry_id HAVING ABS(d) > 0.005").fetchall()
            if bad:
                self.I.add("books", "القيود", f"{len(bad)} قيد غير متوازن (مثال: {bad[0]})", self.n, None, "high")
            with contextlib.suppress(sqlite3.Error):
                errs = c.execute("SELECT COUNT(*) FROM pos_order").fetchone()[0]
                self.steps.append(f"orders in the books: {errs}")
        finally:
            c.close()
        log = (self.srv.tmp / "server.log").read_text(encoding="utf-8", errors="replace")
        for tb in log.split("Traceback (most recent call last):")[1:6]:
            lines = tb.splitlines()
            exc = next((ln for ln in lines if re.match(r"^[\w.]+(Error|Exception|DoesNotExist|Invalid\w*)\b", ln)), "?")
            ours = [ln.strip() for ln in lines if 'File "' in ln and "site-packages" not in ln and "lib/python" not in ln.replace("\\", "/")]
            where = ours[-1] if ours else "?"
            self.I.add("server-exception", re.sub(r'.*File "([^"]+)", line (\d+), in (\w+).*', r"\1:\2 \3", where), exc[:300] + "\n" + "\n".join(ours[-4:]), self.n, None, "high")

    # ----------------------------------------------------------------- the run
    def go(self):
        tmp = Path(tempfile.mkdtemp(prefix=f"trio-qa-{self.n}-"))
        self.srv = Server(self.args.app, self.args.python, tmp, free_port())
        self.base = f"http://127.0.0.1:{self.srv.port}"
        t0 = time.time()
        print(f"▶ run {self.n}: «{self.act[0]}» ({self.act[1]}){' — هاتف' if self.phone else ''}")
        ctx = None
        try:
            self.srv.start()
            vp = dict(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True) if self.phone else dict(viewport={"width": 1366, "height": 768})
            ctx = self.browser.new_context(locale="ar-EG", **vp)
            ctx.add_init_script("window.print=()=>{window.__printed=(window.__printed||0)+1}")
            self.page = ctx.new_page()
            self.page.set_default_timeout(15000)
            self.watch(self.page, "main")
            if not self.step("أول تشغيل (معالج التجربة)", self.wizard):
                return self.summary(t0)
            self.step("كل الصفحات", self.crawl)
            self.step("إضافة منتجات بيد المالك", self.products)
            self.step("النماذج", self.forms)
            self.step("الكاشير والبيع", self.till)
            if self.act[1] == "cafe":
                self.step("الترابيزات", self.tables)
            if self.act[1] == "gaming":
                self.step("صالة البلايستيشن", self.gaming)
            self.step("الحسابات متوازنة", self.books)
        finally:
            with contextlib.suppress(Exception):
                ctx and ctx.close()
            self.srv.stop()
            if not self.args.keep:
                shutil.rmtree(tmp, ignore_errors=True)
        return self.summary(t0)

    def summary(self, t0):
        return dict(run=self.n, activity=self.act[0], business=self.act[1], phone=self.phone, secs=round(time.time() - t0), steps=self.steps,
                    slowest=sorted(self.times.items(), key=lambda x: -x[1])[:5])


def report(out, runs, issues, args):
    sev_order = {"high": 0, "medium": 1, "low": 2}
    items = sorted(issues.items.values(), key=lambda x: (sev_order[x["sev"]], -len(x["runs"])))
    (out / "report.json").write_text(json.dumps(dict(runs=runs, issues=items), ensure_ascii=False, indent=1), encoding="utf-8")
    by = defaultdict(int)
    for i in items:
        by[i["sev"]] += 1
    rows = "".join(
        f"<tr class='{i['sev']}'><td>{ {'high': '🔴 عالية', 'medium': '🟠 متوسطة', 'low': '⚪ بسيطة'}[i['sev']]}</td><td>{html.escape(i['kind'])}</td><td>{html.escape(i['where'])}</td>"
        f"<td><pre>{html.escape(i['msg'][:900])}</pre></td><td>{len(i['runs'])}</td><td>{'<a href=' + i['shot'] + '>صورة</a>' if i['shot'] else ''}</td></tr>" for i in items)
    rr = "".join(f"<tr><td>{r['run']}</td><td>{html.escape(r['activity'])}</td><td>{'📱' if r['phone'] else '🖥'}</td><td>{r['secs']} ث</td>"
                 f"<td><small>{'<br>'.join(html.escape(s) for s in r['steps'])}</small></td></tr>" for r in runs)
    (out / "report.html").write_text(f"""<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8"><title>Trio QA</title>
<style>body{{font-family:Tahoma,Arial;margin:20px;color:#0B1F3A}}table{{border-collapse:collapse;width:100%;margin:12px 0}}td,th{{border:1px solid #DCE5EF;padding:6px 8px;vertical-align:top;font-size:13px}}
th{{background:#EEF2F7}}tr.high td:first-child{{background:#FDECEA}}tr.medium td:first-child{{background:#FFF4E0}}pre{{white-space:pre-wrap;margin:0;font:12px monospace;max-height:160px;overflow:auto}}</style>
<h1>تقرير اختبار Trio</h1><p>{len(runs)} تشغيلة · الأنشطة: {html.escape('، '.join(sorted({r['activity'] for r in runs})))} · مشاكل: 🔴 {by['high']} · 🟠 {by['medium']} · ⚪ {by['low']}</p>
<h2>المشاكل (مرتبة بالأهمية ثم بعدد التشغيلات التي ظهرت فيها)</h2><table><tr><th>الأهمية</th><th>النوع</th><th>المكان</th><th>التفاصيل</th><th>تكررت</th><th></th></tr>{rows}</table>
<h2>التشغيلات</h2><table><tr><th>#</th><th>النشاط</th><th>الشاشة</th><th>المدة</th><th>الخطوات</th></tr>{rr}</table></html>""", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--app", default=str(Path(__file__).resolve().parents[2]), help="the program folder (C:\\Trio or the source folder)")
    ap.add_argument("--python", default="", help="python of the program (default: <app>\\python\\python.exe if present, else this python)")
    ap.add_argument("--runs", type=int, default=52)
    ap.add_argument("--start", type=int, default=1, help="number of the first run (to continue a long series)")
    ap.add_argument("--out", default="trio-qa-report")
    ap.add_argument("--only", default="", help="one business: market / retail / cafe / gaming / clinic / pharmacy")
    ap.add_argument("--forms", type=int, default=8, help="pages per run whose forms are tried")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--headed", action="store_true", help="show the browser")
    ap.add_argument("--shots", action="store_true", help="a full screenshot of every page (first runs only)")
    ap.add_argument("--keep", action="store_true", help="keep each run's database")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    app = Path(args.app)
    if not args.python:
        win = app / "python" / "python.exe"
        args.python = str(win) if win.exists() else sys.executable
    out = Path(args.out)
    (out / "shots").mkdir(parents=True, exist_ok=True)
    (out / "pages").mkdir(exist_ok=True)
    if not (app / "staticfiles").exists():  # the source folder: the installed program ships them collected
        subprocess.run([args.python, "manage.py", "collectstatic", "--noinput", "-v0"], cwd=app, env=dict(os.environ, GG_DEBUG="0", GG_SQLITE=str(out / "cs.sqlite3")), check=True)
    from playwright.sync_api import sync_playwright

    issues, runs = Issues(out), []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headed)
        for n in range(args.start, args.start + args.runs):
            try:
                runs.append(Run(n, args, issues, browser).go())
            except Exception as e:  # noqa: BLE001
                issues.add("run-crash", f"run {n}", f"{type(e).__name__}: {e}", n, None, "high")
                runs.append(dict(run=n, activity="?", business="?", phone=False, secs=0, steps=[f"✖ {e}"], slowest=[]))
            report(out, runs, issues, args)
        browser.close()
    print(f"\n✔ {len(runs)} runs — report: {out / 'report.html'}")


if __name__ == "__main__":
    main()
