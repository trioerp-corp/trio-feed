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
        self.wz_tables = 0
        if p.is_visible("#qTables"):  # cafés / restaurants: «كم ترابيزة لديك؟»
            self.wz_tables = self.rnd.choice([0, 3, 6])
            p.fill("[name=tables]", str(self.wz_tables))
        elif self.act[1] == "cafe":
            self.I.add("wizard", "المعالج", "سؤال «كم ترابيزة لديك؟» لم يظهر لنشاط مطعم / كافيه", self.n, p, "medium")
        if self.rnd.random() < 0.3:
            p.check("[name=multi][value='1']")
        self.samples = self.rnd.random() >= 0.2
        if not self.samples:
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
        if getattr(self, "wz_tables", 0):
            got = self.q1("SELECT COUNT(*) FROM pos_diningtable") or 0
            if got != self.wz_tables:
                self.I.add("wizard", "المعالج", f"طلب المالك {self.wz_tables} ترابيزة في المعالج وأُنشئت {got}", self.n, None, "high")
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
                f.locator("[name=name]").fill(r.choice(["شاورما", "برجر", "كشري", "وافل", "سحلب"]) + f" خاص {self.n}-{k + 1}")
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
            f.locator("[name=name]").fill(r.choice(["منتج", "صنف", "سلعة"]) + f" يدوي {self.n}-{k + 1}")
            if f.locator("[name=barcode]").count():
                f.locator("[name=barcode]").fill(str(r.randint(10 ** 11, 10 ** 12)))
            if f.locator("[name=price]").count():
                f.locator("[name=price]").fill(str(r.randint(5, 500)))
            f.locator("[name=reorder]").fill(r.choice(["0", "5"]))
            f.locator("button[name=new]").click()
            p.wait_for_load_state("load")
            self.page_checks(p, "الأصناف ← إضافة صنف")

    # ----------------------------------------------------------------- 3c. «تجهيز المحل»: the shop logo
    @staticmethod
    def png(w=48, h=48):
        import struct
        import zlib

        raw = b"".join(b"\x00" + bytes(v for x in range(w) for v in (20 + x * 4 % 200, 120, 200 - y * 3 % 180)) for y in range(h))
        chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)  # noqa: E731
        return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")

    def logo(self):
        p = self.page
        self.goto(p, "/ui/start/", "تجهيز المحل")
        f = p.locator("form:has(input[name=op][value=shop])")
        if not f.count() or not f.locator("input[type=file][name=logo]").count():
            self.I.add("logo", "تجهيز المحل", "الخطوة الأولى بدون رفع شعار المحل", self.n, p, "medium")
            return
        if f.locator("[name=name]").count() and not f.locator("[name=name]").input_value().strip():
            f.locator("[name=name]").fill(f"محل التجربة {self.n}")
        f.locator("input[type=file][name=logo]").set_input_files(files=[{"name": "logo.png", "mimeType": "image/png", "buffer": self.png()}])
        with p.expect_navigation(timeout=20000):
            f.locator("button:not([type=button])").last.click()
        self.page_checks(p, "تجهيز المحل ← رفع الشعار")
        errs = self.errors_on(p)
        svg = self.q1("SELECT logo_svg FROM core_companysettings LIMIT 1") or ""
        if errs or "data:image/png;base64," not in svg:
            self.I.add("logo", "تجهيز المحل", f"لم يُحفظ الشعار المرفوع: {' / '.join(errs) or 'logo_svg فارغ'}", self.n, p, "high")
            return
        self.goto(p, "/ui/", "الرئيسية (بعد الشعار)")
        top = p.locator(".gg-brand svg image")
        if not top.count() or not top.first.is_visible() or "data:image/png" not in (top.first.get_attribute("href") or ""):
            self.I.add("logo", "الشريط العلوي", "الشعار المرفوع لا يظهر في أعلى الشاشة", self.n, p, "high")
        else:
            box = p.locator(".gg-brand svg").first.bounding_box() or {}
            if box.get("width", 0) < 16:
                self.I.add("logo", "الشريط العلوي", f"الشعار في أعلى الشاشة صغير جدًا ({box})", self.n, p, "medium")
        self.steps.append("logo: uploaded and shown in the top bar")

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
        if getattr(self, "note", None):
            if not self.q1("SELECT COUNT(*) FROM pos_order WHERE json_extract(raw, '$.note')=?", self.note):
                self.I.add("till-note", "الكاشير", f"ملاحظة الطلب «{self.note}» لم تصل مع الفاتورة إلى البرنامج", self.n, None, "high")
            self.note = None
        if getattr(self, "ret_reason", None):
            got = self.q("SELECT total, flags FROM pos_order WHERE is_return=1 AND cancelled=0")
            if not got:
                self.I.add("till-return", "الكاشير", "المرتجع المعتمد بكلمة سر المالك لم يُسجَّل في البرنامج", self.n, p, "high")
            self.ret_reason = None
        self.forged_return()
        self.close_shift()

    def till_return(self):
        """«مرتجع» ticked → #retModal: a reason, and (unless cashiers may edit) the owner's user name + password.
        A wrong password must be refused; the owner's real one accepted. → True when the return was paid."""
        p = self.page
        try:
            p.wait_for_selector("#retModal", state="visible", timeout=4000)
        except Exception:  # noqa: BLE001
            self.I.add("till-return", "الكاشير", "علامة «مرتجع» ثم «تحصيل» لم تفتح نافذة المرتجع (سبب + موافقة المالك)", self.n, p, "high")
            return True
        p.click("#retOk")  # no reason
        if not p.is_visible("#retErr") or not p.is_visible("#retModal"):
            self.I.add("till-return", "الكاشير", "نافذة المرتجع قبلت «اعتماد» بدون سبب", self.n, p, "medium")
        reason = self.rnd.choice(["الصنف تالف", "العميل غيّر رأيه", "خطأ في الصنف"])
        p.fill("#retWhy", reason)
        if p.is_visible("#retOwner"):
            p.fill("#retU", "owner")
            p.fill("#retP", "wrong-" + PASSWORD)
            p.click("#retOk")
            p.wait_for_timeout(1500)
            if not p.is_visible("#retModal") or not p.is_visible("#retErr"):
                self.I.add("till-return", "الكاشير", "المرتجع قُبل بكلمة سر خاطئة للمالك", self.n, p, "high")
            elif p.inner_text("#tot").strip() in ("0.00", "0"):
                self.I.add("till-return", "الكاشير", "بعد رفض كلمة السر الخاطئة اختفت الفاتورة", self.n, p, "high")
            p.fill("#retP", PASSWORD)
            p.click("#retOk")
            p.wait_for_timeout(1500)
            if p.is_visible("#retModal"):
                self.I.add("till-return", "الكاشير", f"المرتجع رُفض بكلمة سر المالك الصحيحة: {p.inner_text('#retErr')[:200]}", self.n, p, "high")
                p.click("#retCancel")
                with contextlib.suppress(Exception):
                    p.click("#clear")
                return False
        else:
            p.click("#retOk")
            p.wait_for_timeout(800)
        self.ret_reason = reason
        self.steps.append("till: return refused with a wrong password, approved with the owner's")
        return True

    def forged_return(self):
        """The server must refuse a return sent by a till without the owner's approval (unless cashiers may edit)."""
        p = self.page
        if self.q1("SELECT cashier_can_edit FROM core_companysettings LIMIT 1"):
            self.steps.append("till: cashiers may edit — a forged return is allowed by the settings")
            return
        sh = p.evaluate("()=>JSON.parse(localStorage.getItem('pos_shift')||'null')")
        code = self.q1("SELECT code FROM inventory_item WHERE active=1 AND sale_price>0 AND code<>'0' ORDER BY id LIMIT 1")
        if not sh or not code:
            return
        pre = sh["uid"].rsplit("-", 1)[0]
        uid = f"{pre}-{8000000 + self.rnd.randint(1, 99999):07d}"
        body = {"orders": [{"uid": uid, "shift_uid": sh["uid"], "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "is_return": True, "ret_reason": "تجربة",
                            "paid_cash": "5.00", "paid_card": "0.00", "paid_credit": "0.00", "lines": [{"item_code": code, "qty": 1, "price": 5}]}]}
        r = p.request.post(self.base + "/pos/api/sync/", data=json.dumps(body), headers={"Content-Type": "application/json", "X-Device-Token": self.device_token()})
        j = {}
        with contextlib.suppress(Exception):
            j = r.json()
        if uid in (j.get("accepted") or []) or self.q1("SELECT COUNT(*) FROM pos_order WHERE uid LIKE ?", uid + "%"):
            self.I.add("till-return", "/pos/api/sync/", "الخادم سجّل مرتجعًا أرسله الكاشير بدون موافقة المالك (النقدية تخرج بلا رقابة)", self.n, None, "high")
        elif uid not in (j.get("errors") or {}):
            self.I.add("till-return", "/pos/api/sync/", f"مرتجع بدون موافقة: الرد غير متوقع HTTP {r.status} {str(j)[:200]}", self.n, None, "medium")
        # the next real sync clears Device.stuck again
        self.steps.append("till: forged return refused by the server")

    def close_shift(self):
        """Blind close: the drawer is counted without the expected amount on the screen, then «تقرير الوردية» shows it."""
        p = self.page
        if not p.is_visible("#shiftBtn") or "قفل" not in p.inner_text("#shiftBtn"):
            return
        sh = p.evaluate("()=>JSON.parse(localStorage.getItem('pos_shift')||'null')")
        p.click("#shiftBtn")
        try:
            p.wait_for_selector("#closeModal", state="visible", timeout=4000)
        except Exception:  # noqa: BLE001
            self.I.add("shift-close", "الكاشير", "«قفل الوردية» لم يفتح نافذة عدّ النقدية", self.n, p, "high")
            return
        txt = p.inner_text("#closeModal")
        if re.search(r"\d", txt) or "المتوقع" in txt:
            self.I.add("shift-close", "الكاشير", f"نافذة قفل الوردية تُظهر أرقامًا / المتوقع قبل العدّ (ليس عدًّا أعمى): {txt[:200]}", self.n, p, "high")
        p.fill("#cCount", "abc")
        p.click("#cOk")
        if not p.is_visible("#cErr"):
            self.I.add("shift-close", "الكاشير", "قفل الوردية قبل «abc» مبلغًا للدرج", self.n, p, "medium")
        counted = self.rnd.choice([0, 100, 250, 1000])
        p.fill("#cCount", f"{counted:,}" if counted >= 1000 else str(counted))
        p.click("#cOk")
        try:
            p.wait_for_selector("#repModal", state="visible", timeout=4000)
        except Exception:  # noqa: BLE001
            self.I.add("shift-close", "الكاشير", "بعد قفل الوردية لم يظهر «تقرير الوردية»", self.n, p, "high")
            return
        rows = dict(p.evaluate("()=>{const e=[...document.querySelectorAll('#repBody .rep > *')];const o=[];for(let i=0;i+1<e.length;i+=2)o.push([e[i].textContent.trim(),e[i+1].textContent.trim()]);return o}"))
        exp, cnt = self.num(rows.get("النقدية المتوقعة في الدرج")), self.num(rows.get("النقدية المعدودة"))
        if counted >= 1000 and cnt is not None and abs(cnt - counted / 1000) < 0.01:
            self.I.add("number-input", "الكاشير ← قفل الوردية", f"كُتب «{counted:,}» (كما يعرض البرنامج الأرقام) فقُرئ {cnt:g} — الفاصلة تُقرأ علامة عشرية", self.n, p, "medium")
            counted = cnt
        if exp is None or cnt is None or abs(cnt - counted) > 0.01:
            self.I.add("shift-close", "تقرير الوردية", f"التقرير بدون المتوقع أو المعدود صحيحًا (عُدّ {counted}، المتوقع {exp}، المعدود {cnt}): {rows}", self.n, p, "high")
        if exp is not None and exp < 0:
            self.I.add("shift-close", "تقرير الوردية", f"النقدية المتوقعة في الدرج بالسالب ({exp:g}) — قَبِل الكاشير مرتجعًا نقديًا أكبر من النقدية الموجودة في الدرج", self.n, p, "medium")
        p.click("#repOk")
        p.wait_for_timeout(6000)  # the closed shift syncs
        db = self.q("SELECT closed_at, counted_cash, expected_cash FROM pos_shift WHERE uid=?", sh["uid"]) if sh else []
        if not db or not db[0][0]:
            self.I.add("shift-close", "الكاشير", f"الوردية المقفولة لم تصل إلى البرنامج كمقفولة: {db}", self.n, p, "high")
        else:
            if abs(float(db[0][1] or 0) - counted) > 0.01:
                self.I.add("shift-close", "الكاشير", f"المعدود في البرنامج {db[0][1]} والمكتوب {counted}", self.n, None, "high")
            if exp is not None and db[0][2] is not None and abs(float(db[0][2]) - exp) > 0.01:
                self.I.add("shift-close", "تقرير الوردية", f"«النقدية المتوقعة» في تقرير الكاشير {exp} وفي البرنامج {db[0][2]}", self.n, None, "high")
        self.steps.append(f"till: blind close, float {rows.get('رصيد الفتح')} cash sales {rows.get('مبيعات نقدية (بعد المرتجع)')} expected {exp} "
                          f"(server {db[0][2] if db else '?'}) counted {counted}")
        if exp is not None and exp > 100000:
            self.I.add("shift-close", "تقرير الوردية", f"النقدية المتوقعة كبيرة بشكل غير معقول: {rows}", self.n, None, "high")

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
            p.click(f"#payKinds button[data-k={kind}]", timeout=2000)
        if kind == "cash" and self.rnd.random() < 0.5:
            p.fill("#cash", str(int(float(p.inner_text("#tot").replace(",", "") or 0)) + self.rnd.choice([0, 5, 50])))
        if kind == "credit":
            opts = p.evaluate("()=>[...document.querySelectorAll('#cust option')].map(o=>o.value).filter(Boolean)")
            if not opts or not p.is_visible("#cust"):
                with contextlib.suppress(Exception):
                    p.click("#payKinds button[data-k=cash]", timeout=2000)
            else:
                p.select_option("#cust", opts[0])
        if k == 1 and p.is_visible("#oNote"):  # a note on the whole order (kitchen + receipt)
            p.click("#oNote")
            self.note = f"بدون ثلج {self.n}-{self.rnd.randint(100, 999)}"
            p.fill("#oNoteIn", self.note)
            p.press("#oNoteIn", "Enter")
            if self.note not in p.inner_text("#oNoteBox"):
                self.I.add("till-note", "الكاشير", "ملاحظة الطلب لم تظهر على الفاتورة بعد حفظها", self.n, p, "medium")
        ret = k == 2 and p.is_visible("#isRet")
        if ret:
            p.check("#isRet")
        t = time.time()
        p.click("#pay")
        if ret:
            if not self.till_return():
                return
            t = time.time()
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
        tab = self.num(p.inner_text("#tot"))
        p.click("#pay")  # «الحساب والتحصيل» → the payment step
        p.wait_for_timeout(1200)
        if "تحصيل حساب" not in p.inner_text("#cartT") or p.is_visible("#tview"):
            self.I.add("tables", "الكاشير (ترابيزات)", f"«الحساب والتحصيل» لم يفتح خطوة الدفع: {p.inner_text('#cartT')}", self.n, p, "high")
        total = self.num(p.inner_text("#tot"))
        if not total:
            raise RuntimeError("the table payment step shows no total")
        p.click("#pay")  # «تحصيل وطباعة»
        p.wait_for_timeout(2500)
        for _ in range(2):
            if p.is_visible("#lowModal"):
                p.click("#lowOk")
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
        # a buffet order on the running device, then «قفل وحساب» → «✔ تم الدفع واطبع»
        sid = self.q1("SELECT id FROM gaming_playsession WHERE state='open' AND station_id IS NOT NULL ORDER BY id DESC LIMIT 1")
        if not p.is_visible("#side"):
            p.locator(".st.busy").first.click()
            p.wait_for_timeout(500)
        menu = p.locator("#menu button")
        if menu.count() == 0:
            self.I.add("gaming-buffet", "صالة الألعاب", "لا توجد أصناف بوفيه في لوحة الجهاز بعد التجهيز", self.n, p, "medium")
        else:
            menu.nth(self.rnd.randrange(menu.count())).click()
            p.wait_for_timeout(1200)
            if not self.q1("SELECT COUNT(*) FROM gaming_playitem WHERE session_id=?", sid):
                self.I.add("gaming-buffet", "صالة الألعاب", "الضغط على صنف البوفيه لم يُضِفه إلى الجهاز", self.n, p, "high")
        p.click("#payBtn")
        p.wait_for_selector("#mPay.on", timeout=5000)
        shown = self.num(p.inner_text("#mpTotal"))
        try:
            with p.context.expect_page(timeout=8000) as pop:
                p.click("#mpGo")
            pop.value.close()
        except Exception:  # noqa: BLE001 — the receipt window is a nicety; the closing is what counts
            p.wait_for_timeout(1500)
        p.wait_for_timeout(800)
        st = self.q("SELECT state, order_id, total, items_amount FROM gaming_playsession WHERE id=?", sid)
        if not st or st[0][0] != "closed" or not st[0][1]:
            self.I.add("gaming-close", "صالة الألعاب", f"بعد «تم الدفع» لم تُقفل الجلسة أو لم تُسجَّل فاتورتها: {st}", self.n, p, "high")
            raise RuntimeError("the play session was not closed")
        o = self.q("SELECT total, paid_cash+paid_card+paid_credit+paid_rider FROM pos_order WHERE id=?", st[0][1])[0]
        if abs(float(o[0]) - float(st[0][2])) > 0.01 or (shown is not None and abs(shown - float(o[0])) > 0.01):
            self.I.add("gaming-close", "صالة الألعاب", f"إجمالي الجلسة {st[0][2]} / المعروض {shown} / الفاتورة {o[0]} غير متطابقة", self.n, None, "high")
        if p.locator(".st.busy").count():
            self.I.add("gaming-close", "صالة الألعاب", "الجهاز ما زال «مشغول» على الشاشة بعد الدفع", self.n, p, "medium")
        self.steps.append(f"gaming: session {sid} closed, buffet {st[0][3]}, total {st[0][2]}")

    # ----------------------------------------------------------------- 4b. buying, counting, returns, the clinic
    def q(self, sql, *a):
        c = self.srv.db()
        try:
            return c.execute(sql, a).fetchall()
        finally:
            c.close()

    def q1(self, sql, *a):
        r = self.q(sql, *a)
        return r[0][0] if r else None

    @staticmethod
    def num(txt):
        """A number as the screens show it: «1,234.50», «(120)» for a negative, «45 ج»."""
        s = (txt or "").strip().translate(str.maketrans("٠١٢٣٤٥٦٧٨٩٫", "0123456789."))
        neg = "(" in s or s.startswith("-")
        s = re.sub(r"[^\d.]", "", s)
        try:
            return -float(s) if neg else float(s)
        except ValueError:
            return None

    def errors_on(self, page):
        return [t.strip() for t in page.locator(".msg.error").all_inner_texts() if t.strip()]

    def stock_items(self, n):
        rows = self.q("SELECT id, code, name FROM inventory_item WHERE active=1 AND item_type='stock' AND code<>'0' AND is_addon=0 "
                      "AND length(name) BETWEEN 2 AND 80 AND id NOT IN (SELECT variant_of_id FROM inventory_item WHERE variant_of_id IS NOT NULL) ORDER BY id")
        rows = [r for r in rows if r[2] == r[2].strip() and "|" not in r[2] and "  " not in r[2]]
        return self.rnd.sample(rows, min(n, len(rows)))

    def qty(self, item_id):
        return float(self.q1("SELECT qty_on_hand FROM inventory_item WHERE id=?", item_id) or 0)

    def purchase(self):
        p, r = self.page, self.rnd
        picks = self.stock_items(r.randint(2, 4))  # none (a restaurant's menu is recipes): the raw goods are typed in as new items
        before = {i: self.qty(i) for i, _, _ in picks}
        self.goto(p, "/ui/shop/purchases/new/", "فاتورة مشتريات")
        sups = p.evaluate("()=>[...document.querySelectorAll('#pf [name=supplier] option')].map(o=>o.value).filter(Boolean)")
        if sups and r.random() < 0.5:
            p.select_option("#pf [name=supplier]", r.choice(sups))
        else:
            p.fill("#pf [name=new_supplier]", f"مورد الجملة {self.n}")
        p.fill("#pf [name=invoice]", f"INV-{r.randint(100, 9999)}")
        rows = p.locator("#rows tr")
        while rows.count() < len(picks):
            p.click("#addRow")
        want, total = {}, 0.0
        pay = r.choice(["cash", "credit", "credit", "split", "owner"])
        for k, (iid, code, name) in enumerate(picks):
            tr = rows.nth(k)
            t = tr.locator("input[list=itemlist]")
            t.fill(f"{name} | {code}")
            t.dispatch_event("change")
            if tr.locator("input[name=item]").input_value() != str(iid):
                raise RuntimeError(f"picking «{name}» from the list did not set the item")
            qty, cost = (r.choice([1, 2]), r.choice([3.5, 7.25, 10])) if pay == "cash" else (r.choice([1, 2, 5, 10, 12]), r.choice([3.5, 10, 25, 7.25, 120]))
            tr.locator("[name=qty]").fill(str(qty))
            tr.locator("[name=cost]").fill(str(cost))
            want[iid] = qty
            total += qty * cost
        new_name = None
        if not picks or r.random() < 0.4:  # a product bought for the first time: typed in the line, added on save
            p.click("#addRow")
            tr = rows.nth(len(picks))
            new_name = f"صنف جديد من المورد {self.n}-{r.randint(100, 999)}"
            tr.locator("input[list=itemlist]").fill(new_name)
            tr.locator("input[list=itemlist]").dispatch_event("change")
            tr.locator("[name=qty]").fill("3")
            tr.locator("[name=cost]").fill("4")
            total += 12
        p.locator("#pf .paysel label").filter(has=p.locator(f"input[value={pay}]")).click()
        if pay == "split":
            p.fill("#pf [name=paid]", f"{total / 2:.2f}")
        grand = self.num(p.inner_text("#grand"))
        if grand is None or abs(grand - total) > 0.01:
            self.I.add("purchase", "فاتورة مشتريات", f"الإجمالي على الشاشة {grand} والصحيح {total:.2f}", self.n, p, "medium")
        last = self.q1("SELECT MAX(id) FROM inventory_stockmove") or 0
        with p.expect_navigation(timeout=30000):
            p.click("#pf .actions button")
        self.page_checks(p, "فاتورة مشتريات ← حفظ")
        errs = self.errors_on(p)
        if "/purchases/new" in p.url or errs:
            if pay != "credit" and any("رصيد" in e or "يكفي" in e for e in errs):  # an empty drawer on day one: buy on credit
                self.steps.append(f"purchase: cash refused ({errs[0][:80]}) — on credit")
                p.locator("#pf .paysel label").filter(has=p.locator("input[value=credit]")).click()
                pay = "credit"
                with p.expect_navigation(timeout=30000) as nav:
                    p.click("#pf .actions button")
                errs = self.errors_on(p)
                st = nav.value.status if nav.value else "?"
                if "/purchases/new" in p.url and not errs:
                    errs = [f"HTTP {st} عند إعادة الحفظ «آجل» بعد رفض الدفع النقدي" + (f" (الفاتورة فيها صنف جديد «{new_name}»)" if new_name else "")]
            if "/purchases/new" in p.url or errs:
                self.I.add("purchase", "فاتورة مشتريات", f"لم تُحفظ فاتورة المشتريات: {' / '.join(errs) or p.url}", self.n, p, "high")
                raise RuntimeError("the purchase invoice was not saved")
        mv = self.q("SELECT id, state FROM inventory_stockmove WHERE kind='receipt' AND id>? ORDER BY id DESC LIMIT 1", last)
        if not mv or mv[0][1] != "posted":
            self.I.add("purchase", "فاتورة مشتريات", f"لا توجد حركة وارد مرحّلة بعد الحفظ: {mv}", self.n, None, "high")
            return
        value = float(self.q1("SELECT COALESCE(SUM(value),0) FROM inventory_stockmoveline WHERE move_id=?", mv[0][0]))
        if abs(value - total) > 0.05:
            self.I.add("purchase", "فاتورة مشتريات", f"قيمة الحركة {value:.2f} ≠ إجمالي الفاتورة {total:.2f}", self.n, None, "high")
        for iid, qty in want.items():
            got = self.qty(iid) - before[iid]
            if abs(got - qty) > 0.001:
                self.I.add("purchase-stock", "فاتورة مشتريات", f"الصنف {iid}: الرصيد زاد {got:g} والمفروض {qty}", self.n, None, "high")
        if new_name:
            ni = self.q("SELECT id, qty_on_hand FROM inventory_item WHERE name=?", new_name)
            if not ni or abs(float(ni[0][1]) - 3) > 0.001:
                self.I.add("purchase-new-item", "فاتورة مشتريات", f"الصنف الجديد المكتوب في السطر «{new_name}» لم يُضف بكمية 3: {ni}", self.n, None, "high")
            else:
                want[ni[0][0]] = 3
        paid = float(self.q1("SELECT COALESCE(SUM(amount),0) FROM treasury_voucher WHERE legacy_number=? AND state='posted'", f"PUR-{mv[0][0]}") or 0)
        exp = total if pay == "cash" else round(total / 2, 2) if pay == "split" else 0
        if abs(paid - exp) > 0.05:
            self.I.add("purchase-pay", "فاتورة مشتريات", f"الدفع «{pay}»: المسدَّد من الخزينة {paid:.2f} والمفروض {exp:.2f}", self.n, None, "high")
        if pay == "owner":
            own = self.q("SELECT amount, from_owner FROM pos_cashmove WHERE uid=?", f"PUR-{mv[0][0]}-OWNER")
            if not own or not own[0][1] or abs(float(own[0][0]) - total) > 0.05:
                self.I.add("purchase-pay", "فاتورة مشتريات", f"«من صاحب المحل»: لم يُسجَّل للمالك {total:.2f} ({own})", self.n, None, "high")
        self.bought = dict(items=want, move=mv[0][0], supplier=self.q1("SELECT partner_id FROM inventory_stockmove WHERE id=?", mv[0][0]))
        self.steps.append(f"purchase: {len(want)} lines, {total:.2f}, {pay}")

    def stocktake(self):
        p, r = self.page, self.rnd
        picks = self.stock_items(1)
        if not picks:
            self.steps.append("stocktake: no stock items — skipped")
            return
        iid, code, name = picks[0]
        self.goto(p, "/ui/shop/count/", "الجرد")
        wh = p.locator("#cf input[name=warehouse]").input_value()
        inp = p.locator(f"#cf input[name=c_{iid}]")
        if not inp.count():
            self.goto(p, f"/ui/shop/count/?warehouse={wh}&q={code}", "الجرد")
            inp = p.locator(f"#cf input[name=c_{iid}]")
        if not inp.count():
            self.I.add("stocktake", "الجرد", f"الصنف المخزني «{name}» غير موجود في شاشة الجرد", self.n, p, "high")
            return
        book = float(p.locator(f"tr:has(input[name=c_{iid}])").get_attribute("data-book") or 0)
        db_book = float(self.q1("SELECT qty FROM inventory_stockquant WHERE item_id=? AND warehouse_id=?", iid, wh) or 0)
        if abs(book - db_book) > 0.001:
            self.I.add("stocktake", "الجرد", f"«الرصيد في البرنامج» {book:g} ≠ رصيد المخزن {db_book:g}", self.n, p, "high")
        counted = max(0, int(book) + r.choice([-2, -1, 2, 3, 7]))
        if counted == book:
            counted = book + 1
        inp.fill(str(int(counted)))
        inp.dispatch_event("input")
        if p.inner_text("#nD").strip() != "1":
            self.I.add("stocktake", "الجرد", f"ملخص الشاشة يقول «فيه فرق {p.inner_text('#nD')}» بعد جرد صنف واحد مختلف", self.n, p, "low")
        with p.expect_navigation(timeout=20000):
            p.click("#cf button[name=action][value=review]")
        self.page_checks(p, "الجرد ← مراجعة الفروق")
        post = p.locator("button[name=action][value=post]")
        if not post.count():
            self.I.add("stocktake", "الجرد", f"صفحة المراجعة بدون زر «تسوية الفرق» (الدفتري {book:g} والمعدود {counted:g})", self.n, p, "high")
            return
        with p.expect_navigation(timeout=20000):
            post.click()
        self.page_checks(p, "الجرد ← التسوية")
        errs = self.errors_on(p)
        now = float(self.q1("SELECT qty FROM inventory_stockquant WHERE item_id=? AND warehouse_id=?", iid, wh) or 0)
        if errs or abs(now - counted) > 0.001:
            self.I.add("stocktake", "الجرد", f"بعد التسوية رصيد «{name}» {now:g} والمعدود {counted:g} {' / '.join(errs)}", self.n, p, "high")
        self.steps.append(f"stocktake: {book:g} → {counted:g}")

    def returns(self):
        """/ui/shop/returns/ is the PURCHASE return / expired goods page (sales returns are made at the till, «مرتجع»)."""
        p, r = self.page, self.rnd
        b = getattr(self, "bought", None)
        cand = [i for i in (b or {}).get("items", {}) if self.qty(i) >= 1] if b else []
        kind = r.choice(["preturn", "preturn", "expired"])
        if not cand:  # what was bought is already sold: throw away an expired piece of anything in stock
            kind, b = "expired", None
            cand = [i for i, _, _ in self.stock_items(50) if self.qty(i) >= 1]
        if not cand:
            self.steps.append("returns: nothing in stock — skipped (the till already sells a return)")
            return
        iid = r.choice(cand)
        code, name = self.q("SELECT code, name FROM inventory_item WHERE id=?", iid)[0]
        before = self.qty(iid)
        self.goto(p, "/ui/shop/returns/", "مرتجع مشتريات وهالك")
        f = p.locator("form:has([name=kind])")
        f.locator("[name=kind]").select_option(kind)
        if kind == "preturn":
            sup = str(b.get("supplier") or "")
            opts = f.locator("[name=supplier] option").evaluate_all("os=>os.map(o=>o.value)")
            if sup not in opts:
                self.I.add("returns", "مرتجع مشتريات", "مورد فاتورة المشتريات غير موجود في قائمة الموردين", self.n, p, "medium")
            else:
                f.locator("[name=supplier]").select_option(sup)
        row = f.locator("#rows > div").first
        t = row.locator("input[list=itemlist]")
        t.fill(f"{name} | {code}")
        t.dispatch_event("change")
        if row.locator("input[name=item]").input_value() != str(iid):
            raise RuntimeError(f"picking «{name}» on the returns page did not set the item")
        row.locator("[name=qty]").fill("1")
        last = self.q1("SELECT MAX(id) FROM inventory_stockmove") or 0
        with p.expect_navigation(timeout=20000):
            f.locator("button:not([type=button])").click()
        self.page_checks(p, "مرتجع مشتريات ← تسجيل")
        errs = self.errors_on(p)
        mv = self.q("SELECT id, state, partner_id FROM inventory_stockmove WHERE kind=? AND id>?", kind, last)
        if errs or not mv or mv[0][1] != "posted":
            self.I.add("returns", "مرتجع مشتريات وهالك", f"لم يُسجَّل «{kind}»: {' / '.join(errs) or mv}", self.n, p, "high")
            return
        if abs(before - 1 - self.qty(iid)) > 0.001:
            self.I.add("returns-stock", "مرتجع مشتريات وهالك", f"«{kind}» 1 من «{name}»: الرصيد {before:g} → {self.qty(iid):g}", self.n, None, "high")
        self.steps.append(f"returns: {kind} 1 × {iid}")

    def clinic(self):
        p, r = self.page, self.rnd
        if not self.q1("SELECT COUNT(*) FROM clinic_service s JOIN inventory_item i ON i.id=s.item_id WHERE i.active=1"):
            self.goto(p, "/ui/clinic/setup/", "الخدمات والأسعار")
            f = p.locator("form:has(input[name=action][value=doctor])")
            f.locator("[name=name]").fill("أحمد سامي")
            f.locator("button:not([type=button])").click()
            p.wait_for_load_state("load")
            f = p.locator("form:has(input[name=action][value=service])")
            f.locator("[name=name]").fill(f"كشف عام {self.n}")
            f.locator("[name=price]").fill("200")
            f.locator("button:not([type=button])").last.click()
            p.wait_for_load_state("load")
            if getattr(self, "samples", True):  # with the examples declined an empty price list is expected
                self.I.add("clinic-setup", "العيادة", "لا توجد خدمات (كشف) بعد التجهيز مع الأمثلة — أضافها الروبوت يدويًا", self.n, None, "medium")
            self.steps.append("clinic: no services — the robot added a doctor and «كشف»")
        # reception: a new patient file
        name = r.choice(["محمد علي", "سارة حسن", "منى إبراهيم", "خالد يوسف"]) + f" {self.n}{r.randint(10, 99)}"
        self.goto(p, "/ui/clinic/patients/", "ملفات المرضى")
        f = p.locator("form[method=post]:has([name=medical_notes])")
        f.locator("[name=name]").fill(name)
        f.locator("[name=phone]").fill(f"011{r.randint(10000000, 99999999)}")
        with p.expect_navigation():
            f.locator("button").click()
        pid = self.q1("SELECT id FROM clinic_patient WHERE name=?", name)
        if not pid:
            raise RuntimeError("the new patient file was not saved")
        # walk-in: into the queue
        self.goto(p, "/ui/clinic/reception/", "الاستقبال")
        d = p.locator("details:has(input[name=action][value=walkin])")
        if not d.evaluate("e=>e.open"):
            d.locator("summary").click()
        f = d.locator("form")
        f.locator(".pq").fill(name.split()[0] + " " + name.split()[1])
        hit = f.locator(".pres button").filter(has_text=name)
        hit.first.wait_for(timeout=8000)
        hit.first.click()
        if f.locator("input[name=patient]").input_value() != str(pid):
            raise RuntimeError("picking the patient in the walk-in form did not set him")
        svcs = f.locator("[name=service] option").evaluate_all("os=>os.map(o=>o.value).filter(Boolean)")
        docs = f.locator("[name=doctor] option").evaluate_all("os=>os.map(o=>o.value).filter(Boolean)")
        if docs:
            f.locator("[name=doctor]").select_option(r.choice(docs))
        if svcs:
            f.locator("[name=service]").select_option(r.choice(svcs))
        with p.expect_navigation():
            f.locator("button:not([type=button])").last.click()
        self.page_checks(p, "الاستقبال ← دخّله الدور")
        card = p.locator(".lane .pt").filter(has_text=name)
        if not card.count():
            self.I.add("clinic", "الاستقبال", f"المريض «{name}» لم يظهر في الدور بعد «دخّله الدور» {self.errors_on(p)}", self.n, p, "high")
            raise RuntimeError("the walk-in is not in the queue")
        with p.expect_navigation():
            card.first.locator("button[name=action][value=inside]").click()
        m = re.search(r"/visits/(\d+)/", p.url)
        if not m:
            self.I.add("clinic", "الاستقبال", f"«دخل للدكتور» لم يفتح شاشة الكشف (الصفحة {self.path(p)})", self.n, p, "high")
            raise RuntimeError("no visit page after «inside»")
        vid = int(m.group(1))
        self.page_checks(p, "الكشف والحساب")
        # the doctor writes the visit and adds a service
        p.fill("#vf [name=diagnosis]", "التهاب بسيط")
        codes = [c for (c,) in self.q("SELECT i.code FROM clinic_service s JOIN inventory_item i ON i.id=s.item_id WHERE i.active=1")]
        p.fill("#vf [name=add_code]", r.choice(codes))
        with p.expect_navigation():
            p.click("#vf button[name=action][value=add]")
        n_lines = self.q1("SELECT COUNT(*) FROM clinic_visitline WHERE visit_id=?", vid)
        if not n_lines:
            self.I.add("clinic", "الكشف والحساب", f"إضافة الخدمة لم تُسجّل سطرًا في الزيارة {self.errors_on(p)}", self.n, p, "high")
            raise RuntimeError("the service was not added to the visit")
        # checkout at the reception
        pay = r.choice(["cash", "cash", "card", "credit"])
        p.select_option("#pay", pay)
        p.uncheck("#vf input[name=print]")
        shown = self.num(p.inner_text("#tot"))
        with p.expect_navigation():
            p.click("#vf button[name=action][value=pay]")
        self.page_checks(p, "الكشف والحساب ← حاسب")
        v = self.q("SELECT state, total FROM clinic_visit WHERE id=?", vid)[0]
        pays = self.q("SELECT vp.total, o.total, o.paid_cash, o.paid_card, o.paid_credit FROM clinic_visitpayment vp JOIN pos_order o ON o.id=vp.order_id "
                      "WHERE vp.visit_id=? AND vp.cancelled=0", vid)
        if v[0] != "paid" or not pays:
            self.I.add("clinic", "الكشف والحساب", f"بعد «حاسب ✔» الزيارة «{v[0]}» وعدد الفواتير {len(pays)} {self.errors_on(p)}", self.n, p, "high")
            raise RuntimeError("the visit was not paid")
        billed = sum(float(x[1]) for x in pays)
        if abs(billed - float(v[1])) > 0.01 or (shown is not None and abs(shown - billed) > 0.01):
            self.I.add("clinic", "الكشف والحساب", f"المعروض {shown} / إجمالي الزيارة {v[1]} / الفواتير {billed:.2f} غير متطابقة", self.n, p, "high")
        col = {"cash": 2, "card": 3, "credit": 4}[pay]
        if abs(sum(float(x[col]) for x in pays) - billed) > 0.01:
            self.I.add("clinic", "الكشف والحساب", f"الدفع «{pay}» لم يُسجَّل في خانته: {pays}", self.n, None, "high")
        self.steps.append(f"clinic: visit {vid} {billed:.2f} {pay}")

    # ----------------------------------------------------------------- 4c. the owner's reports
    def reports(self):
        p = self.page
        self.goto(p, "/ui/shop/close/", "تقفيل اليوم")
        shown = self.num(p.locator(".kpi.hero .v").first.inner_text())
        try:
            from zoneinfo import ZoneInfo

            tz = ZoneInfo("Africa/Cairo")
        except Exception:  # noqa: BLE001 — no tz data: every order of a fresh copy is from today anyway
            tz = None
        import datetime as dt

        today = dt.datetime.now(tz).date() if tz else None
        net, n = 0.0, 0
        for at, total, ret in self.q("SELECT created_at_device, total, is_return FROM pos_order WHERE cancelled=0"):
            t = dt.datetime.fromisoformat(str(at).replace("Z", "+00:00"))
            if t.tzinfo is None:
                t = t.replace(tzinfo=dt.timezone.utc)
            if tz and t.astimezone(tz).date() != today:
                continue
            net += -float(total) if ret else float(total)
            n += 1
        if shown is None or abs(shown - round(net)) > 1:
            self.I.add("report-day", "تقفيل اليوم", f"«صافي مبيعات اليوم» على الشاشة {shown} ومجموع فواتير اليوم في قاعدة البيانات {net:.2f} ({n} فاتورة)", self.n, p, "high")
        self.steps.append(f"day close: {shown} ({n} orders)")
        self.goto(p, "/ui/shop/profit/", "أرباح الشهر")
        self.goto(p, "/ui/shop/valuation/", "قيمة المخزون")
        cost = self.num(p.locator(".kpi .v").first.inner_text())
        exp = float(self.q1("SELECT COALESCE(SUM(stock_value),0) FROM inventory_item WHERE active=1 AND code<>'0' AND qty_on_hand>0 AND stock_value>0") or 0)
        if cost is None or abs(cost - round(exp)) > 1:
            self.I.add("report-stock", "قيمة المخزون", f"«بسعر التكلفة» على الشاشة {cost} وقيمة الأصناف في الدفاتر {exp:.2f}", self.n, p, "high")

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
            # stock value of the items = the balance of their stock accounts (inventory.services.true_up keeps it so)
            items = dict(c.execute("SELECT cat.stock_account_id, COALESCE(SUM(i.stock_value),0) FROM inventory_item i JOIN inventory_itemcategory cat ON cat.id=i.category_id "
                                   "GROUP BY cat.stock_account_id").fetchall())
            accs = set(items) | {a for (a,) in c.execute("SELECT DISTINCT stock_account_id FROM inventory_itemcategory")}
            for acc in accs:
                gl = c.execute("SELECT COALESCE(SUM(l.debit),0)-COALESCE(SUM(l.credit),0) FROM ledger_journalline l JOIN ledger_journalentry e ON e.id=l.entry_id "
                               "WHERE l.account_id=? AND e.state IN ('posted','reversed')", (acc,)).fetchone()[0]
                iv = items.get(acc, 0)
                if abs(float(gl or 0) - float(iv or 0)) > 0.05:
                    code = c.execute("SELECT code||' '||name FROM ledger_account WHERE id=?", (acc,)).fetchone()
                    worst = c.execute("SELECT code, name, qty_on_hand, stock_value FROM inventory_item WHERE category_id IN "
                                      "(SELECT id FROM inventory_itemcategory WHERE stock_account_id=?) ORDER BY ABS(stock_value) DESC LIMIT 3", (acc,)).fetchall()
                    self.I.add("books-stock", "قيمة المخزون ↔ حساب المخزون", f"حساب {code[0] if code else acc}: الرصيد {float(gl or 0):.2f} وقيمة الأصناف {float(iv or 0):.2f} "
                               f"(الفرق {float(gl or 0) - float(iv or 0):.2f}) — أكبر الأصناف: {worst}", self.n, None, "high")
            # every invoice: what was paid (cash + card + credit) = its total
            off = c.execute("SELECT uid, total, paid_cash, paid_card, paid_credit, flags FROM pos_order WHERE cancelled=0 "
                            "AND ABS(total-(paid_cash+paid_card+paid_credit+paid_rider)) > 0.005").fetchall()
            if off:
                self.I.add("books-pay", "فواتير البيع", f"{len(off)} فاتورة المدفوع فيها ≠ الإجمالي (مثال: {off[0]})", self.n, None, "high")
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
            self.step("تجهيز المحل: الشعار", self.logo)
            self.step("النماذج", self.forms)
            self.step("الكاشير والبيع", self.till)
            if self.act[1] == "cafe":
                self.step("الترابيزات", self.tables)
            if self.act[1] == "gaming":
                self.step("صالة البلايستيشن", self.gaming)
            if self.act[1] == "clinic":
                self.step("العيادة: استقبال وكشف وحساب", self.clinic)
            self.step("فاتورة مشتريات", self.purchase)  # after the sales: the drawer has cash to pay a supplier
            self.step("الجرد والتسوية", self.stocktake)
            self.step("مرتجع مشتريات / هالك", self.returns)
            self.step("التقارير (تقفيل اليوم، الأرباح، قيمة المخزون)", self.reports)
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
