/* Trio — prices in the visitor's own currency (11/2026).
   The table is assets/prices.json (a copy of acc-erp core/prices.json, written by tools/sync_worker_catalog.py).
   Country: the one picked in «بلدك؟» (localStorage trio_cc) > the Worker's GET /geo (Cloudflare country) > Egypt.
   Fills [data-p="year|branch|visit|custom"], select[data-cc] and [data-egp]; fires «trio:price» so the order form rebuilds.
   The card payment is still priced by the Worker from the real country — the page never decides what is charged. */
(function () {
  var AR = document.documentElement.lang !== "en";
  var src = (document.currentScript && document.currentScript.src) || "assets/prices.js";
  var LS = { get: function (k) { try { return localStorage.getItem(k); } catch (e) { return null; } }, set: function (k, v) { try { localStorage.setItem(k, v); } catch (e) {} } };
  var T = null;
  var P = window.TrioPrice = {
    country: "EG", m: null, ready: false,
    p: { year: 3000, branch: 500, visit: 1500, custom: 1500 },
    money: function (n) {
      var m = P.m, s = m ? m[AR ? "ar" : "en"] : (AR ? "ج.م" : "EGP"), v = Number(n || 0).toLocaleString("en-US");
      if (s === "$") return "$" + v;
      return AR ? v + " " + s : s + " " + v;
    },
    egpLine: function () { return ""; }, // the visitor sees his own currency only (owner's choice)
    // what the payment gateway takes for a basket (EGP) — the Worker works it out again from the real country
    egpTotal: function (br, visit, custom) { var e = P.m ? P.m.egp : P.p; return e.year + e.branch * (br || 0) + (visit ? e.visit : 0) + (custom ? e.custom : 0); },
    egp: function (n) { return Number(n || 0).toLocaleString("en-US"); },
    name: function () { var c = T && T.countries[P.country]; return c ? c[AR ? 0 : 1] : P.country; },
    set: function (cc, keep) {
      if (!T) return;
      cc = String(cc || "EG").toUpperCase();
      P.country = cc;
      P.m = T.markets[(T.countries[cc] || [0, 0, "USD"])[2]];
      P.p = { year: P.m.year, branch: P.m.branch, visit: P.m.visit, custom: P.m.custom };
      if (keep) LS.set("trio_cc", cc);
      paint();
    }
  };
  function paint() {
    var m = P.m;
    document.querySelectorAll("[data-p]").forEach(function (e) {
      var k = e.getAttribute("data-p"), plus = e.hasAttribute("data-plus");
      e.textContent = (plus ? "+" : "") + P.money(P.p[k]);
    });
    document.querySelectorAll("select[data-cc]").forEach(function (s) { s.value = T.countries[P.country] ? P.country : "ZZ"; });
    document.querySelectorAll("[data-egp]").forEach(function (e) {
      var eg = m.cur === "EGP";
      e.hidden = eg;
      e.hidden = true;
    });
    P.ready = true;
    document.dispatchEvent(new CustomEvent("trio:price", { detail: P }));
  }
  function pickers() {
    var keys = Object.keys(T.countries);
    document.querySelectorAll("select[data-cc]").forEach(function (s) {
      s.innerHTML = keys.map(function (k) { return '<option value="' + k + '">' + T.countries[k][AR ? 0 : 1] + "</option>"; }).join("");
      s.addEventListener("change", function () { P.set(s.value, true); });
    });
  }
  fetch(src.replace(/prices\.js(\?.*)?$/, "prices.json")).then(function (r) { return r.json(); }).then(function (t) {
    T = t; pickers();
    var saved = LS.get("trio_cc");
    if (saved && /^[A-Z]{2}$/.test(saved)) return P.set(saved);
    P.set("EG");
    if (!window.TrioAcct || !window.TrioAcct.api) return;
    window.TrioAcct.api().then(function (base) { return fetch(base + "/geo"); }).then(function (r) { return r.json(); })
      .then(function (g) { if (g && g.country && !LS.get("trio_cc")) P.set(g.country); }).catch(function () {});
  }).catch(function () {});
})();
