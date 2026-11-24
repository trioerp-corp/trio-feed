/* Trio — prices in the visitor's own currency (11/2026).
   The table is assets/prices.json (a copy of acc-erp core/prices.json, written by tools/sync_worker_catalog.py).
   Country: ALWAYS the Worker's GET /geo (Cloudflare country of the visitor) — no picker, no stored override.
   /geo failed → one retry → USD ("other country") prices, never Egypt's.
   Fills [data-p="year|branch|visit|custom"], [data-cname] and [data-egp]; fires «trio:price» so the order form rebuilds.
   The card payment is still priced by the Worker from the real country — the page never decides what is charged. */
(function () {
  var AR = ["en", "fr"].indexOf(document.documentElement.lang) < 0, FR = document.documentElement.lang === "fr";
  var FRN = { EG: "Égypte", SA: "Arabie saoudite", AE: "Émirats arabes unis", QA: "Qatar", KW: "Koweït", BH: "Bahreïn", OM: "Oman", JO: "Jordanie",
    IQ: "Irak", MA: "Maroc", DZ: "Algérie", TN: "Tunisie", LY: "Libye", LB: "Liban", SY: "Syrie", PS: "Palestine", YE: "Yémen", SD: "Soudan",
    MR: "Mauritanie", SO: "Somalie", DJ: "Djibouti", KM: "Comores", ZZ: "Autre pays" };
  var LANG = AR ? "ar" : FR ? "fr" : "en";
  function cname(k) {
    var c = T.countries[k];
    if (c && k !== "ZZ") return AR ? c[0] : (FR && FRN[k]) || c[1];
    try { if (k !== "ZZ" && window.Intl && Intl.DisplayNames) return new Intl.DisplayNames([LANG], { type: "region" }).of(k); } catch (e) {}
    c = T.countries.ZZ || ["بلد آخر", "Other country"];
    return AR ? c[0] : FR ? FRN.ZZ : c[1];
  }
  try { localStorage.removeItem("trio_cc"); } catch (e) {} // the old manual pick no longer counts
  var src = (document.currentScript && document.currentScript.src) || "assets/prices.js";
  var T = null;
  var P = window.TrioPrice = {
    country: "", m: null, ready: false,
    p: { year: 0, branch: 0, visit: 0, custom: 0 },
    money: function (n) {
      var m = P.m, s = m ? m[AR ? "ar" : "en"] : "$", v = Number(n || 0).toLocaleString("en-US");
      if (s === "$") return "$" + v;
      return AR ? v + " " + s : s + " " + v;
    },
    egpLine: function () { return ""; }, // the visitor sees his own currency only (owner's choice)
    // what the payment gateway takes for a basket (EGP) — the Worker works it out again from the real country
    egpTotal: function (br, visit, custom) { var e = P.m ? P.m.egp : P.p; return e.year + e.branch * (br || 0) + (visit ? (e.visit || 0) : 0) + (custom ? e.custom : 0); },
    egp: function (n) { return Number(n || 0).toLocaleString("en-US"); },
    name: function () { return T ? cname(P.country) : P.country; }
  };
  function set(cc) {
    cc = /^[A-Z]{2}$/.test(String(cc || "").toUpperCase()) ? String(cc).toUpperCase() : "ZZ";
    P.country = cc;
    P.m = T.markets[(T.countries[cc] || [0, 0, "USD"])[2]] || T.markets.USD;
    P.p = { year: P.m.year, branch: P.m.branch, visit: P.m.visit || 0, custom: P.m.custom };
    paint();
  }
  function paint() {
    document.querySelectorAll("[data-p]").forEach(function (e) {
      var k = e.getAttribute("data-p"), plus = e.hasAttribute("data-plus");
      e.textContent = (plus ? "+" : "") + P.money(P.p[k]);
    });
    document.querySelectorAll("[data-cname]").forEach(function (e) { e.textContent = cname(P.country); });
    document.querySelectorAll("[data-egp]").forEach(function (e) { e.hidden = true; });
    P.ready = true;
    document.dispatchEvent(new CustomEvent("trio:price", { detail: P }));
  }
  // no price is shown until the visitor's country is known (the static figures in the HTML are only for search engines)
  function hush() { document.querySelectorAll("[data-p]").forEach(function (e) { e.textContent = "…"; }); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", function () { if (!P.ready) hush(); }); else hush();
  function geo() {
    if (!window.TrioAcct || !window.TrioAcct.api) return Promise.reject(new Error("no api"));
    return window.TrioAcct.api().then(function (base) { return fetch(base + "/geo", { cache: "no-store" }); })
      .then(function (r) { if (!r.ok) throw new Error("geo " + r.status); return r.json(); })
      .then(function (g) { if (!g || !g.country) throw new Error("no country"); return g.country; });
  }
  fetch(src.replace(/prices\.js(\?.*)?$/, "prices.json")).then(function (r) { return r.json(); }).then(function (t) {
    T = t;
    geo().catch(function () { return new Promise(function (ok) { setTimeout(ok, 1200); }).then(geo); })
      .then(set, function () { set("ZZ"); });
  }).catch(function () {});
})();
