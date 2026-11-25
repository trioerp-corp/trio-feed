package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"
)

// Proxy: 127.0.0.1:8010 → the owner PC. Same origin for the till window, so the till's storage (its saved sales and the
// device key) and the cookies of the hall / clinic screens work as on the owner PC. The till page and its files are kept
// in cache\ so the till opens when the owner PC is off; /pos/api/* then answers 503 and the till keeps its sales to send later.
type Proxy struct {
	finder  *Finder
	pair    *Pair
	cache   string
	tr      *http.Transport
	quit    func()
	open    func()
	mu      sync.Mutex
	pending int // unsent papers the till reports (boot.js → /__trio/q)
}

func newProxy(f *Finder, p *Pair, cacheDir string) *Proxy {
	os.MkdirAll(cacheDir, 0755)
	return &Proxy{finder: f, pair: p, cache: cacheDir, tr: &http.Transport{
		Proxy:                 nil,
		DialContext:           (&net.Dialer{Timeout: 3 * time.Second}).DialContext,
		ResponseHeaderTimeout: 90 * time.Second,
		DisableCompression:    true,
		MaxIdleConnsPerHost:   8,
		IdleConnTimeout:       60 * time.Second,
	}}
}

// cacheable: what the till needs to open without the owner PC
func cacheable(path string) bool {
	switch path {
	case "/cashier/", "/pos/", "/pos/display/", "/pos/sw.js", "/favicon.ico":
		return true
	}
	return strings.HasPrefix(path, "/static/")
}

var hop = map[string]bool{"Connection": true, "Keep-Alive": true, "Proxy-Authenticate": true, "Proxy-Authorization": true,
	"Te": true, "Trailer": true, "Transfer-Encoding": true, "Upgrade": true, "Accept-Encoding": true, "Proxy-Connection": true}

type entry struct {
	Status   int    `json:"status"`
	Type     string `json:"type"`
	Location string `json:"location,omitempty"`
	Saved    string `json:"saved"`
}

func (p *Proxy) key(r *http.Request) string {
	h := sha256.Sum256([]byte(r.URL.Path + "?" + r.URL.RawQuery))
	return hex.EncodeToString(h[:16])
}

func (p *Proxy) save(k string, e entry, body []byte) {
	e.Saved = time.Now().Format(time.RFC3339)
	meta, _ := json.Marshal(e)
	b, m := filepath.Join(p.cache, k+".body"), filepath.Join(p.cache, k+".json")
	if os.WriteFile(b+".new", body, 0644) != nil || os.WriteFile(m+".new", meta, 0644) != nil {
		return
	}
	os.Remove(b)
	os.Rename(b+".new", b)
	os.Remove(m)
	os.Rename(m+".new", m)
}

func (p *Proxy) load(k string) (entry, []byte, bool) {
	var e entry
	meta, err := os.ReadFile(filepath.Join(p.cache, k+".json"))
	if err != nil || json.Unmarshal(meta, &e) != nil {
		return e, nil, false
	}
	body, err := os.ReadFile(filepath.Join(p.cache, k+".body"))
	return e, body, err == nil
}

func isHTML(ct string) bool { return strings.HasPrefix(strings.ToLower(ct), "text/html") }

// inject: the small script that hands the device key to the till and shows the connection status
func inject(body []byte) []byte {
	tag := []byte(`<script src="/__trio/boot.js"></script>`)
	low := bytes.ToLower(body)
	if i := bytes.Index(low, []byte("<head>")); i >= 0 {
		i += len("<head>")
		return append(append(append([]byte{}, body[:i]...), tag...), body[i:]...)
	}
	return append(tag, body...)
}

func (p *Proxy) write(w http.ResponseWriter, e entry, body []byte, from string) {
	if isHTML(e.Type) {
		body = inject(body)
		w.Header().Set("Cache-Control", "no-store")
	}
	if e.Type != "" {
		w.Header().Set("Content-Type", e.Type)
	}
	if e.Location != "" {
		w.Header().Set("Location", e.Location)
	}
	w.Header().Set("X-Trio-From", from)
	w.Header().Set("Content-Length", strconv.Itoa(len(body)))
	w.WriteHeader(e.Status)
	w.Write(body)
}

func (p *Proxy) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if strings.HasPrefix(r.URL.Path, "/__trio/") {
		p.local(w, r)
		return
	}
	k := p.key(r)
	useCache := r.Method == http.MethodGet && cacheable(r.URL.Path)
	addr := p.finder.Current()
	if addr != "" {
		if p.forward(w, r, addr, k, useCache) {
			return
		}
		p.finder.Lost(addr)
	}
	// the owner PC is not reachable
	if useCache {
		if e, body, ok := p.load(k); ok {
			p.write(w, e, body, "cache")
			return
		}
	}
	if strings.HasPrefix(r.URL.Path, "/pos/api/") || strings.Contains(r.Header.Get("Accept"), "application/json") {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusServiceUnavailable)
		json.NewEncoder(w).Encode(map[string]any{"error": "لا يوجد اتصال بجهاز المحل الآن", "offline": true})
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(http.StatusServiceUnavailable)
	io.WriteString(w, offlinePage)
}

// forward: false when the owner PC could not be reached (nothing was written then)
func (p *Proxy) forward(w http.ResponseWriter, r *http.Request, addr, k string, useCache bool) bool {
	var body io.Reader
	var reqBody []byte
	if r.Body != nil && r.Method != http.MethodGet && r.Method != http.MethodHead {
		b, err := io.ReadAll(io.LimitReader(r.Body, 64<<20))
		if err != nil {
			http.Error(w, "bad request", http.StatusBadRequest)
			return true
		}
		reqBody = b
		body = bytes.NewReader(b)
	}
	out, err := http.NewRequest(r.Method, "http://"+addr+r.URL.RequestURI(), body)
	if err != nil {
		http.Error(w, "bad request", http.StatusBadRequest)
		return true
	}
	if reqBody != nil {
		out.ContentLength = int64(len(reqBody))
	}
	for h, v := range r.Header {
		if !hop[h] {
			out.Header[h] = v
		}
	}
	out.Host = r.Host // Django sees 127.0.0.1:8010 — the same as the browser's Origin (CSRF) and its redirects stay here
	out.Header.Set("X-Forwarded-For", "127.0.0.1")
	out.Header.Set("X-Trio-Cashier", "1")
	res, err := p.tr.RoundTrip(out)
	if err != nil {
		logLine("الطلب لم يصل إلى جهاز المحل: " + r.URL.Path + " — " + err.Error())
		return false
	}
	defer res.Body.Close()
	ct := res.Header.Get("Content-Type")
	keep := useCache && (res.StatusCode == 200 || res.StatusCode == 301 || res.StatusCode == 302)
	if keep || isHTML(ct) {
		b, err := io.ReadAll(io.LimitReader(res.Body, 48<<20))
		if err != nil {
			return false
		}
		if keep {
			p.save(k, entry{Status: res.StatusCode, Type: ct, Location: res.Header.Get("Location")}, b)
		}
		for h, v := range res.Header {
			if !hop[h] && h != "Content-Length" {
				w.Header()[h] = v
			}
		}
		p.write(w, entry{Status: res.StatusCode, Type: ct, Location: res.Header.Get("Location")}, b, "server")
		return true
	}
	for h, v := range res.Header {
		if !hop[h] {
			w.Header()[h] = v
		}
	}
	w.WriteHeader(res.StatusCode)
	io.Copy(w, res.Body)
	return true
}

// local: /__trio/… — answered by this program itself
func (p *Proxy) local(w http.ResponseWriter, r *http.Request) {
	switch r.URL.Path {
	case "/__trio/status":
		online, stopped, addr := p.finder.Status()
		p.mu.Lock()
		n := p.pending
		p.mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("Cache-Control", "no-store")
		json.NewEncoder(w).Encode(map[string]any{"trio_cashier": 1, "online": online, "stopped": stopped, "server": addr,
			"device": p.pair.DeviceName, "shop": p.pair.Shop, "pending": n})
	case "/__trio/boot.js":
		w.Header().Set("Content-Type", "application/javascript; charset=utf-8")
		w.Header().Set("Cache-Control", "no-store")
		tok, _ := json.Marshal(p.pair.Token)
		mark, _ := json.Marshal(p.pair.ShopID + ":" + strconv.Itoa(p.pair.Device) + ":" + p.pair.Made)
		fmt.Fprintf(w, bootJS, tok, mark)
	case "/__trio/q": // the till's count of unsent papers (for the status and the uninstall warning)
		if r.Method == http.MethodPost {
			n, _ := strconv.Atoi(r.URL.Query().Get("n"))
			p.mu.Lock()
			p.pending = n
			p.mu.Unlock()
		}
		w.WriteHeader(http.StatusNoContent)
	case "/__trio/quit", "/__trio/open":
		// another run of the setup / the shortcut on this PC; a custom header = no web page can ask it (CORS preflight)
		if r.Method != http.MethodPost || r.Header.Get("X-Trio-Local") != "1" {
			http.Error(w, "no", http.StatusForbidden)
			return
		}
		io.WriteString(w, "ok")
		if r.URL.Path == "/__trio/quit" && p.quit != nil {
			go func() { time.Sleep(200 * time.Millisecond); p.quit() }()
		} else if p.open != nil {
			go p.open()
		}
	default:
		http.NotFound(w, r)
	}
}

// boot.js: (1) the device key from pair.json goes into the till's storage once per pairing — never over a key typed by hand
// later, and never while the old key still has unsent papers; (2) a small status badge; (3) the unsent count for this app.
const bootJS = `(function(){var T=%s,P=%s;
try{var g=function(k){try{return JSON.parse(localStorage.getItem(k))}catch(e){return null}},cur=g('pos_token')||'',q=g('pos_queue')||[];
if(cur===T){localStorage.setItem('trio_pair',P)}else if(localStorage.getItem('trio_pair')!==P&&!(cur&&q.length)){localStorage.setItem('pos_token',JSON.stringify(T));localStorage.setItem('trio_pair',P)}}catch(e){}
var W={ar:['متصل بجهاز المحل','غير متصل — ستُرسل الفواتير عند عودة الاتصال','هذا الجهاز موقوف من إعدادات المحل — راجع صاحب المحل'],
en:['Connected to the shop PC','Offline — invoices will be sent when the connection is back','This device is stopped in the shop settings — ask the owner'],
fr:['Connecté au PC du magasin','Hors ligne — les factures seront envoyées au retour de la connexion','Cet appareil est arrêté dans les réglages — voyez le propriétaire']};
function L(){var l=(document.documentElement.lang||'ar').slice(0,2);return W[l]||W.ar}
var b;function badge(){if(b||!document.body)return;b=document.createElement('div');b.id='trioNet';b.setAttribute('role','status');
b.style.cssText='position:fixed;bottom:8px;left:8px;z-index:2147483000;padding:4px 10px;border-radius:999px;font:600 12px "Segoe UI",Tahoma,sans-serif;color:#fff;background:#607D8B;opacity:.92;pointer-events:none;max-width:70vw';
document.body.appendChild(b)}
function tick(){badge();var n=0;try{n=(JSON.parse(localStorage.getItem('pos_queue'))||[]).length+(JSON.parse(localStorage.getItem('pos_moves'))||[]).length}catch(e){}
fetch('/__trio/q?n='+n,{method:'POST'}).catch(function(){});
fetch('/__trio/status',{cache:'no-store'}).then(function(r){return r.json()}).then(function(s){if(!b)return;var t=L();
if(s.online){b.textContent=t[0];b.style.background='#2E7D32'}else if(s.stopped){b.textContent=t[2];b.style.background='#C62828'}else{b.textContent=t[1]+(n?' ('+n+')':'');b.style.background='#E65100'}}).catch(function(){})}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',tick);else tick();setInterval(tick,4000)})();`

const offlinePage = `<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Trio – الكاشير</title>
<style>body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;background:#0A3A66;color:#F3F8FC;font-family:"Segoe UI",Tahoma,sans-serif}
.c{max-width:520px;padding:24px;text-align:center}h1{font-size:24px}p{color:#B6CADF;line-height:1.8}</style></head>
<body><div class="c"><h1>جهاز المحل غير متاح الآن</h1><p>تأكد من تشغيل برنامج Trio على جهاز المحل ومن اتصال هذا الجهاز بشبكة المحل.<br>ستفتح شاشة الكاشير تلقائيًا عند عودة الاتصال.</p>
<p><small>Trio · 01228827915 · 01121460640</small></p></div>
<script>setInterval(function(){fetch('/__trio/status',{cache:'no-store'}).then(function(r){return r.json()}).then(function(s){if(s.online)location.reload()}).catch(function(){})},3000)</script></body></html>`
