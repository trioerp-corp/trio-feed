// Trio's online address (Cloudflare Worker): الحضور + الدفع بالفيزا (Paymob) + الرخص أوتوماتيك
// KV binding: ACC — Secrets: KEY, phone app: ADMIN_KEY, ADMIN_CERT, GH_TOKEN; and for card payments: SALES_KEY, SALES_CERT, PAYMOB_SECRET, PAYMOB_PUBLIC, PAYMOB_INTEGRATION, PAYMOB_HMAC
// and for the AI (activity set-up + «؟ اسأل»): ANTHROPIC_KEY — Variable (optional): ASK_LIMIT (questions per device per day, 30)
// Variable (optional): SITE = the sales page address (https://trioerp.pages.dev)
// «تابع محلك من الموبايل»: D1 binding DB (recommended; KV works with fewer updates) — ملخص اليوم: Secret TG_TOKEN (Trio's bot,
// then Trio License «الموبايل والتقارير» ← «اربط البوت») and for mail (Brevo): Secret BREVO_KEY + Variable MAIL_FROM
const PUB = "tM+bQlw/40oF17B+DvmHJ1TxQd1oIs0oMTOJ3LxDs4c=";   // Trio's public key: checks the licence a copy already has (upgrades / renewals)
const FEED = "https://raw.githubusercontent.com/trioerp-corp/trio-feed/main/feed.txt"; // the command channel written into every licence
const PAYMOB = "https://accept.paymob.com";
// prices and plans — the same as the program (core/modules.py); the browser never decides the price
const CAT = /*CATALOG*/{"plans": {"basic": 10000, "standard": 15000, "pro": 20000}, "plan_mods": {"basic": ["pos", "items", "purchases", "partners", "money", "treasury", "profit_monthly", "expiry"], "standard": ["pos", "items", "purchases", "partners", "money", "treasury", "profit_monthly", "stores", "returns", "promos", "units", "labels", "expiry", "valuation"], "pro": ["pos", "items", "purchases", "partners", "money", "treasury", "stores", "returns", "promos", "units", "labels", "expiry", "valuation", "production", "recipes", "gaming", "clinic", "branches", "profit_monthly", "profit_yearly", "report_whatsapp", "report_telegram", "report_email", "drive_backup", "mobile"]}, "business": {"market": ["labels", "units"], "retail": [], "cafe": ["recipes"], "gaming": ["gaming", "recipes"], "clinic": ["clinic"]}, "addons": {"production": 1500, "profit_yearly": 1000, "branches": 3000, "report_whatsapp": 500, "report_telegram": 750, "report_email": 750, "drive_backup": 1000, "stores": 1000, "returns": 1000, "promos": 1000, "units": 1000, "labels": 1000, "valuation": 1000, "mobile": 1500}, "for_business": {"gaming": ["gaming"], "recipes": ["cafe", "gaming"], "production": ["market"], "clinic": ["clinic"], "labels": ["market", "retail", "cafe", "gaming"], "units": ["market", "retail", "cafe", "gaming"], "promos": ["market", "retail", "cafe", "gaming"]}, "life": {"renew": 1500, "lifetime": 15000, "lifetime_updates": 10000}, "names": {"pos": "نقطة البيع والكاشير", "items": "الأصناف والمخزن", "purchases": "المشتريات والموردين", "partners": "العملاء الآجل", "money": "المصروفات والمرتبات وصاحب المحل", "treasury": "الخزينة والمحافظ والبنك", "stores": "أكتر من مخزن وتحويلات", "returns": "مرتجع المشتريات للمورد", "promos": "العروض والخصومات", "units": "الوحدات (كرتونة/علبة)", "labels": "ملصقات الباركود والميزان", "expiry": "الصلاحية (الإنتاج والانتهاء)", "valuation": "تقييم المخزون", "production": "التصنيع", "recipes": "المنيو والمكونات", "gaming": "صالة الألعاب بالساعة", "clinic": "العيادة", "branches": "أكتر من فرع", "profit_monthly": "تقرير الأرباح الشهري والشركاء", "profit_yearly": "تقرير الأرباح السنوي", "report_whatsapp": "ملخص اليوم على واتساب", "report_telegram": "ملخص اليوم على تليجرام", "report_email": "ملخص اليوم على الإيميل", "drive_backup": "نسخة احتياطية على جوجل درايف", "mobile": "تابع محلك من الموبايل"}, "plan_names": {"basic": "أساسي", "standard": "متقدم", "pro": "شامل"}}
/*END*/;

const enc = new TextEncoder();
const b64u = (bytes) => btoa(String.fromCharCode(...new Uint8Array(bytes))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const unb64 = (s) => { s = s.replace(/-/g, "+").replace(/_/g, "/"); while (s.length % 4) s += "="; return Uint8Array.from(atob(s), (c) => c.charCodeAt(0)); };
const utf8b64u = (obj) => b64u(enc.encode(JSON.stringify(obj)));
const fromB64u = (s) => JSON.parse(new TextDecoder().decode(unb64(s)));
const cors = { "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "GET,POST,OPTIONS", "Access-Control-Allow-Headers": "Content-Type" };
const json = (o, status = 200) => new Response(JSON.stringify(o), { status, headers: { "Content-Type": "application/json; charset=utf-8", ...cors } });
const err = (msg, status = 400) => json({ error: msg }, status);
const digits = (s) => String(s || "").replace(/\D/g, "");
const MACHINE = /^[A-Z2-7]{4}(-[A-Z2-7]{4}){3}$/;
const iso = () => new Date().toISOString().replace(/\.\d+Z$/, "Z");
const today = () => new Date().toISOString().slice(0, 10);
const plusYear = (d) => { const x = new Date((d > today() ? d : today()) + "T00:00:00Z"); x.setUTCFullYear(x.getUTCFullYear() + 1); return x.toISOString().slice(0, 10); };
const fits = (k, b) => !CAT.for_business[k] || CAT.for_business[k].includes(b);

async function verifyWith(pubB64, payload, sig) {
  const key = await crypto.subtle.importKey("raw", Uint8Array.from(atob(pubB64), (c) => c.charCodeAt(0)), { name: "Ed25519" }, false, ["verify"]);
  return crypto.subtle.verify({ name: "Ed25519" }, key, unb64(sig), enc.encode(payload));
}

// the licence the copy has now (TRIO1 from Trio License, or TRIOS bought online) — null when not genuine
async function readLicence(code) {
  code = String(code || "").replace(/\s/g, "");
  const p = code.split(".");
  try {
    if (p[0] === "TRIO1" && p.length === 3 && (await verifyWith(PUB, p[1], p[2]))) return fromB64u(p[1]);
    if (p[0] === "TRIO1" && p.length === 5 && (await verifyWith(PUB, p[3], p[4]))) {  // made from the phone (admin key certified by the main key)
      const cert = fromB64u(p[3]);
      if (cert.use === "admin" && (await verifyWith(cert.k, p[1], p[2]))) return fromB64u(p[1]);
    }
    if (p[0] === "TRIOS" && p.length === 5 && (await verifyWith(PUB, p[3], p[4]))) {
      const cert = fromB64u(p[3]);
      if (cert.use === "sales" && (await verifyWith(cert.k, p[1], p[2]))) return fromB64u(p[1]);
    }
  } catch (e) {}
  return null;
}

async function sign(env, lic) {
  const jwk = JSON.parse(env.SALES_KEY);
  const key = await crypto.subtle.importKey("jwk", jwk, { name: "Ed25519" }, false, ["sign"]);
  const payload = utf8b64u(lic);
  const sig = b64u(await crypto.subtle.sign({ name: "Ed25519" }, key, enc.encode(payload)));
  return "TRIOS." + payload + "." + sig + "." + String(env.SALES_CERT).trim().replace(/^[A-Z0-9]+\./, "");
}

// what is bought and for how much (EGP), worked out here only
async function price(b) {
  const business = CAT.business[b.business] ? b.business : "market";
  const lines = [];
  let total = 0, cur = null;
  if (b.lic) {
    cur = await readLicence(b.lic);
    if (!cur || cur.m !== b.m) return { error: "الرخصة اللي على الجهاز ده مش سليمة — اطلب واتساب" };
  }
  const plan = CAT.plans[b.plan] ? b.plan : "";
  if (!cur) {
    if (!plan) return { error: "اختار الباقة" };
    lines.push(["باقة " + CAT.plan_names[plan], CAT.plans[plan]]); total += CAT.plans[plan];
  } else if (plan && plan !== cur.p) {
    if (!CAT.plans[cur.p]) return { error: "الترقية دي محتاجة Trio — اطلبها واتساب" };
    const diff = CAT.plans[plan] - CAT.plans[cur.p];
    if (diff <= 0) return { error: "دي مش ترقية — اختار باقة أعلى" };
    lines.push(["ترقية لباقة " + CAT.plan_names[plan], diff]); total += diff;
  }
  const newPlan = plan || (cur && cur.p) || "basic";
  const inPlan = new Set([...(CAT.plan_mods[newPlan] || []), ...((CAT.business[business] || [])), ...((cur && cur.x) || [])]);
  const extras = [];
  for (const k of [...new Set(b.extras || [])]) {
    if (!(k in CAT.addons) || !fits(k, business) || inPlan.has(k)) continue;
    extras.push(k); lines.push([CAT.names[k], CAT.addons[k]]); total += CAT.addons[k];
  }
  const life = new Set((b.life || []).filter((k) => k === "lifetime" || k === "lifetime_updates"));
  if (cur && cur.l) life.delete("lifetime");
  if (cur && cur.u) life.delete("lifetime_updates");
  for (const k of life) { lines.push([k === "lifetime" ? "رخصة مدى الحياة" : "التحديثات مدى الحياة", CAT.life[k]]); total += CAT.life[k]; }
  const renew = !!(cur && b.renew && !cur.l && !life.has("lifetime"));
  if (renew) { lines.push(["تجديد سنة", CAT.life.renew]); total += CAT.life.renew; }
  if (total <= 0) return { error: "مفيش حاجة تتدفع" };
  return { business, plan: newPlan, extras, life: [...life], renew, lines, total, cur };
}

function licence(o, m, selfSetup) {
  const cur = o.cur || {};
  const lic = { ...cur };
  delete lic._k;
  Object.assign(lic, { c: cur.c || o.name || "عميل أونلاين", m, p: o.plan, b: o.business, x: [...new Set([...(cur.x || []), ...o.extras])], g: cur.g || 7, f: cur.f || FEED, t: iso(), o: o.id });
  if (o.life.includes("lifetime") || cur.l) { lic.l = true; delete lic.e; }
  else lic.e = o.cur ? (o.renew ? plusYear(cur.e || today()) : cur.e || plusYear(today())) : plusYear(today());
  if (o.life.includes("lifetime_updates") || cur.u) lic.u = true;
  if (selfSetup) lic.s = true;
  return lic;
}

async function getOrder(env, id) { const v = await env.ACC.get("o:" + id); return v ? JSON.parse(v) : null; }
const putOrder = (env, o) => env.ACC.put("o:" + o.id, JSON.stringify(o));

const HMAC_FIELDS = ["amount_cents", "created_at", "currency", "error_occured", "has_parent_transaction", "id", "integration_id", "is_3d_secure", "is_auth",
  "is_capture", "is_refunded", "is_standalone_payment", "is_voided", "order.id", "owner", "pending", "source_data.pan", "source_data.sub_type", "source_data.type", "success"];
const pick = (o, path) => path.split(".").reduce((a, k) => (a == null ? a : a[k]), o);
async function hmacOK(env, values, given) {
  const key = await crypto.subtle.importKey("raw", enc.encode(env.PAYMOB_HMAC || ""), { name: "HMAC", hash: "SHA-512" }, false, ["sign"]);
  const mac = new Uint8Array(await crypto.subtle.sign("HMAC", key, enc.encode(values.join(""))));
  const hex = [...mac].map((x) => x.toString(16).padStart(2, "0")).join("");
  return !!given && hex === String(given).toLowerCase();
}

// a confirmed transaction (the webhook, or the browser coming back) → the order is paid → the licence
async function settle(env, t) {
  const ref = String(pick(t, "order.merchant_order_id") || "");
  let id = ref.split("_")[0];
  if (!id) id = (await env.ACC.get("p:" + pick(t, "order.id"))) || "";
  const o = id && (await getOrder(env, id));
  if (!o || o.status === "paid") return o;
  const ok = t.success === true || t.success === "true";
  const bad = [t.pending, t.is_voided, t.is_refunded].some((x) => x === true || x === "true");
  if (ok && !bad && Number(t.amount_cents) === o.total * 100 && String(t.currency || "EGP") === "EGP") {
    o.status = "paid"; o.paid_at = iso(); o.txn = String(t.id || "");
    if (o.m) o.code = await sign(env, licence(o, o.m, false));
  } else if (!ok && !t.pending) {
    o.status = "failed"; o.reason = String(pick(t, "data.message") || "");
  }
  await putOrder(env, o);
  return o;
}

async function checkout(req, env, url) {
  if (!env.SALES_KEY || !env.PAYMOB_SECRET) return err("الدفع أونلاين مش متاح دلوقتي", 503);
  let b;
  try { b = await req.json(); } catch (e) { return err("طلب ناقص"); }
  if (b.m && !MACHINE.test(b.m)) return err("كود الجهاز غلط");
  const phone = digits(b.phone);
  if (phone.length < 10) return err("اكتب رقم موبايل صحيح");
  const name = String(b.name || "").trim().slice(0, 100);
  if (!name) return err("اكتب اسم المحل");
  const agree = String(b.agree || "").slice(0, 10);
  if (!b.m && !agree) return err("لازم توافق على الشروط والأحكام وسياسة الاسترجاع قبل الدفع");
  const pr = await price(b);
  if (pr.error) return err(pr.error);
  const id = "TRIO-" + b64u(crypto.getRandomValues(new Uint8Array(5))).toUpperCase().replace(/[^A-Z0-9]/g, "7").slice(0, 6);
  const email = /^[^@\s]+@[^@\s]+\.[a-z]{2,}$/i.test(b.email || "") ? b.email : phone + "@trioerp.pages.dev";
  const back = /^http:\/\/[a-z0-9.\-]+(:\d+)?\/ui\/shop\/pay\/$/i.test(b.back || "") ? b.back : "";
  const o = { id, status: "pending", src: b.m ? "app" : "site", m: b.m || "", name, phone, email, business: pr.business, plan: pr.plan, extras: pr.extras,
    life: pr.life, renew: pr.renew, lines: pr.lines, total: pr.total, cur: pr.cur, back, created: iso(),
    terms: agree ? { v: agree, at: iso() } : null }; // the terms / refund policy version the customer ticked
  // where the owner wants his daily summary (filled on the order form; the program saves them when the order is claimed)
  const repMail = /^[^@\s]+@[^@\s]+\.[a-z]{2,}$/i.test(String(b.rep_email || "").trim()) ? String(b.rep_email).trim().slice(0, 120) : "";
  const repWa = digits(b.rep_wa).slice(0, 15);
  if (repMail || repWa) o.rep = { email: repMail, wa: repWa };
  const [first, ...rest] = name.split(/\s+/);
  const body = {
    amount: pr.total * 100, currency: "EGP",
    payment_methods: String(env.PAYMOB_INTEGRATION).split(",").map((x) => parseInt(x, 10)).filter(Boolean),
    items: [{ name: "Trio", amount: pr.total * 100, description: pr.lines.map((l) => l[0]).join(" + ").slice(0, 250), quantity: 1 }],
    billing_data: { first_name: first || "Trio", last_name: rest.join(" ") || "Customer", phone_number: "+2" + phone.replace(/^2/, ""), email, apartment: "NA", floor: "NA",
      street: "NA", building: "NA", shipping_method: "NA", postal_code: "NA", city: "NA", country: "EG", state: "NA" },
    special_reference: id + "_" + Date.now(),
    notification_url: url.origin + "/paymob", redirection_url: url.origin + "/back?o=" + id,
  };
  const r = await fetch((env.PAYMOB_API || PAYMOB) + "/v1/intention/", { method: "POST", headers: { Authorization: "Token " + env.PAYMOB_SECRET, "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const d = await r.json().catch(() => ({}));
  if (!r.ok || !d.client_secret) return err("Paymob مش بترد دلوقتي — جرّب كمان شوية", 502);
  o.pm = String(d.intention_order_id || "");
  if (o.pm) await env.ACC.put("p:" + o.pm, id);
  await putOrder(env, o);
  return json({ id, amount: pr.total, lines: pr.lines,
    url: (env.PAYMOB_CHECKOUT || PAYMOB) + "/unifiedcheckout/?publicKey=" + encodeURIComponent(env.PAYMOB_PUBLIC) + "&clientSecret=" + encodeURIComponent(d.client_secret) });
}


// ---------------------------------------------------------------- the AI (Claude): activities + «؟ اسأل»
const AI = "https://api.anthropic.com";
const PROFILE_MODEL = "claude-sonnet-5-5", ASK_MODEL = "claude-haiku-5-5";
const normAr = (t) => String(t || "").toLowerCase().replace(/[ً-ْـ]/g, "").replace(/[أإآٱ]/g, "ا").replace(/ة/g, "ه").replace(/ى/g, "ي")
  .replace(/[^\p{L}\p{N}\s]/gu, " ").replace(/\s+/g, " ").trim();
async function bump(env, key, ttl) { const n = parseInt((await env.ACC.get(key)) || "0", 10) + 1; await env.ACC.put(key, String(n), { expirationTtl: ttl }); return n; }
async function claude(env, model, system, user, tool, maxTokens) {
  const r = await fetch((env.AI_API || AI) + "/v1/messages", {
    method: "POST", headers: { "x-api-key": env.ANTHROPIC_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json" },
    body: JSON.stringify({ model, max_tokens: maxTokens, system, messages: [{ role: "user", content: user }], tools: [tool], tool_choice: { type: "tool", name: tool.name } }),
  });
  if (!r.ok) throw new Error("ai " + r.status);
  const d = await r.json();
  const use = (d.content || []).find((c) => c.type === "tool_use");
  if (!use) throw new Error("ai: no answer");
  return use.input;
}
const PROFILE_TOOL = { name: "activity_profile", description: "The set-up of Trio for one shop activity", input_schema: { type: "object", required: ["name", "base", "categories", "units", "fields"], properties: {
  name: { type: "string", description: "اسم النشاط بالعربي المصري، قصير" },
  base: { type: "string", enum: ["market", "retail", "cafe", "gaming"], description: "market=بيع بالكيلو والباركود والميزان، retail=محل بيع قطع/منتجات، cafe=مشروبات وأكل بوصفات، gaming=أجهزة بالساعة" },
  categories: { type: "array", items: { type: "string" }, maxItems: 12, description: "تصنيفات الأصناف (6–10) بالعربي" },
  units: { type: "array", items: { type: "string" }, maxItems: 6, description: "وحدات البيع (قطعة، كيلو، متر…)" },
  fields: { type: "array", maxItems: 5, items: { type: "object", required: ["k", "n"], properties: { k: { type: "string", description: "english_snake_case key" }, n: { type: "string", description: "اسم الخانة بالعربي" }, s: { type: "boolean", description: "يتدوّر بيها في الكاشير" } } },
    description: "بيانات زيادة لكل صنف يحتاجها النشاط ده بس (رقم القطعة، المقاس، الماركة، الموديل…) — 0 لـ 5" },
  item: { type: "string", description: "كلمة «صنف» في النشاط ده (مفرد)" }, items: { type: "string", description: "الجمع" },
  suggest: { type: "array", items: { type: "string", enum: Object.keys(CAT.names) }, maxItems: 5, description: "أجزاء البرنامج اللي تفيد النشاط ده" },
  plan: { type: "string", enum: ["basic", "standard", "pro"], description: "الباقة المناسبة: basic لمحل صغير، standard لمحل فيه وحدات/عروض/مرتجع/أكتر من مخزن، pro للفروع" },
  samples: { type: "array", maxItems: 6, items: { type: "object", required: ["name", "price"], properties: { name: { type: "string" }, price: { type: "number", description: "سعر تقريبي بالجنيه المصري" }, category: { type: "string", description: "واحد من categories" },
    extra: { type: "object", description: "قيم البيانات الزيادة (بنفس مفاتيح fields)" } } }, description: "6 أصناف أمثلة حقيقية من النشاط بأسعار تقريبية" },
} } };
async function profile(req, env) {
  if (!env.ANTHROPIC_KEY) return err("الذكاء الاصطناعي مش متاح", 503);
  let b = {};
  try { b = await req.json(); } catch (e) { return err("طلب ناقص"); }
  const text = String(b.text || "").slice(0, 200).trim(), key = "bp:" + normAr(text).slice(0, 80);
  if (text.length < 2) return err("اكتب النشاط");
  const cached = await env.ACC.get(key);
  if (cached) return json({ profile: JSON.parse(cached).profile, cached: true });
  if (!MACHINE.test(b.m || "") || (await bump(env, "rl:p:" + b.m + ":" + today(), 90000)) > 6) return err("حاول كمان شوية", 429);
  if ((await bump(env, "rl:ai:" + today(), 90000)) > Number(env.AI_DAILY_CAP || 1500)) return err("حاول بكره", 429);
  const names = Object.entries(CAT.names).map(([k, v]) => k + " = " + v).join("\n");
  const system = "انت بتجهّز برنامج Trio (مبيعات ومخازن وحسابات للمحلات في مصر) على مقاس نشاط محل. اختار من اللي البرنامج عنده بس: النوع الأساسي، التصنيفات، الوحدات، وبيانات زيادة للصنف. " +
    "الكلام بالعربي المصري البسيط اللي أصحاب المحلات بيستخدموه. لو الكلام مش نشاط محل حقيقي، رجّع أقرب نشاط عام. أجزاء البرنامج:\n" + names;
  let p;
  try { p = await claude(env, PROFILE_MODEL, system, "نشاط المحل: " + text, PROFILE_TOOL, 1600); }
  catch (e) { return err("الذكاء الاصطناعي مش بيرد دلوقتي", 502); }
  p.key = "ai_" + normAr(text).replace(/\s/g, "_").slice(0, 24);
  await env.ACC.put(key, JSON.stringify({ profile: p, text, m: b.m, at: iso() }));
  await bump(env, "us:p:" + today().slice(0, 7), 40 * 86400);
  return json({ profile: p });
}
const ASK_TOOL = { name: "answer", description: "The answer shown to the shop user", input_schema: { type: "object", required: ["answer"], properties: {
  answer: { type: "string", description: "الرد بالعربي المصري: خطوات قصيرة مرقمة لو فيه خطوات، من غير مقدمات" },
  links: { type: "array", items: { type: "string" }, maxItems: 3, description: "عناوين الصفحات (url من القايمة بس) اللي تفيده" } } } };
async function ask(req, env) {
  if (!env.ANTHROPIC_KEY) return err("المساعد مش متاح دلوقتي", 503);
  let b = {};
  try { b = await req.json(); } catch (e) { return err("طلب ناقص"); }
  if (!MACHINE.test(b.m || "")) return err("طلب ناقص");
  const q = String(b.q || "").slice(0, 500).trim();
  if (q.length < 2) return err("اكتب سؤالك");
  // a made-up machine code is free, so the whole service also has a daily ceiling (AI_DAILY_CAP) — the bill can never run away
  if ((await bump(env, "rl:ai:" + today(), 90000)) > Number(env.AI_DAILY_CAP || 1500)) return err("المساعد مشغول النهارده — جرّب بكره", 429);
  const limit = parseInt(env.ASK_LIMIT || "30", 10), n = await bump(env, "rl:a:" + b.m + ":" + today(), 90000);
  if (n > limit) return err("خلصت أسئلة النهارده (" + limit + ") — جرّب بكره أو كلّم Trio 01008030459", 429);
  const cut = (a, k) => (Array.isArray(a) ? a.slice(0, k) : []);
  const pages = cut(b.pages, 60).map((p) => "- " + String(p.name || "").slice(0, 60) + " → " + String(p.url || "").slice(0, 80)).join("\n");
  const topics = cut(b.topics, 8).map((t) => "## " + String(t.t || "").slice(0, 80) + "\n" + String(t.a || "").slice(0, 900)).join("\n\n");
  const system = "انت «مساعد Trio» جوه برنامج مبيعات ومخازن لمحل في مصر. بترد بالعربي المصري البسيط على أسئلة استخدام البرنامج بس: الحاجة دي فين وبتتعمل إزاي. " +
    "استخدم الصفحات والشرح اللي تحت بس، وماتخترعش شاشات أو أزرار مش موجودة. لو حاجة مش موجودة في الأجزاء المفتوحة عنده قوله إنها ممكن تتضاف من «⭐ ترقية وطلبات». " +
    "لو السؤال مش عن البرنامج اعتذر بلطف. ماتطلبش بيانات سرية. خلي الرد قصير.\n\n" +
    "النشاط: " + String(b.activity || "").slice(0, 80) + " — دوره: " + String(b.role || "").slice(0, 30) + " — الصفحة اللي هو فيها: " + String(b.page || "").slice(0, 80) +
    "\nالأجزاء المفتوحة: " + cut(b.mods, 40).map((x) => String(x).slice(0, 40)).join("، ") + "\n\nالصفحات:\n" + pages + "\n\nشرح من دليل البرنامج:\n" + topics;
  let a;
  try { a = await claude(env, ASK_MODEL, system, q, ASK_TOOL, 700); }
  catch (e) { return err("المساعد مش بيرد دلوقتي — جرّب كمان شوية", 502); }
  await bump(env, "us:a:" + today().slice(0, 7), 40 * 86400);
  const ok = new Set(cut(b.pages, 60).map((p) => String(p.url || "")));
  return json({ answer: String(a.answer || "").slice(0, 3000), links: cut(a.links, 3).filter((u) => ok.has(u)), left: Math.max(0, limit - n) });
}
const ICONS = { "192": "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", "512": "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", "180": "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" };


// ---------------------------------------------------------------- «تابع محلك من الموبايل» + ملخص اليوم (Trio's Telegram bot and mail)
// Each shop has one secret (k). The phone page lives at /m/<k>; the data is stored under sid = sha256(k) so the
// store never holds the secret itself. Storage: D1 (binding DB, recommended: many writes a day) or KV (fewer updates).
const KEYRE = /^[A-Za-z0-9]{16,40}$/;
async function sidOf(k) { const h = new Uint8Array(await crypto.subtle.digest("SHA-256", enc.encode("acc-m:" + k))); return [...h.slice(0, 12)].map((x) => x.toString(16).padStart(2, "0")).join(""); }
let dbReady = false;
async function db(env) {
  if (!env.DB) return null;
  if (!dbReady) { await env.DB.exec("CREATE TABLE IF NOT EXISTS snaps (id TEXT PRIMARY KEY, d TEXT, at TEXT, m TEXT, v TEXT)"); dbReady = true; }
  return env.DB;
}
async function snapGet(env, id) {
  const d = await db(env);
  if (d) { const r = await d.prepare("SELECT d, at, v FROM snaps WHERE id = ?").bind(id).first(); return r ? { d: JSON.parse(r.d), at: r.at, v: r.v } : null; }
  const v = await env.ACC.get("s:" + id); return v ? JSON.parse(v) : null;
}
async function snapPut(env, id, rec, m) {
  const d = await db(env);
  if (d) return d.prepare("INSERT INTO snaps (id, d, at, m, v) VALUES (?1, ?2, ?3, ?4, ?5) ON CONFLICT(id) DO UPDATE SET d = ?2, at = ?3, m = ?4, v = ?5")
    .bind(id, JSON.stringify(rec.d), rec.at, m, rec.v).run();
  return env.ACC.put("s:" + id, JSON.stringify(rec), { expirationTtl: 45 * 86400 });
}
async function snap(req, env) {
  let b = {};
  try { b = await req.json(); } catch (e) { return err("طلب ناقص"); }
  if (!KEYRE.test(b.k || "") || !MACHINE.test(b.m || "") || typeof b.d !== "object") return err("طلب ناقص");
  const body = JSON.stringify(b.d);
  if (body.length > 60000) return err("البيانات كبيرة", 413);
  const id = await sidOf(b.k), prev = await snapGet(env, id);
  const gap = env.DB ? 120 : 1100; // seconds: KV allows few writes a day, so it updates less often
  if (prev && Date.now() - Date.parse(prev.at) < gap * 1000) return json({ ok: true, skipped: true });
  await snapPut(env, id, { d: b.d, at: iso(), v: String(b.v || "").slice(0, 20) }, b.m);
  return json({ ok: true });
}

const TG = "https://api.telegram.org";
async function tgCall(env, method, body) {
  const r = await fetch((env.TG_API || TG) + "/bot" + env.TG_TOKEN + "/" + method, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  return r.json().catch(() => ({ ok: false }));
}
async function tgBot(env) { return env.TG_BOT || (await env.ACC.get("cfg:tgbot")) || ""; }
async function tgChats(env, id) { const v = await env.ACC.get("tg:" + id); return v ? JSON.parse(v) : []; }
async function tgHook(req, env) {
  if (!env.TG_TOKEN || req.headers.get("X-Telegram-Bot-Api-Secret-Token") !== (await tgSecret(env))) return new Response("no", { status: 401 });
  let u = {};
  try { u = await req.json(); } catch (e) { return new Response("ok"); }
  const msg = u.message || {}, chat = msg.chat && msg.chat.id, text = String(msg.text || "").trim();
  if (!chat) return new Response("ok");
  const reply = (t) => tgCall(env, "sendMessage", { chat_id: chat, text: t });
  const m = text.match(/^\/start(?:\s+([A-Za-z0-9]{16,40}))?$/);
  if (m && m[1]) {
    const id = await sidOf(m[1]), list = await tgChats(env, id);
    if (!list.includes(chat)) { if (list.length >= 5) { await reply("الحد 5 حسابات للمحل — شيل حساب الأول بـ /stop منه."); return new Response("ok"); } list.push(chat); }
    await env.ACC.put("tg:" + id, JSON.stringify(list));
    await env.ACC.put("tgc:" + chat, id);
    const s = await snapGet(env, id);
    await reply("✔ اتربطت" + (s && s.d && s.d.shop ? " بـ «" + s.d.shop + "»" : "") + " — ملخص اليوم هيوصلك هنا كل يوم من Trio.\nلإيقافه ابعت /stop");
  } else if (/^\/stop/.test(text)) {
    const id = await env.ACC.get("tgc:" + chat);
    if (id) { await env.ACC.put("tg:" + id, JSON.stringify((await tgChats(env, id)).filter((c) => c !== chat))); await env.ACC.delete("tgc:" + chat); }
    await reply("اتوقف ✔ — مش هيوصلك ملخص تاني. ترجعه من «📱 الموبايل والتقارير» في البرنامج.");
  } else {
    await reply("أهلًا 👋 ده بوت Trio لملخص اليوم. اربطه من البرنامج: «📱 الموبايل والتقارير» ← «افتح تليجرام واربط».\nTrio · 01008030459");
  }
  return new Response("ok");
}
async function tgSecret(env) { const h = new Uint8Array(await crypto.subtle.digest("SHA-256", enc.encode("tg:" + (env.TG_TOKEN || "")))); return [...h.slice(0, 16)].map((x) => x.toString(16).padStart(2, "0")).join(""); }
async function tgSetup(env, url) {
  if (!env.TG_TOKEN) return err("حط TG_TOKEN في الـ Worker الأول");
  const me = await tgCall(env, "getMe", {});
  if (!me.ok) return err("توكن البوت غلط");
  await env.ACC.put("cfg:tgbot", me.result.username);
  const hook = await tgCall(env, "setWebhook", { url: url.origin + "/tg", secret_token: await tgSecret(env), allowed_updates: ["message"], drop_pending_updates: true });
  return json({ ok: !!hook.ok, bot: me.result.username, error: hook.ok ? "" : hook.description || "" });
}

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
async function mail(env, to, subject, text, shop) {
  const html = `<div dir="rtl" style="font-family:Tahoma,Arial,sans-serif;font-size:15px;line-height:1.8;color:#0B1F3A;max-width:560px">
<div style="background:#17698F;color:#fff;padding:14px 18px;border-radius:12px 12px 0 0;font-weight:bold">${esc(subject)}</div>
<div style="border:1px solid #DFE6EF;border-top:0;padding:16px 18px;border-radius:0 0 12px 12px;white-space:pre-line">${esc(text)}</div>
<p style="color:#66788F;font-size:12px">اتبعت من Trio لـ ${esc(shop)} · 01008030459</p></div>`;
  const r = await fetch(env.MAIL_API || "https://api.brevo.com/v3/smtp/email", {
    method: "POST", headers: { "api-key": env.BREVO_KEY, "Content-Type": "application/json", accept: "application/json" },
    body: JSON.stringify({ sender: { name: "Trio", email: env.MAIL_FROM }, to: to.map((e) => ({ email: e })), subject, textContent: text + "\n\npowered by Trio", htmlContent: html }),
  });
  if (!r.ok) throw new Error("mail " + r.status);
}
async function notify(req, env) {
  let b = {};
  try { b = await req.json(); } catch (e) { return err("طلب ناقص"); }
  if (!KEYRE.test(b.k || "") || !MACHINE.test(b.m || "")) return err("طلب ناقص");
  const id = await sidOf(b.k);
  if ((await bump(env, "rl:n:" + id + ":" + today(), 90000)) > 8) return err("اتبعت كتير النهارده — بكره", 429);
  const text = String(b.text || "").slice(0, 3500), subject = String(b.subject || "ملخص اليوم").slice(0, 150), shop = String(b.shop || "").slice(0, 100);
  if (!text) return err("مفيش نص");
  const out = {};
  if (b.tg) {
    if (!env.TG_TOKEN) out.tg = "تليجرام مش متاح من Trio لسه";
    else {
      const chats = await tgChats(env, id);
      if (!chats.length) out.tg = "لسه محدش ربط تليجرام — «📱 الموبايل والتقارير» ← «افتح تليجرام واربط»";
      else { let ok = 0; for (const c of chats) ok += (await tgCall(env, "sendMessage", { chat_id: c, text })).ok ? 1 : 0; out.tg = ok ? "ok" : "تليجرام رفض الرسالة"; }
    }
  }
  if (b.email) {
    // mail leaves in Trio's name, so only a copy that holds a genuine licence for its own machine may ask for it (never an open relay)
    const lic = await readLicence(b.lic);
    if (!lic || lic.m !== b.m || (lic.e && !lic.l && lic.e < today())) { out.mail = "الإيميل من Trio للنسخ المفعّلة بس — فعّل الرخصة أو استخدم إيميل المحل"; return json(out); }
    if ((await bump(env, "rl:mail:" + today(), 90000)) > Number(env.MAIL_DAILY_CAP || 250)) { out.mail = "الإيميل وصل حد اليوم — بكره"; return json(out); }
    const to = String(b.email).split(",").map((x) => x.trim()).filter((x) => /^[^@\s]+@[^@\s]+\.[a-z]{2,}$/i.test(x)).slice(0, 3);
    if (!to.length) out.mail = "الإيميل مكتوب غلط";
    else if (!(env.BREVO_KEY && env.MAIL_FROM)) out.mail = "الإيميل مش متاح من Trio لسه";
    else { try { await mail(env, to, subject, text, shop); out.mail = "ok"; } catch (e) { out.mail = "الإيميل مااتبعتش — جرّب كمان شوية"; } }
  }
  return json(out);
}

function png(b64) { return new Response(Uint8Array.from(atob(b64), (c) => c.charCodeAt(0)), { headers: { "Content-Type": "image/png", "Cache-Control": "public, max-age=604800" } }); }
function manifest(k, shop) {
  return new Response(JSON.stringify({
    name: "Trio" + (shop ? " — " + shop : ""), short_name: "Trio", description: "تابع محلك من الموبايل", lang: "ar", dir: "rtl",
    start_url: "/m/" + k, scope: "/m/" + k, display: "standalone", background_color: "#0A3A66", theme_color: "#17698F",
    icons: [{ src: "/icon-192.png", sizes: "192x192", type: "image/png" }, { src: "/icon-512.png", sizes: "512x512", type: "image/png" },
      { src: "/icon-maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" }],
  }), { headers: { "Content-Type": "application/manifest+json; charset=utf-8" } });
}
const SW = `self.addEventListener("install",e=>self.skipWaiting());self.addEventListener("activate",e=>e.waitUntil(clients.claim()));
self.addEventListener("fetch",e=>{if(e.request.method!=="GET")return;e.respondWith(fetch(e.request).then(r=>{if(r.ok){const c=r.clone();caches.open("acc-m").then(x=>x.put(e.request,c))}return r}).catch(()=>caches.match(e.request)))});`;

function phone(k, shop) {
  const t = esc(shop || "محلك");
  return new Response(`<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><title>Trio — ${t}</title>
<link rel="manifest" href="/m/${k}/manifest.webmanifest"><meta name="theme-color" content="#17698F">
<link rel="icon" href="/icon-192.png"><link rel="apple-touch-icon" href="/apple-touch-icon.png">
<meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-title" content="Trio"><meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="robots" content="noindex,nofollow"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Cairo:wght@400;600;700;800&display=swap" rel="stylesheet">
<style>
:root{--bg:#F3F6FA;--card:#fff;--ink:#0B1F3A;--mut:#58697F;--line:#DFE6EF;--brand:#17698F;--brand2:#0A3A66;--gold:#3CC2B5;--good:#1F8A52;--bad:#C2410C}
@media (prefers-color-scheme:dark){:root{--bg:#07111E;--card:#0F1C2E;--ink:#E6EEF8;--mut:#9FB2C8;--line:#1F3350}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:Cairo,system-ui,sans-serif;-webkit-tap-highlight-color:transparent}
header{background:linear-gradient(160deg,var(--brand),var(--brand2));color:#fff;padding:calc(14px + env(safe-area-inset-top)) 16px 54px}
.top{display:flex;align-items:center;gap:10px;max-width:640px;margin:auto}.top img{width:38px;height:38px;border-radius:10px}
.top h1{font-size:18px;margin:0;line-height:1.3}.top small{display:block;opacity:.85;font-size:12.5px;font-weight:400}
.st{display:inline-flex;align-items:center;gap:6px}.st i{width:8px;height:8px;border-radius:50%;background:#7CE3A6;box-shadow:0 0 0 3px rgba(124,227,166,.25)}.st.off i{background:#F2C14E;box-shadow:0 0 0 3px rgba(242,193,78,.25)}
main{max-width:640px;margin:-42px auto 0;padding:0 12px calc(24px + env(safe-area-inset-bottom))}
.hero{background:var(--card);border-radius:20px;padding:18px;box-shadow:0 8px 24px rgba(11,31,58,.10)}
.hero .k{color:var(--mut);font-size:13px}.hero .v{font-size:34px;font-weight:800;letter-spacing:-.5px}.hero .v small{font-size:15px;color:var(--mut);font-weight:600}
.row{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:12px}.row div{background:var(--bg);border-radius:12px;padding:8px 10px}.row b{display:block;font-size:15px}.row span{font-size:11.5px;color:var(--mut)}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:12px}.tile{background:var(--card);border-radius:16px;padding:14px;border:1px solid var(--line)}
.tile .k{font-size:12.5px;color:var(--mut)}.tile .v{font-size:21px;font-weight:800}.tile.warn .v{color:var(--bad)}
section{background:var(--card);border-radius:16px;border:1px solid var(--line);margin-top:12px;overflow:hidden}
section h2{font-size:15px;margin:0;padding:12px 14px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;align-items:center}
section h2 em{font-style:normal;font-size:12px;color:var(--mut);font-weight:600}
ul{list-style:none;margin:0;padding:0}li{display:flex;justify-content:space-between;gap:10px;padding:10px 14px;border-bottom:1px solid var(--line);font-size:14px}li:last-child{border:0}
li span:last-child{font-weight:700;white-space:nowrap}li .s{color:var(--mut);font-size:12px;font-weight:400}.empty{padding:14px;color:var(--mut);font-size:14px}
.tag{font-size:11px;border-radius:6px;padding:1px 6px;background:var(--bg);color:var(--mut);margin-inline-start:6px}.tag.r{color:var(--bad)}
.acts{display:flex;gap:8px;margin-top:14px;flex-wrap:wrap}.btn{flex:1;min-width:140px;border:0;border-radius:14px;padding:13px;font:700 15px Cairo,system-ui;background:var(--brand);color:#fff;text-decoration:none;text-align:center;cursor:pointer}
.btn.l{background:var(--card);color:var(--brand);border:1.5px solid var(--brand)}
.tip{background:var(--card);border:1px dashed var(--gold);border-radius:14px;padding:12px 14px;margin-top:12px;font-size:13.5px;display:none}.tip b{color:var(--gold)}
footer{text-align:center;color:var(--mut);font-size:12px;margin-top:18px}.load{padding:40px;text-align:center;color:var(--mut)}
nav.bt{position:fixed;bottom:0;inset-inline:0;background:var(--card);border-top:1px solid var(--line);display:flex;padding:6px 4px calc(6px + env(safe-area-inset-bottom));z-index:9}
nav.bt a{flex:1;text-align:center;color:var(--mut);text-decoration:none;font-size:11.5px;padding:2px 0;position:relative;cursor:pointer}nav.bt a i{display:block;font-style:normal;font-size:21px}nav.bt a.on{color:var(--brand);font-weight:700}
nav.bt a em{position:absolute;top:-2px;inset-inline-start:calc(50% + 6px);background:var(--bad);color:#fff;font-style:normal;font-size:11px;border-radius:9px;padding:0 6px}
body{padding-bottom:70px}.view[hidden]{display:none}.f{background:var(--card);border-radius:18px;padding:16px;box-shadow:0 8px 24px rgba(11,31,58,.10)}
.f h2{font-size:17px;margin:0 0 10px}.f label{display:block;font-size:13px;color:var(--mut);margin:10px 0 4px}
.f input,.f select{width:100%;font:16px Cairo,system-ui;padding:11px 12px;border:1.5px solid var(--line);border-radius:12px;background:var(--bg);color:var(--ink)}
.f .btn{width:100%;margin-top:14px}.note{font-size:12.5px;color:var(--mut);margin:10px 0 0}.ok{color:var(--good)}.no{color:var(--bad)}
.ln{display:grid;grid-template-columns:1fr 70px 86px 30px;gap:6px;align-items:end;margin-top:8px}.ln input{padding:9px 8px;font-size:15px}.ln button{border:0;background:none;color:var(--bad);font-size:20px}
.rq{padding:12px 14px;border-bottom:1px solid var(--line)}.rq b{display:block}.rq .s{color:var(--mut);font-size:12.5px}.rq .a{display:flex;gap:8px;margin-top:8px}.rq .a button{flex:1;padding:9px;border-radius:10px;border:0;font:700 14px Cairo,system-ui}
.yes{background:var(--good);color:#fff}.nope{background:var(--bg);color:var(--bad);border:1.5px solid var(--bad)!important}
.modal{position:fixed;inset:0;background:rgba(7,17,30,.55);display:flex;align-items:center;justify-content:center;z-index:20;padding:20px}.modal[hidden]{display:none}
.modal .f{width:100%;max-width:360px}.pin{letter-spacing:10px;text-align:center;font-size:26px!important}
</style></head><body>
<header><div class="top"><img src="/icon-192.png" alt=""><div><h1 id="shop">${t}</h1><small><span class="st off" id="st"><i></i><span id="stt">بيحمّل…</span></span></small></div></div></header>
<main id="app"><div class="hero load">بيحمّل…</div></main>
<main id="v-draw" class="view" hidden></main><main id="v-req" class="view" hidden></main><main id="v-buy" class="view" hidden></main>
<div class="modal" id="pinm" hidden><div class="f"><h2 id="pint">اكتب الرقم السري</h2><input id="pinv" class="pin" type="password" inputmode="numeric" maxlength="6" autocomplete="off"><div class="note no" id="pine"></div>
<button class="btn" id="pinok">تأكيد</button><button class="btn l" onclick="pinDone(null)">إلغاء</button></div></div>
<datalist id="itl"></datalist><datalist id="spl"></datalist>
<nav class="bt"><a data-v="app" class="on"><i>📊</i>الأرقام</a><a data-v="draw"><i>💵</i>سحب</a><a data-v="req"><i>✅</i>الطلبات<em id="rqn" hidden></em></a><a data-v="buy"><i>🧾</i>مشتريات</a></nav>

<script>
const K=${JSON.stringify(k)},$=s=>document.querySelector(s),E=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const N=v=>Number(v||0).toLocaleString("en-US",{maximumFractionDigits:2});
const ago=t=>{const m=Math.round((Date.now()-Date.parse(t))/60000);return m<1?"دلوقتي":m<60?"من "+m+" دقيقة":m<1440?"من "+Math.round(m/60)+" ساعة":"من "+Math.round(m/1440)+" يوم"};
let deferred=null;addEventListener("beforeinstallprompt",e=>{e.preventDefault();deferred=e;const b=$("#inst");if(b)b.style.display=""});
function list(rows,f,emptyText){return rows&&rows.length?"<ul>"+rows.map(f).join("")+"</ul>":'<div class="empty">'+emptyText+"</div>"}
function render(r){
  const d=r.d,old=(Date.now()-Date.parse(r.at))>25*60000,today=d.day===new Date(Date.now()+2*3600e3).toISOString().slice(0,10);
  $("#shop").textContent=d.shop||"محلك";document.title="Trio — "+(d.shop||"");
  $("#st").className="st"+(old?" off":"");$("#stt").textContent=(old?"البرنامج مقفول أو مفيش نت — آخر تحديث ":"المحل شغال — آخر تحديث ")+ago(r.at);
  const t=d.today||{},wa=d.wa?"https://wa.me/"+d.wa+"?text="+encodeURIComponent(sumText(d)):"https://wa.me/?text="+encodeURIComponent(sumText(d));
  let h='<div class="hero"><div class="k">مبيعات '+(today?"النهارده":d.day)+'</div><div class="v">'+N(t.net)+' <small>ج.م</small></div>'+
   '<div class="row"><div><b>'+(t.n||0)+'</b><span>فاتورة</span></div><div><b>'+N(t.cash)+'</b><span>نقدي</span></div><div><b>'+N(t.card)+'</b><span>فيزا / محافظ</span></div></div></div>';
  h+='<div class="grid"><div class="tile"><div class="k">الربح التقريبي</div><div class="v">'+N(t.profit)+'</div></div><div class="tile"><div class="k">رصيد الدرج</div><div class="v">'+N(d.drawer)+'</div></div>'+
   '<div class="tile"><div class="k">مبيعات الشهر</div><div class="v">'+N((d.month||{}).net)+'</div></div><div class="tile'+(d.low_n?" warn":"")+'"><div class="k">نواقص</div><div class="v">'+(d.low_n||0)+' صنف</div></div>'+
   (t.credit?'<div class="tile"><div class="k">آجل النهارده</div><div class="v">'+N(t.credit)+'</div></div>':"")+(t.returns?'<div class="tile warn"><div class="k">مرتجعات</div><div class="v">'+N(t.returns)+'</div></div>':"")+"</div>";
  if(d.branches)h+='<section><h2>الفروع النهارده</h2>'+list(d.branches,b=>"<li><span>"+E(b.n)+' <span class="s">درج '+N(b.cash)+"</span></span><span>"+N(b.v)+"</span></li>","")+"</section>";
  if(d.play)h+='<section><h2>الأجهزة الشغالة <em>'+d.play.length+'</em></h2>'+list(d.play,p=>"<li><span>"+E(p.n)+' <span class="s">من '+E(p.at)+"</span></span><span>"+p.m+" د</span></li>","مفيش أجهزة شغالة دلوقتي")+"</section>";
  h+='<section><h2>آخر الفواتير</h2>'+list(d.recent,o=>"<li><span>#"+E(o.n)+' <span class="s">'+E(o.t)+" · "+E(o.d)+"</span>"+(o.r?'<span class="tag r">مرتجع</span>':"")+(o.x?'<span class="tag r">ملغية</span>':"")+'<span class="tag">'+E(o.pay)+"</span></span><span>"+N(o.v)+"</span></li>","لسه مفيش فواتير النهارده")+"</section>";
  h+='<section><h2>الأكتر بيعًا النهارده</h2>'+list(d.top,x=>"<li><span>"+E(x.n)+' <span class="s">× '+N(x.q)+"</span></span><span>"+N(x.v)+"</span></li>","لسه مفيش")+"</section>";
  h+='<section><h2>النواقص <em>'+(d.low_n||0)+'</em></h2>'+list(d.low,x=>"<li><span>"+E(x.n)+'</span><span>'+N(x.q)+' <span class="s">/ حد الطلب '+N(x.r)+"</span></span></li>","مفيش نواقص 👌")+"</section>";
  if(d.exp_n)h+='<section><h2>صلاحية قربت <em>'+d.exp_n+'</em></h2>'+list(d.exp,x=>"<li><span>"+E(x.n)+"</span><span>"+(x.d<0?"خلصت":"باقي "+x.d+" يوم")+"</span></li>","")+"</section>";
  if(d.shifts&&d.shifts.length)h+='<section><h2>ورديات مفتوحة</h2>'+list(d.shifts,s=>"<li><span>"+E(s.c)+' <span class="s">'+E(s.d)+"</span></span><span>من "+E(s.at)+"</span></li>","")+"</section>";
  h+='<div class="acts"><a class="btn" href="'+wa+'" target="_blank" rel="noopener">ابعت الملخص واتساب</a><button class="btn l" onclick="load(1)">تحديث</button><button class="btn l" id="inst" style="display:none" onclick="deferred&&deferred.prompt()">ثبّت الأيقونة</button></div>'+
   '<div class="tip" id="ios"><b>ضيفه للشاشة الرئيسية:</b> دوس زرار المشاركة ⬆️ تحت ← «إضافة إلى الشاشة الرئيسية» ← هتلاقي أيقونة Trio.</div>'+
   '<footer>Trio · 01008030459<br>الأرقام بتتحدّث كل 10 دقايق طول ما البرنامج شغال في المحل</footer>';
  $("#app").innerHTML=h;
  if(/iphone|ipad/i.test(navigator.userAgent)&&!navigator.standalone)$("#ios").style.display="block";
  if(deferred)$("#inst").style.display="";
}
function sumText(d){const t=d.today||{};return ["ملخص "+(d.shop||"")+" — "+d.day,"المبيعات: "+N(t.net)+" ج ("+(t.n||0)+" فاتورة)","نقدي "+N(t.cash)+" — فيزا/محافظ "+N(t.card)+" — آجل "+N(t.credit),"الربح التقريبي: "+N(t.profit),"رصيد الدرج: "+N(d.drawer),"powered by Trio"].join("\\n")}
async function load(manual){try{const r=await fetch("/m/"+K+"/data",{cache:"no-store"});const j=await r.json();if(j.d)render(j);else $("#app").innerHTML='<div class="hero load">لسه مفيش بيانات من المحل — افتح البرنامج على كمبيوتر المحل ودوس «حدّث دلوقتي» من «📱 الموبايل والتقارير».</div>'}
catch(e){$("#stt").textContent="مفيش نت على الموبايل"}}
load();setInterval(load,60000);document.addEventListener("visibilitychange",()=>{if(!document.hidden)load()});
if("serviceWorker" in navigator)navigator.serviceWorker.register("/m/"+K+"/sw.js",{scope:"/m/"+K}).catch(()=>{});
// ---------- the owner's actions (signed with the phone's secret; the PIN goes only to Trio's check)
let ME={paired:false},CAT=null,CUR="app";
const LSK="trio-own-"+K,J=s=>JSON.stringify(s);
function sec(){try{return localStorage.getItem(LSK)||""}catch(e){return ""}}
async function hx(s){const b=await crypto.subtle.digest("SHA-256",new TextEncoder().encode(s));return [...new Uint8Array(b)].map(x=>x.toString(16).padStart(2,"0")).join("")}
async function hmac(key,msg){const k=await crypto.subtle.importKey("raw",new TextEncoder().encode(key),{name:"HMAC",hash:"SHA-256"},false,["sign"]);
 return [...new Uint8Array(await crypto.subtle.sign("HMAC",k,new TextEncoder().encode(msg)))].map(x=>x.toString(16).padStart(2,"0")).join("")}
async function api(p,body){const r=await fetch("/m/"+K+p,body?{method:"POST",headers:{"Content-Type":"application/json","X-Trio":"1"},body:J(body),credentials:"same-origin"}:{cache:"no-store",credentials:"same-origin"});
 let j={};try{j=await r.json()}catch(e){}if(!r.ok)throw new Error(j.error||"مفيش نت");return j}
function show(v){CUR=v;document.querySelectorAll(".view,#app").forEach(e=>e.hidden=(e.id!==(v==="app"?"app":"v-"+v)));document.querySelectorAll("nav.bt a").forEach(a=>a.classList.toggle("on",a.dataset.v===v));
 scrollTo(0,0);if(v!=="app")drawAll()}
document.querySelectorAll("nav.bt a").forEach(a=>a.onclick=()=>show(a.dataset.v));
let pinCb=null;function askPin(t,cb){pinCb=cb;$("#pint").textContent=t;$("#pinv").value="";$("#pine").textContent="";$("#pinm").hidden=false;setTimeout(()=>$("#pinv").focus(),50)}
function pinDone(v){$("#pinm").hidden=true;const c=pinCb;pinCb=null;if(c&&v)c(v)}
$("#pinok").onclick=()=>{const v=$("#pinv").value;if(!/^\\d{4,6}$/.test(v)){$("#pine").textContent="من 4 لـ 6 أرقام";return}pinDone(v)};
async function send(kind,body,pin){const s=sec();if(!s)throw new Error("الموبايل مش مربوط");const id=[...crypto.getRandomValues(new Uint8Array(12))].map(x=>x.toString(16).padStart(2,"0")).join("");
 const b=J(body);await api("/cmd",{id,kind,body:b,mac:await hmac(s,id+"\\n"+kind+"\\n"+b),pin});return id}
function stTxt(c){return c.state==="ok"?'<span class="ok">✓ '+E(c.res)+"</span>":c.state==="no"?'<span class="no">✗ '+E(c.res)+"</span>":'<span class="s">⏳ مستني برنامج المحل…</span>'}
function cmdList(kinds){const r=(ME.cmds||[]).filter(c=>kinds.includes(c.kind));return r.length?'<section><h2>آخر أوامرك</h2><ul>'+r.map(c=>"<li style='display:block'><b>"+E(c.sum)+"</b> <span class='s'>"+E(c.at.slice(5,16).replace("T"," "))+"</span><div style='white-space:normal;font-size:13.5px'>"+stTxt(c)+"</div></li>").join("")+"</ul></section>":""}
function pairForm(){const code=(location.hash.match(/pair=([A-Z0-9-]{12,16})/)||[])[1]||"";
 return '<div class="f"><h2>🔗 اربط موبايلك</h2><p class="note" style="margin:0">من البرنامج على كمبيوتر المحل: <b>الموبايل والتقارير ← اربط موبايلي</b> — صوّر الكود أو اكتبه هنا.</p>'+
 '<label>الكود</label><input id="pc" dir="ltr" value="'+E(code)+'" placeholder="XXXX-XXXX-XXXX" autocomplete="off">'+
 '<label>اختار رقم سري (PIN) — بيتسأل قبل أي سحب أو فاتورة</label><input id="pp" class="pin" type="password" inputmode="numeric" maxlength="6">'+
 '<label>الرقم السري تاني</label><input id="pp2" class="pin" type="password" inputmode="numeric" maxlength="6">'+
 '<button class="btn" onclick="doPair()">اربط</button><div class="note no" id="pe"></div></div>'}
async function doPair(){const code=$("#pc").value.toUpperCase().replace(/[^A-Z0-9]/g,""),p=$("#pp").value;
 if(code.length!==12){$("#pe").textContent="الكود 12 حرف";return}if(!/^\\d{4,6}$/.test(p)){$("#pe").textContent="الرقم السري من 4 لـ 6 أرقام";return}if(p!==$("#pp2").value){$("#pe").textContent="الرقمين مش زي بعض";return}
 try{await api("/pair",{ph:await hx("trio-pair:"+code),pin:p,name:/iphone|ipad/i.test(navigator.userAgent)?"آيفون":/android/i.test(navigator.userAgent)?"أندرويد":"موبايل"});
  localStorage.setItem(LSK,await hx("trio-sign:"+code));history.replaceState(null,"",location.pathname);await refreshMe();drawAll()}catch(e){$("#pe").textContent=e.message}}
function needPair(el){if(ME.paired&&sec())return false;el.innerHTML=ME.paired?'<div class="f"><h2>الموبايل ده محتاج يتربط تاني</h2><p class="note">اتربط من متصفح تاني. افصله واربطه من هنا.</p><button class="btn l" onclick="unpairMe()">افصل</button></div>':pairForm();return true}
async function unpairMe(){try{await api("/unpair",{})}catch(e){}try{localStorage.removeItem(LSK)}catch(e){}await refreshMe();drawAll()}
function fresh(){return ME.at?'<p class="note">بيانات البرنامج من '+ago(ME.at)+(Date.now()-Date.parse(ME.at)>180000?" — البرنامج مقفول أو مفيش نت: الأمر هيتنفّذ أول ما يفتح":"")+"</p>":""}
function drawAll(){drawDraw();drawReq();drawBuy();const n=(ME.pend||[]).length;$("#rqn").hidden=!n;$("#rqn").textContent=n}
function drawDraw(){const el=$("#v-draw");if(CUR!=="draw"&&el.innerHTML)return;if(needPair(el))return;
 const opts=(ME.boxes||[]).map(b=>'<option value="'+b.i+'">'+E(b.n)+" — "+N(b.v)+"</option>").join("")+'<option value="card">فلوس الفيزا (Paymob)</option>';
 el.innerHTML='<div class="f"><h2>💵 سحب على حسابك</h2><label>المبلغ</label><input id="da" type="number" inputmode="decimal" min="1" step="0.01">'+
 '<label>من</label><select id="df">'+opts+'</select><label>ملاحظة</label><input id="dn" maxlength="100" placeholder="اختياري">'+
 '<button class="btn" onclick="doDraw()">سجّل السحب</button><div class="note no" id="de"></div>'+
 '<p class="note">بيتسجّل في حسابات المحل كمسحوبات على حسابك. لو السحب من فلوس الفيزا، التحويل نفسه بيتعمل من تطبيق Paymob أو لوحته.</p>'+fresh()+'</div>'+cmdList(["drawing"])}
function doDraw(){const a=Number($("#da").value);if(!(a>0)){$("#de").textContent="اكتب المبلغ";return}const f=$("#df").value,fn=$("#df").selectedOptions[0].text.split(" — ")[0];
 askPin("سحب "+N(a)+" من "+fn,async pin=>{try{await send("drawing",{amount:String(a),from:f,note:$("#dn").value},pin);await refreshMe();CUR="draw";$("#v-draw").innerHTML="";drawDraw()}catch(e){$("#de").textContent=e.message}})}
function drawReq(){const el=$("#v-req");if(CUR!=="req"&&el.innerHTML)return;if(needPair(el))return;const P=ME.pend||[];
 el.innerHTML='<section style="margin-top:0"><h2>طلبات الكاشير <em>'+P.length+'</em></h2>'+(P.length?P.map((r,i)=>'<div class="rq"><b>'+E(r.kind)+" "+E(r.target)+(r.v!=null?" — "+N(r.v):"")+'</b><span class="s">'+E(r.by)+" · "+E(r.at)+"</span><div>"+E(r.why)+"</div>"+
 '<div class="a"><button class="yes" onclick="doDecI('+i+',1)">موافق</button><button class="nope" onclick="doDecI('+i+',0)">رفض</button></div></div>').join(""):'<div class="empty">مفيش طلبات مستنية 👌</div>')+
 "</section>"+fresh()+cmdList(["decide"])}
function doDecI(i,yes){const r=(ME.pend||[])[i];if(r)doDec(r.uid,yes,r.kind+" "+r.target)}
async function doDec(uid,yes,what){try{await send("decide",{req:uid,approve:!!yes,what});ME.pend=(ME.pend||[]).filter(r=>r.uid!==uid);await refreshMe(true);$("#v-req").innerHTML="";CUR="req";drawReq()}catch(e){alert(e.message)}}
let BL=[{}];
async function loadCat(){if(CAT)return;try{CAT=await api("/cat");$("#itl").innerHTML=CAT.map(r=>'<option value="'+E(r[1])+'">'+E(r[2])+(r[3]?" · "+E(r[3]):"")+"</option>").join("")}catch(e){CAT=[]}}
function drawBuy(){const el=$("#v-buy");if(CUR!=="buy"&&el.innerHTML)return;if(needPair(el))return;loadCat();
 $("#spl").innerHTML=(ME.sup||[]).map(s=>'<option value="'+E(s.n)+'">').join("");
 const boxes=(ME.boxes||[]).map(b=>'<option value="'+b.i+'">'+E(b.n)+"</option>").join("");
 el.innerHTML='<div class="f"><h2>🧾 فاتورة مشتريات</h2><label>المورد</label><input id="bs" list="spl" placeholder="اسم المورد (أو مورد جديد)">'+
 '<label>رقم الفاتورة</label><input id="bi" maxlength="30" placeholder="اختياري"><div class="ln" style="margin-top:12px;font-size:12px;color:var(--mut)"><span>الصنف</span><span>الكمية</span><span>سعر الشراء</span><span></span></div><div id="bl"></div>'+
 '<button class="btn l" onclick="BL.push({});bl()">+ صنف</button><label>الدفع</label><select id="bp" onchange="$(\\'#bbx\\').hidden=this.value!==\\'cash\\'"><option value="cash">كاش دفعته</option><option value="credit">آجل على المورد</option></select>'+
 '<div id="bbx"><label>اتدفع من</label><select id="bb">'+boxes+'</select></div><p style="margin:12px 0 0;font-weight:800">الإجمالي: <span id="bt">0</span></p>'+
 '<button class="btn" onclick="doBuy()">سجّل الفاتورة</button><div class="note no" id="be"></div>'+fresh()+'</div>'+cmdList(["purchase"]);bl()}
function bl(){$("#bl").innerHTML=BL.map((l,i)=>'<div class="ln"><input list="itl" value="'+E(l.n||"")+'" oninput="BL['+i+'].n=this.value;bt()" placeholder="اسم / كود"><input type="number" inputmode="decimal" value="'+E(l.q||"")+'" oninput="BL['+i+'].q=this.value;bt()"><input type="number" inputmode="decimal" value="'+E(l.c||"")+'" oninput="BL['+i+'].c=this.value;bt()"><button onclick="BL.splice('+i+',1);if(!BL.length)BL.push({});bl()">×</button></div>').join("");bt()}
function bt(){$("#bt").textContent=N(BL.reduce((s,l)=>s+(Number(l.q)||0)*(Number(l.c)||0),0))}
function findItem(n){n=String(n||"").trim();if(!n||!CAT)return null;return CAT.find(r=>r[1]===n||r[2]===n||r[3]===n)||null}
function doBuy(){const sn=$("#bs").value.trim();if(!sn){$("#be").textContent="اكتب المورد";return}const sp=(ME.sup||[]).find(s=>s.n===sn);
 const lines=[],news=[];for(const l of BL){if(!l.n&&!l.q)continue;const q=Number(l.q),c=Number(l.c);if(!l.n||!(q>0)||!(c>=0)||l.c===""||l.c==null){$("#be").textContent="كمّل كل سطر: الصنف والكمية والسعر";return}
  const it=findItem(l.n);if(it)lines.push({item:it[0],qty:String(q),cost:String(c)});else{lines.push({new:l.n,qty:String(q),cost:String(c),sale:""});news.push(l.n)}}
 if(!lines.length){$("#be").textContent="ضيف صنف";return}
 if(news.length&&!confirm("أصناف جديدة هتتضاف للبرنامج من غير سعر بيع: "+news.join("، ")+" — تحدد سعرها بعدين من البرنامج. نكمّل؟"))return;
 const pay=$("#bp").value,total=BL.reduce((s,l)=>s+(Number(l.q)||0)*(Number(l.c)||0),0);
 askPin("فاتورة "+sn+" بـ "+N(total),async pin=>{try{await send("purchase",{supplier:sp?String(sp.i):"",new_supplier:sp?"":sn,sup_name:sn,invoice:$("#bi").value,pay,box:pay==="cash"?$("#bb").value:"",lines,total:N(total)},pin);
  BL=[{}];await refreshMe();$("#v-buy").innerHTML="";CUR="buy";drawBuy()}catch(e){$("#be").textContent=e.message}})}
async function refreshMe(keep){try{const pend=ME.pend;ME=await api("/me");if(keep&&pend&&ME.pend)ME.pend=ME.pend.filter(r=>pend.some(x=>x.uid===r.uid))}catch(e){}const n=(ME.pend||[]).length;$("#rqn").hidden=!n;$("#rqn").textContent=n}
async function tick(){await refreshMe();if(CUR!=="app"){const el=$("#v-"+CUR);if(!el.contains(document.activeElement)&&$("#pinm").hidden){el.innerHTML="";drawAll()}}}
tick();setInterval(tick,15000);if(location.hash.includes("pair="))show("draw");
</script></body></html>`, { headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store", "X-Robots-Tag": "noindex" } });
}

// ---------------------------------------------------------------- «منيو QR» (cafés / restaurants): public menu at /menu/<id>
// id = sha256("acc-menu:" + shop secret)[:10 bytes] — public, and it does not reveal the owner's phone page.
async function menuId(k) { const h = new Uint8Array(await crypto.subtle.digest("SHA-256", enc.encode("acc-menu:" + k))); return [...h.slice(0, 10)].map((x) => x.toString(16).padStart(2, "0")).join(""); }
async function menuPut(req, env) {
  let b = {};
  try { b = await req.json(); } catch (e) { return err("طلب ناقص"); }
  if (!KEYRE.test(b.k || "") || !MACHINE.test(b.m || "") || typeof b.d !== "object" || !Array.isArray(b.d.cats)) return err("طلب ناقص");
  const body = JSON.stringify(b.d);
  if (body.length > 200000) return err("المنيو كبير", 413);
  const id = await menuId(b.k);
  await env.ACC.put("mn:" + id, JSON.stringify({ d: b.d, at: iso() }));
  return json({ ok: true, id });
}
function menuPage(rec) {
  const d = rec.d, fmt = (n) => Number(n || 0).toLocaleString("ar-EG", { maximumFractionDigits: 2 });
  const tabs = d.cats.map((c, i) => `<a href="#c${i}">${esc(c.n)}</a>`).join("");
  const secs = d.cats.map((c, i) => `<section id="c${i}"><h2>${esc(c.n)}</h2>${c.items.map((x) =>
    `<div class="it"><div><b>${esc(x.n)}</b>${x.d ? `<small>${esc(x.d)}</small>` : ""}</div><span>${fmt(x.p)} <i>ج</i></span></div>`).join("")}</section>`).join("");
  return `<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>${esc(d.name)} — المنيو</title><meta name="theme-color" content="#0B1F3A"><link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Cairo:wght@400;700;800&display=swap" rel="stylesheet">
<style>*{box-sizing:border-box}body{margin:0;font-family:Cairo,system-ui,sans-serif;background:#F3F6FA;color:#0B1F3A}
header{background:#0B1F3A;color:#fff;padding:22px 16px 14px;text-align:center}header h1{margin:0;font-size:24px}header p{margin:4px 0 0;opacity:.8;font-size:14px}
nav{position:sticky;top:0;background:#fff;display:flex;gap:6px;overflow-x:auto;padding:10px 12px;box-shadow:0 2px 8px #0001;z-index:2}
nav a{flex:none;padding:6px 14px;border-radius:99px;background:#E3F0F6;color:#0B1F3A;text-decoration:none;font-weight:700;font-size:14px}
main{max-width:720px;margin:0 auto;padding:6px 14px 30px}h2{margin:22px 0 8px;font-size:19px;color:#0B1F3A;border-bottom:2px solid #3CC2B5;display:inline-block}
.it{display:flex;justify-content:space-between;gap:12px;align-items:center;background:#fff;border-radius:14px;padding:12px 14px;margin:8px 0;box-shadow:0 1px 3px #0000000d}
.it b{font-size:16px}.it small{display:block;color:#66788F;font-size:13px}.it span{font-weight:800;color:#17698F;white-space:nowrap;font-size:17px}.it i{font-style:normal;font-size:13px}
.note{text-align:center;color:#66788F;font-size:13px;margin:20px 0 0}footer{text-align:center;font-size:12px;color:#7F8EA3;padding:16px}footer a{color:#17698F}</style></head><body>
<header><h1>${esc(d.name)}</h1>${d.address || d.phone ? `<p>${[d.address ? esc(d.address) : "", d.phone ? `<span dir="ltr">${esc(d.phone)}</span>` : ""].filter(Boolean).join(" · ")}</p>` : ""}</header>
${d.cats.length > 1 ? `<nav>${tabs}</nav>` : ""}<main>${secs || "<p class=note>المنيو بيتجهز…</p>"}
${d.note ? `<p class="note">${esc(d.note)}</p>` : ""}${d.vat ? `<p class="note">الأسعار قبل ضريبة القيمة المضافة</p>` : ""}</main>
<footer>المنيو بيتحدّث لوحده من برنامج المحل · <a href="https://trioerp.pages.dev/" rel="noopener">Trio</a></footer></body></html>`;
}
async function menuGet(env, id) {
  if (!/^[0-9a-f]{20}$/.test(id)) return new Response("not found", { status: 404 });
  const v = await env.ACC.get("mn:" + id);
  if (!v) return new Response("<!doctype html><meta charset=utf-8><p style='font-family:sans-serif;text-align:center;margin-top:40px'>المنيو مش متاح دلوقتي</p>",
    { status: 404, headers: { "Content-Type": "text/html; charset=utf-8" } });
  return new Response(menuPage(JSON.parse(v)), { headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "public, max-age=60" } });
}

// ======================================================================= the owner's phone: orders to the shop program
// The phone leaves an order here (signed with a secret only the phone and the shop's computer know); the program picks it
// up on its next round (every minute, every 10 s while the app is open) and answers. Needs D1 (binding DB).
let ownReady = false;
async function ownDB(env) {
  if (!env.DB) return null;
  if (!ownReady) {
    await env.DB.batch([
      env.DB.prepare("CREATE TABLE IF NOT EXISTS ocmd (id TEXT PRIMARY KEY, sid TEXT, ph TEXT, kind TEXT, body TEXT, mac TEXT, at TEXT, state TEXT, res TEXT, done TEXT)"),
      env.DB.prepare("CREATE INDEX IF NOT EXISTS ocmd_sid ON ocmd (sid, state)"),
      env.DB.prepare("CREATE TABLE IF NOT EXISTS ophone (ph TEXT PRIMARY KEY, sid TEXT, name TEXT, pin TEXT, tok TEXT, at TEXT, seen TEXT, fails INTEGER, lock TEXT)"),
      env.DB.prepare("CREATE TABLE IF NOT EXISTS opair (ph TEXT PRIMARY KEY, sid TEXT, exp TEXT, owner TEXT)"),
      env.DB.prepare("CREATE TABLE IF NOT EXISTS oaux (sid TEXT PRIMARY KEY, d TEXT, cat TEXT, at TEXT, act TEXT)"),
    ]);
    ownReady = true;
  }
  return env.DB;
}
const HEX64 = /^[0-9a-f]{64}$/;
async function hexsha(s) { return [...new Uint8Array(await crypto.subtle.digest("SHA-256", enc.encode(s)))].map((x) => x.toString(16).padStart(2, "0")).join(""); }
function cookieOf(req, name) { const m = (req.headers.get("Cookie") || "").match(new RegExp("(?:^|;\\s*)" + name + "=([A-Za-z0-9_-]{20,80})")); return m ? m[1] : ""; }

// --- the shop program (authenticated by its own mobile key k, the same one its numbers use)
async function ownProg(req, env, url) {
  let b = {};
  try { b = await req.json(); } catch (e) { return err("طلب ناقص"); }
  if (!KEYRE.test(b.k || "") || !MACHINE.test(b.m || "")) return err("طلب ناقص");
  const d = await ownDB(env);
  if (!d) return err("السيرفر محتاج قاعدة D1", 503);
  const sid = await sidOf(b.k), p = url.pathname;
  if (p === "/m/pair") {
    if (!HEX64.test(b.ph || "")) return err("طلب ناقص");
    await d.prepare("DELETE FROM opair WHERE sid = ?1 OR exp < ?2").bind(sid, iso()).run();
    await d.prepare("INSERT OR REPLACE INTO opair (ph, sid, exp, owner) VALUES (?1, ?2, ?3, ?4)").bind(b.ph, sid, String(b.exp || "").slice(0, 30), String(b.owner || "").slice(0, 80)).run();
    return json({ ok: true });
  }
  if (p === "/m/unpair") { await d.prepare("DELETE FROM ophone WHERE ph = ?1 AND sid = ?2").bind(String(b.ph || ""), sid).run(); return json({ ok: true }); }
  if (p === "/m/cat") {
    const items = JSON.stringify(Array.isArray(b.items) ? b.items.slice(0, 5000) : []);
    if (items.length > 900000) return err("الأصناف كتير", 413);
    await d.prepare("INSERT INTO oaux (sid, cat, at) VALUES (?1, ?2, ?3) ON CONFLICT(sid) DO UPDATE SET cat = ?2").bind(sid, items, iso()).run();
    return json({ ok: true });
  }
  // /m/q — answers up, the program's lists up, new orders down
  for (const r of (Array.isArray(b.res) ? b.res : []).slice(0, 50)) {
    await d.prepare("UPDATE ocmd SET state = ?1, res = ?2, done = ?3 WHERE id = ?4 AND sid = ?5").bind(r.ok ? "ok" : "no", String(r.msg || "").slice(0, 400), iso(), String(r.id || ""), sid).run();
  }
  const aux = JSON.stringify({ pend: (b.pend || []).slice(0, 20), boxes: (b.boxes || []).slice(0, 20), sup: (b.sup || []).slice(0, 400), at: iso() });
  if (aux.length < 200000) await d.prepare("INSERT INTO oaux (sid, d, at) VALUES (?1, ?2, ?3) ON CONFLICT(sid) DO UPDATE SET d = ?2, at = ?3").bind(sid, aux, iso()).run();
  const cmds = (await d.prepare("SELECT id, ph, kind, body, mac FROM ocmd WHERE sid = ?1 AND state = 'new' ORDER BY at LIMIT 20").bind(sid).all()).results || [];
  for (const c of cmds) await d.prepare("UPDATE ocmd SET state = 'sent' WHERE id = ?1").bind(c.id).run();
  // an order «sent» for 10 minutes without an answer (program closed mid-way) goes again: the program does each id only once
  await d.prepare("UPDATE ocmd SET state = 'new' WHERE sid = ?1 AND state = 'sent' AND at < ?2").bind(sid, new Date(Date.now() - 600000).toISOString()).run();
  const phones = ((await d.prepare("SELECT ph, name, seen FROM ophone WHERE sid = ?1").bind(sid).all()).results || []);
  const a = await d.prepare("SELECT act FROM oaux WHERE sid = ?1").bind(sid).first();
  return json({ cmds, phones, fast: !!(a && a.act && Date.now() - Date.parse(a.act) < 180000) });
}

// --- the owner's phone (/m/<k>/…)
async function ownPhone(req, env, k, sub) {
  const d = await ownDB(env);
  if (!d) return err("الميزة دي مش متاحة على السيرفر لسه", 503);
  const sid = await sidOf(k), tok = cookieOf(req, "tp");
  const me = tok ? await d.prepare("SELECT * FROM ophone WHERE sid = ?1 AND tok = ?2").bind(sid, await hexsha("tok:" + tok)).first() : null;
  if (req.method === "POST" && req.headers.get("X-Trio") !== "1") return err("طلب مش من التطبيق", 403);
  if (sub === "/me") {
    const out = { paired: !!me };
    if (me) {
      await d.prepare("UPDATE ophone SET seen = ?1 WHERE ph = ?2").bind(iso(), me.ph).run();
      await d.prepare("INSERT INTO oaux (sid, act) VALUES (?1, ?2) ON CONFLICT(sid) DO UPDATE SET act = ?2").bind(sid, iso()).run();
      const a = await d.prepare("SELECT d FROM oaux WHERE sid = ?1").bind(sid).first();
      Object.assign(out, a && a.d ? JSON.parse(a.d) : {}, { name: me.name });
      out.cmds = ((await d.prepare("SELECT id, kind, body, at, state, res FROM ocmd WHERE sid = ?1 AND ph = ?2 ORDER BY at DESC LIMIT 15").bind(sid, me.ph).all()).results || [])
        .map((c) => ({ id: c.id, kind: c.kind, at: c.at, state: c.state, res: c.res, sum: cmdSum(c.kind, c.body) }));
    }
    return json(out);
  }
  if (sub === "/cat") {
    if (!me) return err("الموبايل مش مربوط", 401);
    const a = await d.prepare("SELECT cat FROM oaux WHERE sid = ?1").bind(sid).first();
    return new Response((a && a.cat) || "[]", { headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" } });
  }
  if (req.method !== "POST") return err("مش موجود", 404);
  let b = {};
  try { b = await req.json(); } catch (e) { return err("طلب ناقص"); }
  if (sub === "/pair") {
    if (!HEX64.test(b.ph || "") || !/^\d{4,6}$/.test(b.pin || "")) return err("اكتب الكود ورقم سري من 4 لـ 6 أرقام");
    if ((await bump(env, "rl:op:" + sid, 3600)) > 12) return err("محاولات كتير — استنى ساعة", 429);
    const pr = await d.prepare("SELECT * FROM opair WHERE ph = ?1 AND sid = ?2").bind(b.ph, sid).first();
    if (!pr || pr.exp < iso()) return err("الكود غلط أو خلص — اعمل كود جديد من البرنامج (الموبايل والتقارير ← اربط موبايلي)");
    const t = crypto.randomUUID().replace(/-/g, "") + crypto.randomUUID().replace(/-/g, "").slice(0, 16), salt = crypto.randomUUID();
    await d.prepare("DELETE FROM opair WHERE ph = ?1").bind(b.ph).run();
    await d.prepare("INSERT OR REPLACE INTO ophone (ph, sid, name, pin, tok, at, seen, fails, lock) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?6, 0, '')")
      .bind(b.ph, sid, String(b.name || "موبايل").slice(0, 60), salt + ":" + (await hexsha(salt + ":" + b.pin)), await hexsha("tok:" + t), iso()).run();
    return new Response(JSON.stringify({ ok: true }), { headers: { "Content-Type": "application/json; charset=utf-8",
      "Set-Cookie": `tp=${t}; Path=/m/${k}; Max-Age=31536000; HttpOnly; Secure; SameSite=Strict` } });
  }
  if (!me) return err("الموبايل مش مربوط — اربطه من البرنامج", 401);
  if (sub === "/unpair") {
    await d.prepare("DELETE FROM ophone WHERE ph = ?1").bind(me.ph).run();
    return new Response(JSON.stringify({ ok: true }), { headers: { "Content-Type": "application/json; charset=utf-8", "Set-Cookie": `tp=x; Path=/m/${k}; Max-Age=0; HttpOnly; Secure; SameSite=Strict` } });
  }
  if (sub === "/cmd") {
    const kinds = { drawing: true, purchase: true, decide: false }; // true: asks for the PIN
    if (!(b.kind in kinds) || !/^[0-9a-f]{20,40}$/.test(b.id || "") || !HEX64.test(b.mac || "") || typeof b.body !== "string" || b.body.length > 20000) return err("طلب ناقص");
    if (kinds[b.kind]) {
      if (me.lock && me.lock > iso()) return err("الرقم السري اتكتب غلط كذا مرة — استنى ربع ساعة", 429);
      const [salt, h] = String(me.pin).split(":");
      if ((await hexsha(salt + ":" + String(b.pin || ""))) !== h) {
        const f = (me.fails || 0) + 1;
        if (f >= 15) { await d.prepare("DELETE FROM ophone WHERE ph = ?1").bind(me.ph).run(); return err("الرقم السري غلط كتير — الموبايل اتفصل، اربطه تاني من البرنامج", 401); }
        await d.prepare("UPDATE ophone SET fails = ?1, lock = ?2 WHERE ph = ?3").bind(f, f % 5 === 0 ? new Date(Date.now() + 900000).toISOString() : "", me.ph).run();
        return err("الرقم السري غلط", 403);
      }
      if (me.fails) await d.prepare("UPDATE ophone SET fails = 0, lock = '' WHERE ph = ?1").bind(me.ph).run();
    }
    if ((await bump(env, "rl:oc:" + me.ph, 3600)) > 60) return err("أوامر كتير في ساعة واحدة", 429);
    await d.prepare("INSERT OR IGNORE INTO ocmd (id, sid, ph, kind, body, mac, at, state, res, done) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, 'new', '', '')")
      .bind(b.id, sid, me.ph, b.kind, b.body, b.mac, iso()).run();
    await d.prepare("INSERT INTO oaux (sid, act) VALUES (?1, ?2) ON CONFLICT(sid) DO UPDATE SET act = ?2").bind(sid, iso()).run();
    return json({ ok: true, id: b.id });
  }
  return err("مش موجود", 404);
}
function cmdSum(kind, body) {
  try {
    const b = JSON.parse(body);
    if (kind === "drawing") return "سحب " + b.amount;
    if (kind === "decide") return (b.approve ? "موافقة على " : "رفض ") + (b.what || "طلب");
    if (kind === "purchase") return "فاتورة مشتريات " + (b.sup_name || "") + (b.total ? " — " + b.total : "");
  } catch (e) { /* */ }
  return kind;
}

async function mobileRoutes(req, env, url) {
  const p = url.pathname;
  if (p === "/icon-192.png") return png(ICONS["192"]);
  if (p === "/icon-512.png") return png(ICONS["512"]);
  if (p === "/icon-maskable-512.png") return png(ICONS.m512);
  if (p === "/apple-touch-icon.png") return png(ICONS["180"]);
  const m = p.match(/^\/m\/([A-Za-z0-9]{16,40})(\/[a-z.]+)?$/);
  if (!m) return null;
  const k = m[1], sub = m[2] || "", id = await sidOf(k);
  if (["/me", "/cat", "/pair", "/cmd", "/unpair"].includes(sub)) return ownPhone(req, env, k, sub);
  if (sub === "/data") { const r = await snapGet(env, id); return new Response(JSON.stringify(r || {}), { headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" } }); }
  if (sub === "/sw.js") return new Response(SW, { headers: { "Content-Type": "text/javascript; charset=utf-8", "Service-Worker-Allowed": "/m/" + k } });
  const r = await snapGet(env, id);
  if (sub === "/manifest.webmanifest") return manifest(k, r && r.d && r.d.shop);
  if (sub) return null;
  return phone(k, r && r.d && r.d.shop);
}

function page(title, text, go) {
  const html = `<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Trio</title>
<style>body{font-family:system-ui,sans-serif;background:#F3F6FA;color:#0B1F3A;display:grid;place-items:center;min-height:100vh;margin:0;padding:16px}
div{background:#fff;border-radius:18px;padding:28px;max-width:520px;text-align:center;box-shadow:0 10px 30px rgba(0,0,0,.08)}h1{font-size:24px}a{display:inline-block;margin-top:10px;background:#17698F;color:#fff;padding:10px 22px;border-radius:12px;text-decoration:none}</style>
<div><h1>${title}</h1><p>${text}</p>${go ? `<a href="${go}">كمّل</a><script>setTimeout(()=>location.href=${JSON.stringify(go)},2500)</script>` : ""}</div></html>`;
  return new Response(html, { headers: { "Content-Type": "text/html; charset=utf-8" } });
}

// ================================================================ Trio's phone app (/admin) — 1.16
// Everything Trio License does on the computer, from the phone, with the computer off: clients, licences, plans and parts,
// stop / resume, developer codes, online orders, payments / AI switches — and the command channel is republished from here.
// Codes are signed with the PHONE KEY (Secret ADMIN_KEY, a JWK) that Trio's own key certified (Secret ADMIN_CERT = TRIOK
// use=admin); the program accepts it for TRIO1/TRIOM/TRIOC/TRIOD/TRIOP only (never updates) and Trio License can stop it.
// The register lives in D1 (table admin, k=state) — Trio License pulls / pushes it (POST /admin/sync/*, Secret KEY), so the
// computer and the phone see the same clients. Login: a 6-digit code to ADMIN_EMAIL (default MAIL_FROM) through Brevo.
// Secrets: ADMIN_KEY, ADMIN_CERT, GH_TOKEN (GitHub: Contents read and write on the feed repository), KEY, BREVO_KEY.
const BIZ = { market: "سوبر ماركت / بقالة", retail: "محل تجزئة", cafe: "كافيه / مطعم", gaming: "بلايستيشن / بلياردو", clinic: "عيادة / مركز طبي" };
const ALWAYS = ["pos", "items", "profit_monthly", "expiry"];
const MODKEYS = Object.keys(CAT.names);
const defMods = (plan, biz) => new Set([...(CAT.plan_mods[plan] || []), ...(CAT.business[biz] || [])].filter((k) => fits(k, biz)));
const cairo = () => new Date(Date.now() + 2 * 3600e3).toISOString().slice(0, 16).replace("T", " ");
const sha = async (s) => [...new Uint8Array(await crypto.subtle.digest("SHA-256", enc.encode(s)))].map((x) => x.toString(16).padStart(2, "0")).join("");
const adminEmail = (env) => env.ADMIN_EMAIL || env.MAIL_FROM || "";

async function adminDB(env) {
  const d = await db(env);
  if (!d) throw new Error("مفيش قاعدة بيانات (D1) مربوطة باسم DB");
  await d.prepare("CREATE TABLE IF NOT EXISTS admin (k TEXT PRIMARY KEY, v TEXT)").run();
  return d;
}
async function stGet(env) {
  const r = await (await adminDB(env)).prepare("SELECT v FROM admin WHERE k='state'").first();
  return r ? JSON.parse(r.v) : null;
}
async function stPut(env, st) {
  await (await adminDB(env)).prepare("INSERT INTO admin (k, v) VALUES ('state', ?1) ON CONFLICT(k) DO UPDATE SET v=excluded.v").bind(JSON.stringify(st)).run();
}

async function asign(env, tag, payload, upd = false) {
  const key = await crypto.subtle.importKey("jwk", JSON.parse(upd ? env.UPDATE_KEY : env.ADMIN_KEY), { name: "Ed25519" }, false, ["sign"]);
  const p = utf8b64u(payload);
  const s = b64u(await crypto.subtle.sign({ name: "Ed25519" }, key, enc.encode(p)));
  return `${tag}.${p}.${s}.` + String(upd ? env.UPDATE_CERT : env.ADMIN_CERT).trim().replace(/^TRIOK\./, "");
}

// ---------------------------------------------------------------- releases from the phone (update key; clients wait 2 hours)
const relVer = (code) => { try { return fromB64u(String(code || "").split(".")[1]).v || ""; } catch (e) { return ""; } };
const verGt = (a, b) => { const x = String(a).split(".").map(Number), y = String(b).split(".").map(Number);
  for (let i = 0; i < Math.max(x.length, y.length); i++) { if ((x[i] || 0) !== (y[i] || 0)) return (x[i] || 0) > (y[i] || 0); } return false; };
async function ghReleases(env, st) {
  const g = st.gh || {};
  if (!env.GH_TOKEN || !g.Owner || !g.Repo) throw new Error("ناقص GH_TOKEN أو بيانات GitHub");
  const h = { Authorization: "Bearer " + env.GH_TOKEN, "User-Agent": "Trio", Accept: "application/vnd.github+json" };
  const r = await fetch(`https://api.github.com/repos/${g.Owner}/${g.Repo}/contents/updates?ref=${encodeURIComponent(g.Branch || "main")}`, { headers: h });
  if (r.status === 404) return [];
  if (!r.ok) throw new Error("مقدرتش أقرا GitHub (" + r.status + ")");
  const files = await r.json(), out = [];
  for (const f of files.filter((f) => /^trio-[\d.]+\.json$/.test(f.name))) {
    const m = await fetch(f.download_url + "?t=" + Date.now()).then((x) => x.json()).catch(() => null);
    if (m && m.v && /^[0-9a-f]{64}$/.test(m.sha || "") && files.some((z) => z.name === `trio-${m.v}.zip`))
      out.push({ v: String(m.v), sha: m.sha, n: Number(m.n) || 0, notes: String(m.notes || "").slice(0, 600),
        u: `https://raw.githubusercontent.com/${g.Owner}/${g.Repo}/${g.Branch || "main"}/updates/trio-${m.v}.zip` });
  }
  return out.sort((a, b) => (verGt(a.v, b.v) ? -1 : 1));
}
async function updatesPage(env, st, tok, msg = "", errText = "") {
  const cur = relVer(st.lines?.release), ready = !!(env.UPDATE_KEY && env.UPDATE_CERT);
  let rels = [], e2 = "";
  try { rels = (await ghReleases(env, st)).filter((r) => verGt(r.v, cur)); } catch (e) { e2 = String(e.message || e); }
  const otp = await env.ACC.get("uotp");
  const f = (a, inner, cls = "btn") => `<form method="post"><input type="hidden" name="csrf" value="${tok}"><input type="hidden" name="a" value="${a}">${inner}</form>`;
  return apage("التحديثات", `${msg ? `<div class="msg">${esc(msg)}</div>` : ""}${errText || e2 ? `<div class="err">${esc(errText || e2)}</div>` : ""}<h1>⬆ التحديثات</h1>
<div class="card"><h2>المنشور دلوقتي</h2><p><b dir="ltr">${esc(cur || "—")}</b>${st.lines?.relFrom === "phone" ? ' <span class="tag warn">من الموبايل</span>' : ""}</p>
${st.lines?.prevRelease !== undefined && st.lines?.relFrom === "phone" ? f("undo", `<p class="muted">لو النشر ده مش انت اللي عامله أو فيه مشكلة: رجّع اللي قبله (العملاء اللي لسه ماسطّبوش مش هياخدوه).</p><button class="btn red" onclick="return confirm('ترجع للإصدار اللي قبله؟')">تراجع عن النشر</button>`) : ""}</div>
${!ready ? `<div class="card"><p class="muted">النشر من الموبايل محتاج «مفتاح النشر»: من Trio License ← «📱 من الموبايل» ← رقم 4 ← اعمل مفتاح النشر، وحط UPDATE_KEY و UPDATE_CERT في الـ Worker.</p></div>` :
  rels.length ? rels.map((r) => `<div class="card"><h2>إصدار <span dir="ltr">${esc(r.v)}</span> جاهز</h2><p class="muted" style="white-space:pre-line">${esc(r.notes)}</p><p class="muted">${(r.n / 1048576).toFixed(1)} ميجا</p>
${otp ? f("go", `<input type="hidden" name="v" value="${esc(r.v)}"><label>كود النشر اللي وصلك على الإيميل<input name="code" inputmode="numeric" maxlength="6" required></label><button class="btn">انشر ${esc(r.v)} للعملاء</button>`)
      : f("code", `<button class="btn">ابعتلي كود النشر</button>`)}
<p class="muted">بعد النشر العملاء بيستنوا ساعتين قبل ما يسطّبوا — ويوصلك إيميل بالنشر.</p></div>`).join("")
  : `<div class="card"><p class="muted">مفيش إصدار أحدث من المنشور على GitHub. أول ما Trio يرفع إصدار جديد هيظهر هنا.</p></div>`}`, "/settings");
}
async function updatesAct(env, st, f) {
  if (f.a === "undo") {
    if (st.lines?.relFrom !== "phone") throw new Error("مفيش نشر من الموبايل ترجع عنه");
    st.lines.release = st.lines.prevRelease || ""; st.lines.relFrom = "undo"; delete st.lines.prevRelease;
    await bumpRev(st); await publish(env, st, "تراجع عن نشر إصدار"); await stPut(env, st);
    try { await mail(env, [adminEmail(env)], "Trio: اتعمل تراجع عن نشر إصدار", "اتعمل تراجع عن آخر إصدار اتنشر من الموبايل. المنشور دلوقتي: " + (relVer(st.lines.release) || "—"), "Trio"); } catch (e) { /* */ }
    return "اترجع ✔ — المنشور دلوقتي " + (relVer(st.lines.release) || "—");
  }
  if (!env.UPDATE_KEY || !env.UPDATE_CERT) throw new Error("ناقص مفتاح النشر في الـ Worker");
  if (f.a === "code") {
    if ((await bump(env, "rl:uotp", 3600)) > 5) throw new Error("طلبت أكواد كتير — استنى ساعة");
    const code = String(crypto.getRandomValues(new Uint32Array(1))[0] % 900000 + 100000);
    await env.ACC.put("uotp", JSON.stringify({ h: await sha(code + env.KEY), n: 0 }), { expirationTtl: 600 });
    await mail(env, [adminEmail(env)], "كود نشر تحديث Trio: " + code, `حد طلب ينشر تحديث للبرنامج عند كل العملاء من لوحة الموبايل.\nالكود: ${code} (صالح 10 دقايق)\nلو مش انت: ماتدخلش الكود، وغيّر باسورد الإيميل وCloudflare، واقفل «مفتاح النشر» من Trio License.`, "Trio");
    return "الكود اتبعت على " + adminEmail(env);
  }
  if (f.a === "go") {
    const o = JSON.parse((await env.ACC.get("uotp")) || "null");
    if (!o) throw new Error("الكود خلص — اطلب كود جديد");
    if ((await sha(String(f.code || "").trim() + env.KEY)) !== o.h) {
      o.n++; if (o.n >= 5) await env.ACC.delete("uotp"); else await env.ACC.put("uotp", JSON.stringify(o), { expirationTtl: 600 });
      throw new Error("الكود غلط");
    }
    await env.ACC.delete("uotp");
    const r = (await ghReleases(env, st)).find((x) => x.v === String(f.v || ""));
    if (!r) throw new Error("الإصدار ده مش موجود على GitHub");
    if (!verGt(r.v, relVer(st.lines?.release))) throw new Error("فيه إصدار أحدث منه منشور");
    const t = new Date().toISOString().replace(/\.\d+Z$/, "Z");
    st.lines = st.lines || {};
    st.lines.prevRelease = st.lines.release || "";
    st.lines.release = await asign(env, "TRIOUP", { v: r.v, notes: r.notes, sha: r.sha, n: r.n, to: "*", u: r.u, t }, true);
    st.lines.relFrom = "phone";
    await bumpRev(st); await publish(env, st, "نشر إصدار " + r.v + " من الموبايل"); await stPut(env, st);
    try { await mail(env, [adminEmail(env)], "Trio: اتنشر إصدار " + r.v + " من الموبايل", `اتنشر إصدار ${r.v} لكل العملاء من لوحة الموبايل (${cairo()}).\nالعملاء هيسطّبوه بعد ساعتين.\nلو مش انت: افتح لوحة الموبايل ← الإعدادات ← التحديثات ← «تراجع عن النشر»، واقفل «مفتاح النشر» من Trio License.`, "Trio"); } catch (e) { /* */ }
    return "اتنشر " + r.v + " ✔ — العملاء هيسطّبوه بعد ساعتين، ووصلك إيميل بالنشر";
  }
  throw new Error("طلب غلط");
}

// ---------------------------------------------------------------- the command channel (same lines as Trio License)
function feedText(st) {
  const L = ["Trio command channel. Every line is signed with Trio's key.", st.lines?.control || ""];
  for (const m of Object.keys(st.clients || {}).sort()) if (st.clients[m].Order) L.push(st.clients[m].Order);
  if (st.lines?.triop) L.push(st.lines.triop);
  for (const k of st.lines?.keys || []) L.push(k);
  if (st.lines?.release) L.push(st.lines.release);
  return L.filter(Boolean).join("\n") + "\n";
}
async function publish(env, st, why) {
  const g = st.gh || {};
  if (!env.GH_TOKEN || !g.Owner || !g.Repo) throw new Error("ناقص GH_TOKEN في الـ Worker أو بيانات GitHub من Trio License");
  const api = `https://api.github.com/repos/${g.Owner}/${g.Repo}/contents/feed.txt`;
  const h = { Authorization: "Bearer " + env.GH_TOKEN, "User-Agent": "Trio", Accept: "application/vnd.github+json" };
  const cur = await fetch(api + "?ref=" + encodeURIComponent(g.Branch || "main"), { headers: h });
  const sha0 = cur.ok ? (await cur.json()).sha : undefined;
  const body = { message: "Trio (موبايل): " + why, content: btoa(String.fromCharCode(...enc.encode(feedText(st)))), branch: g.Branch || "main" };
  if (sha0) body.sha = sha0;
  const r = await fetch(api, { method: "PUT", headers: { ...h, "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!r.ok) throw new Error("GitHub " + r.status + " " + (await r.text()).slice(0, 120));
  st.published = cairo();
}

// ---------------------------------------------------------------- codes (same payloads as Trio License)
function licPayload(st, c, noName) {
  const p = { m: c.Machine, e: c.Lifetime ? "9999-12-31" : c.Expires, g: c.Grace || 7, p: c.Plan, t: iso() };
  if (!noName) p.c = c.Name;
  if (c.Business) p.b = c.Business;
  if (c.Self) p.s = true;
  if (c.Lifetime) p.l = true;
  if (c.LifetimeUpdates) p.u = true;
  p.f = st.feed || FEED;
  return p;
}
async function orderCode(env, c, lic) {
  const def = defMods(c.Plan, c.Business), have = new Set(c.Mods || []);
  const x = MODKEYS.filter((k) => have.has(k) && !def.has(k)), n = MODKEYS.filter((k) => def.has(k) && !have.has(k) && !ALWAYS.includes(k));
  const p = { m: c.Machine, p: c.Plan, b: c.Business, x, n, t: iso() };
  if (lic) p.lic = lic;
  return asign(env, "TRIOM", p);
}
const note = (c, s) => { c.Updated = cairo(); c.Log = [c.Updated + " — " + s, ...(c.Log || [])].slice(0, 60); };
const licText = (c) => (c.Lifetime ? "مدى الحياة" : c.Expires ? "لحد " + c.Expires : "لسه مفيش رخصة");
const expired = (c) => !c.Lifetime && c.Expires && c.Expires < today();

// ---------------------------------------------------------------- login: a code to Trio's e-mail, then a 7-day session
async function session(req, env) {
  const m = /(?:^|;\s*)ta=([A-Za-z0-9_-]{20,})/.exec(req.headers.get("Cookie") || "");
  if (!m) return null;
  return (await env.ACC.get("as:" + (await sha(m[1])))) ? m[1] : null;
}
const csrf = async (sid) => (await sha("csrf:" + sid)).slice(0, 24);

const ACSS = `*{box-sizing:border-box}body{margin:0;font-family:system-ui,"Segoe UI",Tahoma,sans-serif;background:#F3F6FA;color:#0B1F3A;font-size:16px;padding-bottom:84px}
header{background:linear-gradient(100deg,#062A4F,#0C4A76 55%,#17698F);color:#fff;padding:14px 16px;display:flex;align-items:center;gap:10px;position:sticky;top:0;z-index:5;box-shadow:0 2px 12px rgba(3,21,43,.3);border-bottom:3px solid #3CC2B5}
header img{width:30px;height:30px;border-radius:8px}header b{font-size:18px}header small{display:block;color:#9BEDE3;font-size:12px}
main{max-width:720px;margin:0 auto;padding:14px}h1{font-size:21px;margin:6px 0 12px}h2{font-size:17px;margin:0 0 10px}
.card{background:#fff;border:1px solid #DFE6EF;border-radius:16px;padding:14px;margin-bottom:12px;box-shadow:0 1px 3px rgba(10,31,58,.05)}
.kpis{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px}.kpi{background:#fff;border:1px solid #DFE6EF;border-radius:14px;padding:12px;border-inline-start:4px solid #3CC2B5}
.kpi b{display:block;font-size:24px}.kpi span{color:#58697F;font-size:13px}.kpi.hero{background:linear-gradient(135deg,#17698F,#0A3A66);color:#fff;border:0}.kpi.hero span{color:#CFE0EC}
label{display:block;font-size:13px;color:#58697F;margin:10px 0 4px}input,select,textarea{width:100%;font:inherit;padding:11px 12px;border:1px solid #DFE6EF;border-radius:12px;background:#fff;color:#0B1F3A}
input:focus,select:focus,textarea:focus{outline:2px solid rgba(23,105,143,.25);border-color:#17698F}
.btn{display:inline-flex;justify-content:center;align-items:center;gap:6px;border:0;border-radius:12px;padding:12px 16px;font:inherit;font-weight:700;background:#17698F;color:#fff;text-decoration:none;cursor:pointer;width:100%;margin-top:12px}
.btn.alt{background:#E3F0F6;color:#0C4A76}.btn.red{background:#B42318}.btn.aq{background:#3CC2B5;color:#04213F}.row{display:flex;gap:8px}.row>*{flex:1}
.list a{display:flex;justify-content:space-between;gap:10px;padding:12px 4px;border-bottom:1px solid #EDF2F7;color:inherit;text-decoration:none}.list a:last-child{border-0}
.list small,.muted{color:#58697F;font-size:13px}.tag{display:inline-block;font-size:12px;padding:2px 8px;border-radius:20px;background:#E3F0F6;color:#0C4A76;margin-inline-start:4px}
.tag.red{background:#FDE8E7;color:#B42318}.tag.ok{background:#E0F6F3;color:#0F7A5C}.tag.warn{background:#FFF4E2;color:#9A5B07}
.msg{background:#E0F6F3;color:#0F5E49;border-radius:12px;padding:10px 12px;margin-bottom:12px}.err{background:#FDE8E7;color:#B42318;border-radius:12px;padding:10px 12px;margin-bottom:12px}
.code{direction:ltr;text-align:left;font:13px/1.5 ui-monospace,Consolas,monospace;word-break:break-all;background:#F5F8FB;border:1px dashed #9DBDD6;border-radius:12px;padding:10px;max-height:150px;overflow:auto}
.mods{display:grid;grid-template-columns:1fr;gap:2px}.mods label{display:flex;gap:8px;align-items:center;color:#0B1F3A;font-size:15px;margin:0;padding:6px 2px}.mods input{width:20px;height:20px}
nav.tabs{position:fixed;bottom:0;inset-inline:0;background:#fff;border-top:1px solid #DFE6EF;display:flex;justify-content:space-around;padding:6px 4px calc(6px + env(safe-area-inset-bottom));z-index:9}
nav.tabs a{flex:1;text-align:center;color:#58697F;text-decoration:none;font-size:11px;padding:4px 0}nav.tabs a i{display:block;font-style:normal;font-size:22px}nav.tabs a.on{color:#17698F;font-weight:700}
details summary{cursor:pointer;font-weight:700;color:#0C4A76}dl{display:grid;grid-template-columns:auto 1fr;gap:6px 12px;margin:0}dt{color:#58697F}dd{margin:0}
@media (prefers-color-scheme:dark){body{background:#07111E;color:#E6EEF8}.card,.kpi,nav.tabs,input,select,textarea{background:#0F1C2E;border-color:#1F3350;color:#E6EEF8}
.list a{border-color:#16263C}.muted,.list small,dt,label{color:#9FB2C8}.mods label{color:#E6EEF8}.code{background:#0B1F3A;border-color:#1F3350}.btn.alt{background:#11283F;color:#8AD6E0}}`;

function apage(title, body, active, extra = "") {
  const tab = (k, ic, t) => `<a href="/admin${k}" class="${active === k ? "on" : ""}"><i>${ic}</i>${t}</a>`;
  return new Response(`<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#0C4A76"><meta name="robots" content="noindex"><link rel="manifest" href="/admin/manifest.webmanifest"><link rel="apple-touch-icon" href="/icon-192.png">
<title>${esc(title)} · Trio</title><style>${ACSS}</style></head><body>
<header><img src="/icon-192.png" alt=""><div><b>Trio</b><small>لوحة التحكم — من الموبايل</small></div></header><main>${body}</main>
${active === null ? "" : `<nav class="tabs">${tab("", "🏠", "الرئيسية")}${tab("/clients", "👥", "العملاء")}${tab("/new", "🔑", "رخصة جديدة")}${tab("/orders", "💳", "الطلبات")}${tab("/settings", "⚙️", "الإعدادات")}</nav>`}
<script>document.querySelectorAll("[data-copy]").forEach(b=>b.onclick=()=>{navigator.clipboard.writeText(document.getElementById(b.dataset.copy).innerText);b.textContent="اتنسخ ✔"});${extra}</script></body></html>`,
    { headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer" } });
}
const redirect = (to, cookie) => new Response(null, { status: 303, headers: { Location: to, ...(cookie ? { "Set-Cookie": cookie } : {}) } });
const codeBox = (id, code, wa) => `<div class="code" id="${id}">${esc(code)}</div><div class="row"><button class="btn alt" type="button" data-copy="${id}">📋 انسخ الكود</button>${wa ? `<a class="btn aq" href="https://wa.me/${wa}?text=${encodeURIComponent(code)}">واتساب للعميل</a>` : ""}</div>`;
const waNum = (p) => { const d = digits(p); return d.length === 11 && d.startsWith("01") ? "2" + d : d.length >= 10 ? d : ""; };

function loginPage(step, msg, errText) {
  const body = step === "code"
    ? `<div class="card"><h1>اكتب الكود</h1><p class="muted">بعتنا كود من 6 أرقام على إيميل Trio — صالح 10 دقايق.</p>${errText ? `<div class="err">${esc(errText)}</div>` : ""}
<form method="post" action="/admin/verify"><input name="code" inputmode="numeric" autocomplete="one-time-code" maxlength="6" pattern="\\d{6}" required autofocus style="font-size:26px;text-align:center;letter-spacing:8px;direction:ltr">
<button class="btn">دخول</button></form><form method="post" action="/admin/login"><button class="btn alt">ابعت كود تاني</button></form></div>`
    : `<div class="card"><h1>دخول لوحة Trio</h1><p class="muted">نفس Trio License بس من الموبايل: العملاء، الرخص، الإيقاف، الطلبات. الدخول بكود بيوصل على إيميل Trio.</p>
${msg ? `<div class="msg">${esc(msg)}</div>` : ""}${errText ? `<div class="err">${esc(errText)}</div>` : ""}<form method="post" action="/admin/login"><button class="btn">ابعتلي كود الدخول</button></form></div>`;
  return apage("دخول", body, null);
}

async function form(req) { const f = await req.formData(); const o = {}; for (const [k, v] of f) (o[k] = o[k] === undefined ? v : [].concat(o[k], v)); return o; }
const arr = (v) => (v === undefined ? [] : [].concat(v));

// ---------------------------------------------------------------- pages
async function presenceAll(env) { try { return await all(env, "m:"); } catch (e) { return {}; } }
const ago = (t) => { if (!t) return ""; const m = Math.round((Date.now() - Date.parse(t)) / 60000); return m < 60 ? `من ${Math.max(m, 1)} دقيقة` : m < 1440 ? `من ${Math.round(m / 60)} ساعة` : `من ${Math.round(m / 1440)} يوم`; };

function setupMissing(env, st) {
  const miss = [];
  if (!st) miss.push("بيانات Trio License: من الكمبيوتر «📱 من الموبايل» ← «فعّل وارفع بياناتي»");
  if (!env.ADMIN_KEY || !env.ADMIN_CERT) miss.push("Secrets في الـ Worker: ADMIN_KEY و ADMIN_CERT (من نفس الصفحة في Trio License)");
  if (!env.GH_TOKEN) miss.push("Secret في الـ Worker: GH_TOKEN (مفتاح GitHub — من نفس الصفحة)");
  return miss;
}

async function homePage(env, st) {
  const miss = setupMissing(env, st);
  if (!st) return apage("الرئيسية", `<div class="card"><h1>لسه مش متوصل</h1><ul>${miss.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>`, "");
  const pres = await presenceAll(env), cs = Object.values(st.clients || {});
  const live = cs.filter((c) => pres[c.Machine] && Date.now() - Date.parse(pres[c.Machine].at) < 36 * 3600e3).length;
  const soon = cs.filter((c) => !c.Lifetime && c.Expires && c.Expires >= today() && c.Expires <= new Date(Date.now() + 30 * 864e5).toISOString().slice(0, 10));
  const orders = Object.values(await all(env, "o:")).filter((o) => o.status === "paid").length;
  const recent = cs.filter((c) => c.Updated).sort((a, b) => (b.Updated > a.Updated ? 1 : -1)).slice(0, 5);
  return apage("الرئيسية", `${miss.length ? `<div class="err">ناقص: ${miss.map(esc).join(" · ")}</div>` : ""}
<div class="kpis"><div class="kpi hero"><span>العملاء</span><b>${cs.length}</b></div><div class="kpi"><span>شغالين آخر يوم ونص</span><b>${live}</b></div>
<div class="kpi"><span>موقوفين</span><b>${Object.keys(st.stopped || {}).length}</b></div><div class="kpi"><span>طلبات أونلاين مدفوعة</span><b>${orders}</b></div></div>
${soon.length ? `<div class="card"><h2>رخص بتخلص خلال 30 يوم</h2><div class="list">${soon.map((c) => `<a href="/admin/c/${c.Machine}"><span>${esc(c.Name)}</span><small>${esc(c.Expires)}</small></a>`).join("")}</div></div>` : ""}
<div class="card"><h2>آخر حاجات اتعملت</h2><div class="list">${recent.map((c) => `<a href="/admin/c/${c.Machine}"><span>${esc(c.Name)}<br><small>${esc((c.Log || [""])[0].split(" — ").slice(1).join(" — "))}</small></span><small>${esc(c.Updated)}</small></a>`).join("") || '<p class="muted">لسه مفيش</p>'}</div></div>
<p class="muted">آخر نشر لقناة الأوامر: ${esc(st.published || "—")} · آخر مزامنة مع الكمبيوتر: ${esc(st.at || "—")}</p>`, "");
}

async function clientsPage(env, st, q) {
  const pres = await presenceAll(env), qq = String(q || "").trim().toLowerCase();
  const cs = Object.values(st.clients || {}).filter((c) => !qq || [c.Name, c.Phone, c.Machine].join(" ").toLowerCase().includes(qq)).sort((a, b) => String(a.Name).localeCompare(String(b.Name), "ar"));
  const row = (c) => { const p = pres[c.Machine]; const stop = (st.stopped || {})[c.Machine] !== undefined;
    return `<a href="/admin/c/${c.Machine}"><span><b>${esc(c.Name)}</b>${stop ? '<span class="tag red">موقوف</span>' : ""}${expired(c) ? '<span class="tag warn">الرخصة خلصت</span>' : ""}<br><small>${esc(CAT.plan_names[c.Plan] || c.Plan || "")} · ${esc(BIZ[c.Business] || "")} · ${esc(licText(c))}</small></span>
<small style="text-align:left">${p ? `${esc(p.v)}<br>${ago(p.at)}` : "مظهرش"}</small></a>`; };
  return apage("العملاء", `<h1>العملاء (${cs.length})</h1><form><input name="q" value="${esc(q || "")}" placeholder="دوّر بالاسم أو التليفون أو كود الجهاز"></form>
<div class="card list" style="margin-top:12px">${cs.map(row).join("") || '<p class="muted">مفيش</p>'}</div>`, "/clients");
}

function modsBox(c) {
  const def = defMods(c.Plan, c.Business), have = new Set(c.Mods || []);
  return `<div class="mods">${MODKEYS.filter((k) => fits(k, c.Business)).map((k) => `<label><input type="checkbox" name="mod" value="${k}"${have.has(k) ? " checked" : ""}${ALWAYS.includes(k) ? " checked disabled" : ""}> ${esc(CAT.names[k])}${def.has(k) ? ' <span class="tag">في الباقة</span>' : ""}</label>`).join("")}</div>`;
}

async function clientPage(env, st, m, sid, msg, errText, code) {
  const c = (st.clients || {})[m];
  if (!c) return redirect("/admin/clients");
  const p = (await presenceAll(env))[m], stopped = (st.stopped || {})[m], tok = await csrf(sid), wa = waNum(c.Phone);
  const sel = (name, opts, v) => `<select name="${name}">${Object.entries(opts).map(([k, t]) => `<option value="${k}"${k === v ? " selected" : ""}>${esc(t)}</option>`).join("")}</select>`;
  const act = (a, inner, btn, cls = "") => `<form method="post"><input type="hidden" name="csrf" value="${tok}"><input type="hidden" name="a" value="${a}">${inner}<button class="btn ${cls}">${btn}</button></form>`;
  return apage(c.Name, `${msg ? `<div class="msg">${esc(msg)}</div>` : ""}${errText ? `<div class="err">${esc(errText)}</div>` : ""}
${code ? `<div class="card"><h2>${esc(code.title)}</h2>${codeBox("cx", code.text, code.wa ? wa : "")}</div>` : ""}
<div class="card"><h1>${esc(c.Name)} ${stopped !== undefined ? '<span class="tag red">موقوف</span>' : ""}</h1><dl>
<dt>كود الجهاز</dt><dd dir="ltr" style="text-align:right">${esc(c.Machine)}</dd><dt>التليفون</dt><dd>${c.Phone ? `<a href="tel:${esc(c.Phone)}">${esc(c.Phone)}</a>${wa ? ` · <a href="https://wa.me/${wa}">واتساب</a>` : ""}` : "—"}</dd>
<dt>الباقة</dt><dd>${esc(CAT.plan_names[c.Plan] || c.Plan || "")} · ${esc(BIZ[c.Business] || "")}</dd><dt>الرخصة</dt><dd>${esc(licText(c))}${expired(c) ? ' <span class="tag warn">خلصت</span>' : ""}</dd>
<dt>التحديثات</dt><dd>${c.LifetimeUpdates ? "مدى الحياة" : "مع الاشتراك"}</dd><dt>الإصدار عنده</dt><dd>${p ? `${esc(p.v)} · ${ago(p.at)}` : "مظهرش لسه"}</dd></dl></div>
<details class="card"><summary>🔑 الرخصة: تجديد / مدى الحياة</summary>${act("licence", `<label>نوعها</label>${sel("kind", { renew: "تجديد", lifetime: "مدى الحياة", same: "زي ما هي" }, "renew")}
<label>مدة التجديد</label>${sel("months", { 1: "شهر", 3: "3 شهور", 6: "6 شهور", 12: "سنة", 24: "سنتين" }, "12")}
<label><input type="checkbox" name="updates" value="1"${c.LifetimeUpdates ? " checked" : ""} style="width:auto"> التحديثات مدى الحياة</label>`, "طلّع الرخصة وابعتها")}</details>
<details class="card"><summary>⭐ الباقة والنشاط والمميزات</summary>${act("mods", `<label>الباقة</label>${sel("plan", CAT.plan_names, c.Plan)}<label>النشاط</label>${sel("biz", BIZ, c.Business)}
<label><input type="checkbox" name="reset" value="1" style="width:auto"> رجّع مميزات الباقة الافتراضية</label><label>المميزات</label>${modsBox(c)}`, "ابعت للعميل")}</details>
<details class="card"><summary>${stopped !== undefined ? "▶️ تشغيل البرنامج تاني" : "⛔ إيقاف البرنامج"}</summary>${stopped !== undefined
    ? `<p class="muted">موقوف: ${esc(stopped)}</p>` + act("resume", "", "شغّله تاني", "aq")
    : act("stop", `<label>الرسالة اللي هتظهرله</label><textarea name="why" rows="2" required>البرنامج موقوف مؤقتًا — كلّمنا</textarea>`, "اوقفه", "red")}</details>
<details class="card"><summary>🛠 كود دخول مطور</summary>${act("dev", `<label>كود الطلب اللي ظاهر عند العميل (XXXX-XXXX)</label><input name="ch" dir="ltr" required pattern="[0-9A-Fa-f]{4}-?[0-9A-Fa-f]{4}">`, "طلّع الكود")}</details>
${bkBox(await bkList(env, m))}
<details class="card"><summary>✏️ الاسم والتليفون</summary>${act("info", `<label>الاسم</label><input name="name" value="${esc(c.Name)}" required><label>التليفون</label><input name="phone" value="${esc(c.Phone || "")}" inputmode="tel">`, "احفظ", "alt")}</details>
<div class="card"><h2>السجل</h2><div class="muted" style="white-space:pre-line;font-size:13px">${esc((c.Log || []).slice(0, 15).join("\n")) || "—"}</div></div>`, "/clients");
}

// ---- recovery keys of the encrypted Drive backups (sent once by each copy; read only by Trio)
const BKRE = /^[A-HJ-NP-Z2-9]{4}(-[A-HJ-NP-Z2-9]{4}){5}$/;
async function bkList(env, m) { try { return JSON.parse((await env.ACC.get("bk:" + m)) || "[]"); } catch (e) { return []; } }
function bkBox(list) {
  if (!list.length) return "";
  return `<details class="card"><summary>♻️ مفتاح استرجاع النسخ الاحتياطية</summary><p class="muted">لو جهاز العميل باظ: يرفع آخر ملف من الدرايف في «عندك نسخة احتياطية؟» ويكتب المفتاح ده.</p>${
    list.map((x) => `<p><b dir="ltr" style="font-size:17px;user-select:all">${esc(x.key)}</b><br><small class="muted">${esc(x.shop || "")} · ${esc(String(x.at || "").slice(0, 10))}${x.lic ? "" : " · من نسخة تجريبية"}</small></p>`).join("")}</details>`;
}
async function bkPut(req, env) {
  let b = {};
  try { b = await req.json(); } catch (e) { return err("طلب ناقص"); }
  if (!MACHINE.test(b.m || "") || !BKRE.test(b.key || "")) return err("طلب ناقص");
  const lic = await readLicence(b.lic), genuine = !!(lic && lic.m === b.m);
  const list = await bkList(env, b.m);
  if (list.some((x) => x.key === b.key)) return json({ ok: true });
  // a licensed copy may add its key; without a licence only the first key of a machine is kept (nobody overwrites another's)
  if (!genuine && list.length) return json({ ok: false, error: "محفوظ قبل كده" });
  list.unshift({ key: b.key, shop: String(b.shop || "").slice(0, 80), at: iso(), lic: genuine });
  await env.ACC.put("bk:" + b.m, JSON.stringify(list.slice(0, 5)));
  return json({ ok: true });
}

function newPage(sid, tok, vals = {}, errText = "", code = null) {
  const sel = (name, opts, v) => `<select name="${name}">${Object.entries(opts).map(([k, t]) => `<option value="${k}"${k === v ? " selected" : ""}>${esc(t)}</option>`).join("")}</select>`;
  return apage("رخصة جديدة", `${errText ? `<div class="err">${esc(errText)}</div>` : ""}${code ? `<div class="card"><h2>الكود جاهز ✔</h2><p class="muted">ابعته للعميل يلصقه في «التفعيل».</p>${codeBox("lx", code.text, code.wa)}</div>` : ""}
<div class="card"><h1>رخصة جديدة</h1><form method="post"><input type="hidden" name="csrf" value="${tok}">
<label>اسم العميل / المحل</label><input name="name" required value="${esc(vals.name || "")}"><label>التليفون</label><input name="phone" inputmode="tel" value="${esc(vals.phone || "")}">
<label>كود الجهاز (16 حرف)</label><input name="m" dir="ltr" required value="${esc(vals.m || "")}" placeholder="ABCD-EFGH-IJKL-MNOP" style="text-transform:uppercase">
<div class="row"><div><label>الباقة</label>${sel("plan", CAT.plan_names, vals.plan || "pro")}</div><div><label>النشاط</label>${sel("biz", BIZ, vals.biz || "market")}</div></div>
<div class="row"><div><label>النوع</label>${sel("kind", { year: "اشتراك", lifetime: "مدى الحياة" }, vals.kind || "year")}</div><div><label>المدة</label>${sel("months", { 1: "شهر", 3: "3 شهور", 6: "6 شهور", 12: "سنة", 24: "سنتين" }, vals.months || "12")}</div></div>
<label><input type="checkbox" name="updates" value="1" style="width:auto"${vals.updates ? " checked" : ""}> التحديثات مدى الحياة</label>
<label><input type="checkbox" name="self" value="1" style="width:auto"${vals.self ? " checked" : ""}> تسطيب ذاتي (العميل بيجهّز محله بنفسه)</label>
<button class="btn">طلّع كود التفعيل</button></form></div>`, "/new");
}

async function ordersPage(env, st, tok, msg) {
  const os = Object.values(await all(env, "o:")).sort((a, b) => (String(b.created || "") > String(a.created || "") ? 1 : -1)).slice(0, 60);
  const stx = { paid: ["اتدفع", "ok"], pending: ["مستني الدفع", "warn"], failed: ["فشل", "red"] };
  const row = (o) => { const s = stx[o.status] || [o.status || "—", ""]; const known = o.m && (st.clients || {})[o.m];
    return `<div style="padding:10px 0;border-bottom:1px solid #EDF2F7"><b>${esc(o.name || o.phone || o.id)}</b> <span class="tag ${s[1]}">${esc(s[0])}</span><br>
<small class="muted">${esc(o.id)} · ${esc(CAT.plan_names[o.plan] || o.plan || "")} · ${esc(String(o.total || o.amount || ""))} ج · ${esc(o.phone || "")}${o.m ? " · " + esc(o.m) : " · لسه متفعّلش على جهاز"}</small>
${o.status === "paid" && o.m && o.code && !known ? `<form method="post"><input type="hidden" name="csrf" value="${tok}"><input type="hidden" name="a" value="take"><input type="hidden" name="id" value="${esc(o.id)}"><button class="btn alt">سجّله في العملاء</button></form>` : known ? `<a href="/admin/c/${o.m}" class="muted">عند العملاء ←</a>` : ""}</div>`; };
  return apage("الطلبات", `${msg ? `<div class="msg">${esc(msg)}</div>` : ""}<h1>طلبات الموقع والبرنامج</h1><div class="card">${os.map(row).join("") || '<p class="muted">لسه مفيش طلبات</p>'}</div>`, "/orders");
}

function settingsPage(env, st, tok, msg, errText) {
  const sw = (a, on, t) => `<form method="post"><input type="hidden" name="csrf" value="${tok}"><input type="hidden" name="a" value="${a}"><button class="btn ${on ? "alt" : "aq"}">${on ? "اقفل " + t : "افتح " + t}</button></form>`;
  return apage("الإعدادات", `${msg ? `<div class="msg">${esc(msg)}</div>` : ""}${errText ? `<div class="err">${esc(errText)}</div>` : ""}<h1>الإعدادات</h1>
<div class="card"><h2>💳 الدفع بالفيزا ${st.pay ? '<span class="tag ok">شغال</span>' : '<span class="tag">مقفول</span>'}</h2>${env.SALES_KEY && env.PAYMOB_SECRET ? "" : '<p class="muted">لسه ناقص مفاتيح Paymob / المبيعات في الـ Worker.</p>'}${sw("pay", st.pay, "الدفع")}</div>
<div class="card"><h2>🤖 الذكاء الاصطناعي ${st.ai ? '<span class="tag ok">شغال</span>' : '<span class="tag">مقفول</span>'}</h2>${env.ANTHROPIC_KEY ? "" : '<p class="muted">لسه ناقص ANTHROPIC_KEY في الـ Worker.</p>'}${sw("ai", st.ai, "الذكاء الاصطناعي")}</div>
<div class="card"><h2>📡 قناة الأوامر</h2><p class="muted">آخر نشر: ${esc(st.published || "—")} — بتتنشر لوحدها بعد أي تغيير.</p><form method="post"><input type="hidden" name="csrf" value="${tok}"><input type="hidden" name="a" value="publish"><button class="btn alt">انشرها دلوقتي</button></form></div>
<div class="card"><h2>⬆ التحديثات</h2><p class="muted">المنشور: <b dir="ltr">${esc(relVer(st.lines?.release) || "—")}</b></p><a class="btn alt" href="/admin/updates">افتح التحديثات</a></div>
<div class="card"><h2>🔒 الدخول</h2><p class="muted">الكود بيوصل على ${esc(adminEmail(env))}. الجلسة بتفضل 7 أيام على الموبايل ده.</p><form method="post" action="/admin/logout"><button class="btn red">خروج</button></form></div>`, "/settings");
}

// ---------------------------------------------------------------- actions
async function bumpRev(st) { st.rev = (st.rev || 0) + 1; st.at = cairo(); st.by = "phone"; }
async function triop(env, st) { return st.ping ? asign(env, "TRIOP", { u: st.ping, pay: !!st.pay, ai: !!st.ai, t: iso() }) : ""; }

async function clientAct(env, st, m, f) {
  const c = st.clients[m];
  if (!c) throw new Error("العميل مش موجود");
  const before = JSON.stringify(c);
  let what = "", lic = "", code = null;
  if (f.a === "info") {
    c.Name = String(f.name || c.Name).trim() || c.Name; c.Phone = String(f.phone || "").trim(); note(c, "البيانات اتعدلت (موبايل)");
    await bumpRev(st); await stPut(env, st); return { msg: "اتحفظ ✔" };
  }
  if (f.a === "dev") {
    const ch = String(f.ch || "").toUpperCase().replace(/[^0-9A-F]/g, "");
    if (ch.length !== 8) throw new Error("كود الطلب 8 حروف/أرقام زي AB12-CD34");
    const t = await asign(env, "TRIOD", { t: "dev", m, n: ch.slice(0, 4) + "-" + ch.slice(4), e: new Date(Date.now() + 2 * 3600e3).toISOString().slice(0, 10), at: iso() }); // «t» is the code's type here
    note(c, "كود دخول مطور (موبايل)"); await bumpRev(st); await stPut(env, st);
    return { msg: "الكود صالح النهارده بس — اكتبه عند العميل في «دخول المطور»", code: { title: "كود دخول المطور", text: t } };
  }
  if (f.a === "stop" || f.a === "resume") {
    st.stopped = st.stopped || {};
    if (f.a === "stop") st.stopped[m] = String(f.why || "البرنامج موقوف مؤقتًا").slice(0, 300); else delete st.stopped[m];
    st.lines.control = await asign(env, "TRIOC", { stop: st.stopped, t: iso() });
    note(c, f.a === "stop" ? "اتوقف (موبايل): " + st.stopped[m] : "اتشغّل تاني (موبايل)");
    await bumpRev(st); await publish(env, st, (f.a === "stop" ? "stop " : "resume ") + m); await stPut(env, st);
    return { msg: f.a === "stop" ? "اتبعت ✔ — البرنامج هيقف عنده خلال ساعة" : "اتبعت ✔ — هيشتغل عنده خلال ساعة" };
  }
  if (f.a === "mods") {
    c.Plan = CAT.plan_names[f.plan] ? f.plan : c.Plan; c.Business = BIZ[f.biz] ? f.biz : c.Business;
    const set = f.reset ? defMods(c.Plan, c.Business) : new Set(arr(f.mod).filter((k) => CAT.names[k] && fits(k, c.Business)));
    ALWAYS.forEach((k) => set.add(k)); c.Mods = MODKEYS.filter((k) => set.has(k));
    what = "الباقة " + (CAT.plan_names[c.Plan] || c.Plan) + " — " + (BIZ[c.Business] || "") + " (موبايل)";
  } else if (f.a === "licence") {
    if (f.kind === "lifetime") { c.Lifetime = true; what = "رخصة مدى الحياة"; }
    else if (f.kind === "renew") {
      const months = Math.max(1, parseInt(f.months || "12", 10) || 12), start = new Date(((c.Expires || "") > today() ? c.Expires : today()) + "T00:00:00Z");
      start.setUTCMonth(start.getUTCMonth() + months); c.Lifetime = false; c.Expires = start.toISOString().slice(0, 10); what = "تجديد لحد " + c.Expires;
    } else what = "الرخصة زي ما هي";
    c.LifetimeUpdates = f.updates === "1";
    if (c.LifetimeUpdates) what += " + التحديثات مدى الحياة";
    lic = await asign(env, "TRIO1", licPayload(st, c));
    what += " (موبايل)";
  } else throw new Error("طلب مش معروف");
  const prev = c.Order;
  c.Order = await orderCode(env, c, lic ? await asign(env, "TRIO1", licPayload(st, c, true)) : "");
  await bumpRev(st);
  try { await publish(env, st, "order " + m); } catch (e) { Object.assign(c, JSON.parse(before)); c.Order = prev; throw e; }
  note(c, what);
  if (lic) { st.licences = st.licences || []; st.licences.push({ Date: cairo(), Client: c.Name, Machine: m, Expires: c.Lifetime ? "9999-12-31" : c.Expires, Plan: c.Plan, Code: lic, Grace: c.Grace || 7 }); code = { title: "كود الرخصة الجديد (لو العميل محتاجه يدوي)", text: lic, wa: true }; }
  await stPut(env, st);
  return { msg: "اتبعت ✔ — بيتفعّل عند العميل خلال ساعة", code };
}

async function newLicence(env, st, f) {
  const m = String(f.m || "").toUpperCase().replace(/\s/g, "");
  const name = String(f.name || "").trim();
  if (!name || !MACHINE.test(m)) throw new Error("اكتب اسم العميل وكود الجهاز صح (16 حرف زي ABCD-EFGH-IJKL-MNOP)");
  st.clients = st.clients || {};
  const isNew = !st.clients[m];
  const c = st.clients[m] || (st.clients[m] = { Machine: m, Created: cairo(), Grace: 7, Log: [] });
  c.Name = name; if (f.phone) c.Phone = String(f.phone).trim();
  c.Plan = CAT.plan_names[f.plan] ? f.plan : "pro"; c.Business = BIZ[f.biz] ? f.biz : "market"; c.Self = f.self === "1";
  c.Lifetime = f.kind === "lifetime"; c.LifetimeUpdates = f.updates === "1"; c.Grace = c.Grace || 7;
  if (!c.Lifetime) { const d = new Date(today() + "T00:00:00Z"); d.setUTCMonth(d.getUTCMonth() + (parseInt(f.months || "12", 10) || 12)); c.Expires = d.toISOString().slice(0, 10); }
  if (isNew || c.Self || !(c.Mods || []).length) c.Mods = MODKEYS.filter((k) => defMods(c.Plan, c.Business).has(k));
  const code = await asign(env, "TRIO1", licPayload(st, c));
  note(c, "رخصة " + licText(c) + (c.LifetimeUpdates ? " + التحديثات مدى الحياة" : "") + (c.Self ? " (تسطيب ذاتي)" : "") + " (موبايل)");
  st.licences = st.licences || []; st.licences.push({ Date: cairo(), Client: name, Machine: m, Expires: c.Lifetime ? "9999-12-31" : c.Expires, Plan: c.Plan, Code: code, Grace: c.Grace });
  await bumpRev(st); await stPut(env, st);
  return { text: code, wa: waNum(c.Phone) };
}

async function takeOrder(env, st, id) {
  const o = await getOrder(env, id);
  if (!o || o.status !== "paid" || !o.m || !o.code) throw new Error("الطلب ده لسه متفعّلش على جهاز");
  const lic = fromB64u(o.code.split(".")[1]);
  st.clients = st.clients || {};
  const c = st.clients[o.m] || (st.clients[o.m] = { Machine: o.m, Name: o.name || lic.c || o.phone, Phone: o.phone, Grace: 7, Created: cairo(), Log: [] });
  c.Phone = c.Phone || o.phone; c.Plan = lic.p; c.Business = lic.b; c.Lifetime = !!lic.l; c.LifetimeUpdates = !!lic.u; c.Self = c.Self || !!lic.s;
  if (!lic.l) c.Expires = lic.e;
  const set = defMods(lic.p, lic.b); (c.Mods || []).forEach((k) => set.add(k)); (lic.x || []).forEach((k) => set.add(k)); c.Mods = MODKEYS.filter((k) => set.has(k));
  note(c, `💳 اشترى أونلاين (${o.id})`);
  st.salesDone = st.salesDone || {}; st.salesDone[o.id] = true;
  await bumpRev(st); await stPut(env, st);
}

// ---------------------------------------------------------------- router
async function adminRoutes(req, env, url) {
  const path = url.pathname.replace(/\/+$/, "") || "/admin";
  if (path === "/admin/manifest.webmanifest") return new Response(JSON.stringify({ name: "Trio — لوحة التحكم", short_name: "Trio", start_url: "/admin", display: "standalone", dir: "rtl", lang: "ar",
    background_color: "#F3F6FA", theme_color: "#0C4A76", icons: [{ src: "/icon-192.png", sizes: "192x192", type: "image/png" }, { src: "/icon-512.png", sizes: "512x512", type: "image/png" }] }), { headers: { "Content-Type": "application/manifest+json" } });
  // Trio License on the computer: pull / push the register (Secret KEY)
  if (path === "/admin/sync/pull" || path === "/admin/sync/push") {
    if (req.method !== "POST") return err("POST", 405);
    let b; try { b = await req.json(); } catch (e) { return err("bad"); }
    if (!env.KEY || b.k !== env.KEY) return err("KEY غلط", 403);
    const cur = await stGet(env);
    if (path === "/admin/sync/pull") return json({ state: cur });
    if (cur && (b.base || 0) !== (cur.rev || 0)) return json({ conflict: true, state: cur }, 409);
    const st = b.state || {};
    st.rev = (cur?.rev || 0) + 1; st.at = cairo(); st.by = "pc";
    st.salesDone = { ...(cur?.salesDone || {}), ...(st.salesDone || {}) };
    let pubErr = "";
    if (b.publish) { try { await publish(env, st, b.why || "Trio License"); } catch (e) { pubErr = String(e.message || e); } }
    await stPut(env, st);
    return json({ rev: st.rev, published: st.published || "", error: pubErr });
  }
  if (!env.KEY || !env.BREVO_KEY || !adminEmail(env)) return page("لوحة Trio", "اللوحة محتاجة KEY و BREVO_KEY في الـ Worker الأول.");
  const sid = await session(req, env);
  if (path === "/admin/login" && req.method === "POST") {
    if ((await bump(env, "al:" + new Date().toISOString().slice(0, 13), 3600)) > 8) return loginPage("", "", "طلبات كتير — استنى ساعة");
    const code = String(crypto.getRandomValues(new Uint32Array(1))[0] % 1000000).padStart(6, "0");
    await env.ACC.put("aotp", JSON.stringify({ h: await sha(code + env.KEY), n: 0 }), { expirationTtl: 600 });
    try { await mail(env, [adminEmail(env)], "كود دخول لوحة Trio: " + code, `كود الدخول من الموبايل: ${code}\nصالح 10 دقايق. لو مش انت اللي طلبته، تجاهل الرسالة.`, "Trio"); }
    catch (e) { return loginPage("", "", "مقدرتش أبعت الإيميل — راجع BREVO_KEY"); }
    return loginPage("code");
  }
  if (path === "/admin/verify" && req.method === "POST") {
    const f = await form(req), o = JSON.parse((await env.ACC.get("aotp")) || "null");
    if (!o) return loginPage("", "", "الكود خلص — اطلب واحد جديد");
    if (o.h !== (await sha(String(f.code || "").trim() + env.KEY))) {
      o.n++; if (o.n >= 5) await env.ACC.delete("aotp"); else await env.ACC.put("aotp", JSON.stringify(o), { expirationTtl: 600 });
      return loginPage(o.n >= 5 ? "" : "code", "", o.n >= 5 ? "غلط كتير — اطلب كود جديد" : `الكود غلط — فاضل ${5 - o.n} محاولات`);
    }
    await env.ACC.delete("aotp");
    const s = b64u(crypto.getRandomValues(new Uint8Array(24)));
    await env.ACC.put("as:" + (await sha(s)), cairo(), { expirationTtl: 7 * 86400 });
    return redirect("/admin", `ta=${s}; Path=/admin; Max-Age=${7 * 86400}; HttpOnly; Secure; SameSite=Strict`);
  }
  if (path === "/admin/logout" && req.method === "POST") {
    if (sid) await env.ACC.delete("as:" + (await sha(sid)));
    return redirect("/admin", "ta=; Path=/admin; Max-Age=0; HttpOnly; Secure; SameSite=Strict");
  }
  if (!sid) return loginPage("");
  const st = await stGet(env), tok = await csrf(sid);
  const miss = setupMissing(env, st);
  if (req.method === "POST") {
    const f = await form(req);
    if (f.csrf !== tok) return new Response("طلب غلط", { status: 403 });
    if (!st || miss.length) return homePage(env, st);
    try {
      if (path === "/admin/new") return newPage(sid, tok, {}, "", await newLicence(env, st, f));
      if (path.startsWith("/admin/c/")) { const r = await clientAct(env, st, path.slice(9), f); return clientPage(env, st, path.slice(9), sid, r.msg, "", r.code); }
      if (path === "/admin/orders") { await takeOrder(env, st, f.id); return ordersPage(env, st, tok, "اتسجّل ✔"); }
      if (path === "/admin/updates") return updatesPage(env, (await stGet(env)) || st, tok, await updatesAct(env, st, f));
      if (path === "/admin/settings") {
        if (f.a === "pay" || f.a === "ai") { st[f.a] = !st[f.a]; st.lines.triop = await triop(env, st); await bumpRev(st); await publish(env, st, f.a); await stPut(env, st);
          return settingsPage(env, st, tok, (f.a === "pay" ? "الدفع بالفيزا " : "الذكاء الاصطناعي ") + (st[f.a] ? "اتفتح ✔" : "اتقفل ✔") + " — عند العملاء خلال ساعة"); }
        if (f.a === "publish") { await publish(env, st, "يدوي"); await stPut(env, st); return settingsPage(env, st, tok, "اتنشرت ✔"); }
      }
    } catch (e) {
      const m = String(e.message || e);
      if (path === "/admin/new") return newPage(sid, tok, f, m);
      if (path.startsWith("/admin/c/")) return clientPage(env, (await stGet(env)) || st, path.slice(9), sid, "", m);
      if (path === "/admin/settings") return settingsPage(env, st, tok, "", m);
      if (path === "/admin/updates") return updatesPage(env, (await stGet(env)) || st, tok, "", m);
      return apage("غلط", `<div class="err">${esc(m)}</div>`, "");
    }
    return redirect(path);
  }
  if (path === "/admin" || !st) return homePage(env, st);
  if (path === "/admin/clients") return clientsPage(env, st, url.searchParams.get("q"));
  if (path.startsWith("/admin/c/")) return clientPage(env, st, path.slice(9), sid);
  if (path === "/admin/new") return newPage(sid, tok);
  if (path === "/admin/orders") return ordersPage(env, st, tok);
  if (path === "/admin/settings") return settingsPage(env, st, tok);
  if (path === "/admin/updates") return updatesPage(env, st, tok);
  return redirect("/admin");
}

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    const q = url.searchParams;
    if (req.method === "OPTIONS") return new Response(null, { headers: cors });
    // flood guard (Workers rate-limit binding RL, per address; Telegram and Paymob call from shared addresses → not counted)
    if (env.RL && url.pathname !== "/tg" && url.pathname !== "/paymob") {
      const ip = req.headers.get("CF-Connecting-IP") || "?";
      const { success } = await env.RL.limit({ key: ip });
      if (!success) return new Response(JSON.stringify({ error: "طلبات كتير — استنى دقيقة" }), { status: 429, headers: { "Content-Type": "application/json; charset=utf-8", "Retry-After": "60", ...cors } });
    }
    if (url.pathname === "/admin" || url.pathname.startsWith("/admin/")) return adminRoutes(req, env, url); // Trio's phone app
    if (req.method === "POST" && url.pathname === "/ping") {
      let d = {};
      try { d = await req.json(); } catch (e) { return new Response("bad", { status: 400 }); }
      if (!MACHINE.test(d.m || "")) return new Response("bad", { status: 400 });
      const cut = (x, n) => String(x || "").slice(0, n);
      const rec = { v: cut(d.v, 20), p: cut(d.p, 12), b: cut(d.b, 12), s: cut(d.s, 12), n: cut(d.n, 80), ph: cut(d.ph, 30), at: new Date().toISOString() };
      await env.ACC.put("m:" + d.m, JSON.stringify(rec));
      return new Response("ok");
    }
    if (url.pathname === "/list" && env.KEY && q.get("k") === env.KEY) return json(await all(env, "m:"));
    if (url.pathname === "/sales" && env.KEY && q.get("k") === env.KEY) return json(await all(env, "o:"));
    if (url.pathname === "/info") return json({ pay: !!(env.SALES_KEY && env.PAYMOB_SECRET), ai: !!env.ANTHROPIC_KEY, catalog: CAT,
      mobile: true, db: !!env.DB, tg: env.TG_TOKEN ? await tgBot(env) : "", mail: !!(env.BREVO_KEY && env.MAIL_FROM) });
    if (req.method === "POST" && url.pathname === "/snap") return snap(req, env);
    if (req.method === "POST" && url.pathname === "/menu") return menuPut(req, env);
    if (req.method === "GET" && url.pathname.startsWith("/menu/")) return menuGet(env, url.pathname.slice(6));
    if (req.method === "POST" && url.pathname === "/notify") return notify(req, env);
    if (req.method === "POST" && url.pathname === "/bk") return bkPut(req, env);
    if (req.method === "POST" && ["/m/q", "/m/pair", "/m/unpair", "/m/cat"].includes(url.pathname)) return ownProg(req, env, url);
    if (url.pathname === "/bk/get" && env.KEY && q.get("k") === env.KEY) return json({ list: await bkList(env, String(q.get("m") || "")) });
    if (req.method === "POST" && url.pathname === "/tg") return tgHook(req, env);
    if (req.method === "POST" && url.pathname === "/tg/status") {
      let b = {};
      try { b = await req.json(); } catch (e) { return err("طلب ناقص"); }
      if (!KEYRE.test(b.k || "")) return err("طلب ناقص");
      return json({ chats: (await tgChats(env, await sidOf(b.k))).length, bot: env.TG_TOKEN ? await tgBot(env) : "" });
    }
    if (url.pathname === "/tg/setup") return tgSetup(env, url); // only points Trio's bot at this Worker — safe to call again
    if (url.pathname === "/mobile/stats" && env.KEY && q.get("k") === env.KEY) {
      const d = await db(env);
      const n = d ? (await d.prepare("SELECT COUNT(*) AS n, SUM(at > ?1) AS live FROM snaps").bind(new Date(Date.now() - 86400e3).toISOString()).first()) : null;
      return json({ db: !!d, shops: n ? n.n : null, live24h: n ? n.live : null, tg: env.TG_TOKEN ? await tgBot(env) : "", mail: !!(env.BREVO_KEY && env.MAIL_FROM) });
    }
    { const r = await mobileRoutes(req, env, url); if (r) return r; }
    if (req.method === "POST" && url.pathname === "/profile") return profile(req, env);
    if (req.method === "POST" && url.pathname === "/ask") return ask(req, env);
    if (url.pathname === "/activities" && env.KEY && q.get("k") === env.KEY) {
      const out = await all(env, "bp:"), m = today().slice(0, 7);
      return json({ list: out, usage: { profiles: +((await env.ACC.get("us:p:" + m)) || 0), questions: +((await env.ACC.get("us:a:" + m)) || 0), month: m } });
    }
    if (req.method === "POST" && url.pathname === "/activities/delete" && env.KEY && q.get("k") === env.KEY) {
      await env.ACC.delete("bp:" + String(q.get("id") || "")); return json({ ok: true });
    }
    if (req.method === "POST" && url.pathname === "/checkout") return checkout(req, env, url);
    if (req.method === "POST" && url.pathname === "/paymob") {
      let d = {};
      try { d = await req.json(); } catch (e) { return new Response("bad", { status: 400 }); }
      const t = d.obj || {};
      if (d.type && d.type !== "TRANSACTION") return new Response("ok");
      if (!(await hmacOK(env, HMAC_FIELDS.map((f) => String(pick(t, f) ?? "")), q.get("hmac")))) return new Response("bad hmac", { status: 401 });
      await settle(env, t);
      return new Response("ok");
    }
    if (url.pathname === "/back") {
      let o = await getOrder(env, q.get("o") || "");
      if (!o) return page("Trio", "الطلب ده مش موجود.");
      // the browser back from Paymob carries the result signed the same way: settle now if the webhook is late
      const gv = (f) => (f === "order.id" ? q.get("order") ?? q.get("order_id") ?? "" : q.get(f) ?? "");
      if (q.get("hmac") && o.status === "pending" && (await hmacOK(env, HMAC_FIELDS.map(gv), q.get("hmac")))) {
        o = (await settle(env, { amount_cents: q.get("amount_cents"), currency: q.get("currency"), success: q.get("success"), pending: q.get("pending"),
          is_voided: q.get("is_voided"), is_refunded: q.get("is_refunded"), id: q.get("id"), order: { id: gv("order.id"), merchant_order_id: o.id + "_" } })) || o;
      }
      const site = (env.SITE || "https://trioerp.pages.dev").replace(/\/$/, "");
      if (o.src === "app") {
        const go = o.back ? o.back + "?id=" + o.id : "";
        if (o.status === "failed") return page("❌ الدفع ماتمّش", "مااتخصمش منك حاجة. ارجع للبرنامج وجرّب تاني.", go);
        return page("✔ شكرًا", "ارجع للبرنامج — باقتك بتتفعّل لوحدها خلال دقيقة. رقم طلبك " + o.id, go);
      }
      return Response.redirect(site + "/?order=" + encodeURIComponent(o.id) + "&st=" + o.status, 302);
    }
    if (url.pathname === "/order") {
      const o = await getOrder(env, q.get("id") || "");
      if (!o) return json({ status: "missing" });
      const mine = o.m && o.m === q.get("m");
      return json({ id: o.id, status: o.status, total: o.total, lines: o.lines, code: mine ? o.code || "" : "", claim: o.status === "paid" && !o.m });
    }
    if (req.method === "POST" && url.pathname === "/claim") {
      let b = {};
      try { b = await req.json(); } catch (e) { return err("طلب ناقص"); }
      const o = await getOrder(env, String(b.id || "").trim().toUpperCase());
      if (!o || digits(o.phone).slice(-9) !== digits(b.phone).slice(-9)) return err("رقم الطلب أو التليفون غلط", 404);
      if (o.status !== "paid") return err(o.status === "failed" ? "الطلب ده الدفع بتاعه ماتمّش" : "الطلب ده لسه مااتدفعش");
      if (!MACHINE.test(b.m || "")) return err("كود الجهاز غلط");
      if (o.m && o.m !== b.m) return err("الطلب ده اتفعّل على جهاز تاني — كلّم Trio", 409);
      if (!o.code) { o.m = b.m; o.code = await sign(env, licence(o, b.m, true)); o.claimed = iso(); await putOrder(env, o); }
      return json({ code: o.code, rep: o.rep || {} });
    }
    return new Response("Trio", { status: 404, headers: cors });
  },
};

async function all(env, prefix) {
  const out = {};
  let cursor;
  do {
    const l = await env.ACC.list({ prefix, cursor });
    for (const k of l.keys) out[k.name.slice(prefix.length)] = JSON.parse(await env.ACC.get(k.name));
    cursor = l.list_complete ? null : l.cursor;
  } while (cursor);
  return out;
}
