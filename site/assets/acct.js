/* Trio — site accounts, sales-rep links and order requests (1.22).
   - A rep's link (…/?ref=CODE) is remembered in this browser for 90 days; registrations and orders made from here count for him.
   - The account token lives in localStorage (the API is on another origin, so no cookies). */
(function () {
  var FEED = "https://raw.githubusercontent.com/trioerp-corp/trio-feed/main/feed.txt";
  var FALLBACK = "https://trio.trioerp-corp.workers.dev";
  var LS = {
    get: function (k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set: function (k, v) { try { localStorage.setItem(k, v); } catch (e) {} },
    del: function (k) { try { localStorage.removeItem(k); } catch (e) {} }
  };
  var q = new URLSearchParams(location.search), r = (q.get("ref") || "").toUpperCase();
  if (/^[A-Z2-9]{6}$/.test(r)) LS.set("trio_ref", JSON.stringify({ c: r, t: Date.now() }));
  function ref() {
    try { var o = JSON.parse(LS.get("trio_ref") || "null"); if (o && Date.now() - o.t < 90 * 864e5) return o.c; } catch (e) {}
    return "";
  }
  var apiP = null;
  function api() {
    if (!apiP) apiP = fetch(FEED + "?t=" + Math.floor(Date.now() / 3e5)).then(function (x) { return x.text(); }).then(function (t) {
      var m = t.match(/TRIOP\.([A-Za-z0-9_-]+)\./);
      if (!m) return FALLBACK;
      var p = JSON.parse(decodeURIComponent(escape(atob(m[1].replace(/-/g, "+").replace(/_/g, "/")))));
      return /^https:\/\//.test(p.u || "") ? p.u.replace(/\/$/, "") : FALLBACK;
    }).catch(function () { return FALLBACK; });
    return apiP;
  }
  function call(path, body) {
    return api().then(function (base) {
      var h = { "Content-Type": "application/json" }, t = LS.get("trio_tok");
      if (t) h.Authorization = "Bearer " + t;
      return fetch(base + "/site" + path, { method: body ? "POST" : "GET", headers: h, body: body ? JSON.stringify(body) : undefined, keepalive: true })
        .then(function (res) {
          return res.json().catch(function () { return {}; }).then(function (d) {
            if (!res.ok) { var e = new Error(d.error || "تعذّر الاتصال — حاول مرة أخرى"); e.status = res.status; throw e; }
            return d;
          });
        });
    });
  }
  window.TrioAcct = {
    ref: ref, call: call, api: api,
    token: function () { return LS.get("trio_tok"); },
    setToken: function (t) { t ? LS.set("trio_tok", t) : LS.del("trio_tok"); },
    user: function () { try { return JSON.parse(LS.get("trio_user") || "null"); } catch (e) { return null; } },
    setUser: function (u) { u ? LS.set("trio_user", JSON.stringify(u)) : LS.del("trio_user"); }
  };
  // the header button: «حسابي» once signed in
  document.addEventListener("DOMContentLoaded", function () {
    var a = document.getElementById("acctLink"), u = window.TrioAcct.user();
    if (a && u) a.textContent = (a.dataset.me || "حسابي") + " — " + String(u.name || "").split(" ")[0];
    var box = document.getElementById("refNote"), c = ref();
    if (box && c) call("/ref?c=" + c).then(function (d) {
      if (d.ok) {
        box.querySelector("b").textContent = d.name; box.hidden = false; box.classList.remove("hide");
        if (!box.querySelector(".refx")) { // a visitor who did not come through this rep can remove him
          var x = document.createElement("button"); x.type = "button"; x.className = "refx"; x.textContent = "✕ لست عن طريق مندوب";
          x.style.cssText = "border:0;background:none;color:inherit;font:inherit;font-size:.85em;opacity:.75;cursor:pointer;margin-inline-start:6px;text-decoration:underline";
          x.onclick = function () { LS.del("trio_ref"); box.hidden = true; };
          box.appendChild(x);
        }
      }
    }).catch(function () {});
  });
})();
