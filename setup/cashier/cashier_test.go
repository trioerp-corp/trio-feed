package main

import (
	"archive/zip"
	"bytes"
	"encoding/binary"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func testPair() *Pair {
	return &Pair{V: 1, Shop: "محل", ShopID: "shop1", Device: 3, DeviceName: "كاشير 2", Token: "tok-123", Port: 8000, Made: "2026-11-24T10:00:00"}
}

func mkZip(t *testing.T, files map[string]string) []byte {
	var buf bytes.Buffer
	zw := zip.NewWriter(&buf)
	for n, c := range files {
		w, _ := zw.Create(n)
		io.WriteString(w, c)
	}
	zw.Close()
	return buf.Bytes()
}

func TestPayloadRoundTrip(t *testing.T) {
	dir := t.TempDir()
	pj, _ := json.Marshal(testPair())
	z := mkZip(t, map[string]string{"pair.json": string(pj), "window/TrioWindow.exe": "MZwin", "../evil.txt": "x", "window/../../evil2.txt": "y"})
	stub := []byte("MZ-the-program")
	var f bytes.Buffer
	f.Write(stub)
	f.Write(z)
	binary.Write(&f, binary.LittleEndian, uint64(len(z)))
	f.WriteString(magic)
	path := filepath.Join(dir, "setup.exe")
	os.WriteFile(path, f.Bytes(), 0644)
	got, n, err := readPayload(path)
	if err != nil || n != int64(len(stub)) || !bytes.Equal(got, z) {
		t.Fatalf("payload: %v n=%d", err, n)
	}
	inst := filepath.Join(dir, "inst")
	os.MkdirAll(inst, 0755)
	p, err := extract(got, inst)
	if err != nil || p.Token != "tok-123" || p.ShopID != "shop1" {
		t.Fatalf("extract %v %+v", err, p)
	}
	if b, _ := os.ReadFile(filepath.Join(inst, "window", "TrioWindow.exe")); string(b) != "MZwin" {
		t.Fatal("window not extracted")
	}
	for _, bad := range []string{filepath.Join(dir, "evil.txt"), filepath.Join(dir, "evil2.txt")} {
		if _, err := os.Stat(bad); err == nil {
			t.Fatal("zip slip:", bad)
		}
	}
	if err := copyExe(path, n, filepath.Join(inst, exeName)); err != nil {
		t.Fatal(err)
	}
	if b, _ := os.ReadFile(filepath.Join(inst, exeName)); !bytes.Equal(b, stub) {
		t.Fatal("installed exe must be the program without its payload")
	}
	if _, _, err := readPayload(filepath.Join(inst, exeName)); err == nil {
		t.Fatal("installed exe has a payload")
	}
}

func TestPairRules(t *testing.T) {
	if _, err := parsePair([]byte(`{"v":1,"token":"","shop_id":"s"}`)); err == nil {
		t.Fatal("no token accepted")
	}
	if _, err := extract(mkZip(t, map[string]string{"window/x": "1"}), t.TempDir()); err == nil {
		t.Fatal("zip without pair.json accepted")
	}
	p, _ := parsePair([]byte(`{"v":1,"token":"t","shop_id":"s"}`))
	if p.Port != 8000 {
		t.Fatal("default port")
	}
}

func TestParseReply(t *testing.T) {
	ip := net.ParseIP("192.168.1.5")
	if a, ok := parseReply([]byte(`{"trio":1,"shop":"s1","port":8000,"host":"PC"}`), "s1", ip); !ok || a != "192.168.1.5:8000" {
		t.Fatal(a, ok)
	}
	for _, b := range []string{`{"trio":1,"shop":"s2","port":8000}`, `{"trio":2,"shop":"s1","port":8000}`, `{"trio":1,"shop":"s1","port":0}`, `junk`} {
		if _, ok := parseReply([]byte(b), "s1", ip); ok {
			t.Fatal("accepted", b)
		}
	}
	if _, ok := parseReply([]byte(`{"trio":1,"shop":"","port":8000}`), "", ip); ok {
		t.Fatal("empty shop id accepted")
	}
}

// a fake owner launcher on loopback UDP: answers its own shop id only
func fakeAnswerer(t *testing.T, shop string, port int) string {
	pc, err := net.ListenPacket("udp4", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { pc.Close() })
	go func() {
		buf := make([]byte, 512)
		for {
			n, from, err := pc.ReadFrom(buf)
			if err != nil {
				return
			}
			if strings.TrimSpace(string(buf[:n])) == "TRIO-FIND 1 "+shop {
				b, _ := json.Marshal(map[string]any{"trio": 1, "shop": shop, "port": port, "host": "PC"})
				pc.WriteTo(b, from)
			}
		}
	}()
	return pc.LocalAddr().String()
}

func TestDiscoverLoopback(t *testing.T) {
	a := fakeAnswerer(t, "s1", 8123)
	got := discover("s1", []string{a}, 700*time.Millisecond)
	if len(got) != 1 || got[0] != "127.0.0.1:8123" {
		t.Fatal(got)
	}
	if got := discover("other", []string{a}, 400*time.Millisecond); len(got) != 0 {
		t.Fatal("another shop's PC answered", got)
	}
}

// the owner server: /pos/api/ping/ checks the key; /cashier/ → /pos/; /pos/ = the till page; a static file; the API
func fakeOwner(t *testing.T) *httptest.Server {
	mux := http.NewServeMux()
	mux.HandleFunc("/pos/api/ping/", func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("X-Device-Token") != "tok-123" {
			http.Error(w, `{"error":"x"}`, 401)
			return
		}
		io.WriteString(w, `{"ok":true,"shop_id":"shop1"}`)
	})
	mux.HandleFunc("/cashier/", func(w http.ResponseWriter, r *http.Request) { http.Redirect(w, r, "/pos/", 302) })
	mux.HandleFunc("/pos/", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		w.Header().Set("X-Seen-Host", r.Host)
		io.WriteString(w, "<!doctype html><html><head><title>till</title></head><body>TILL "+r.Host+"</body></html>")
	})
	mux.HandleFunc("/static/ui/app.css", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/css")
		io.WriteString(w, "body{}")
	})
	mux.HandleFunc("/pos/api/sync/", func(w http.ResponseWriter, r *http.Request) {
		b, _ := io.ReadAll(r.Body)
		w.Header().Set("Content-Type", "application/json")
		w.Write(append([]byte(`{"echo":`), append(b, '}')...))
	})
	s := httptest.NewServer(mux)
	t.Cleanup(s.Close)
	return s
}

func get(t *testing.T, h http.Handler, method, path, body string) *httptest.ResponseRecorder {
	r := httptest.NewRequest(method, "http://127.0.0.1:8010"+path, strings.NewReader(body))
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)
	return w
}

func TestFinderOrder(t *testing.T) {
	owner := fakeOwner(t)
	addr := strings.TrimPrefix(owner.URL, "http://")
	dir := t.TempDir()
	p := testPair()
	p.Host = "no-such-host.invalid"
	p.IPs = []string{"127.0.0.1"}
	p.Port = 1 // nothing on it
	f := newFinder(dir, p)
	f.targets = func() []string { return nil }
	if f.search() {
		t.Fatal("found a server where there is none")
	}
	// the owner PC got a new address: discovery finds it, the key check passes, it is remembered
	host, prt, _ := net.SplitHostPort(addr)
	_ = host
	var port int
	json.Unmarshal([]byte(prt), &port)
	ans := fakeAnswerer(t, "shop1", port)
	f.targets = func() []string { return []string{ans} }
	if !f.search() || f.Current() != addr {
		t.Fatal("discovery did not connect", f.Current())
	}
	if st := loadState(dir); st.Last != addr || st.ShopID != "shop1" {
		t.Fatalf("state %+v", st)
	}
	// a wrong key: reachable but refused → stopped, not connected
	f2 := newFinder(t.TempDir(), &Pair{V: 1, ShopID: "shop1", Token: "bad", Port: port, IPs: []string{"127.0.0.1"}})
	f2.targets = func() []string { return nil }
	if f2.search() {
		t.Fatal("bad key connected")
	}
	if on, stopped, _ := f2.Status(); on || !stopped {
		t.Fatal("refused device should show stopped")
	}
}

func TestProxyOnlineThenOffline(t *testing.T) {
	owner := fakeOwner(t)
	addr := strings.TrimPrefix(owner.URL, "http://")
	dir := t.TempDir()
	p := testPair()
	f := newFinder(dir, p)
	f.targets = func() []string { return nil }
	f.use(addr)
	px := newProxy(f, p, filepath.Join(dir, "cache"))

	w := get(t, px, "GET", "/cashier/", "")
	if w.Code != 302 || w.Header().Get("Location") != "/pos/" {
		t.Fatal("redirect", w.Code, w.Header())
	}
	w = get(t, px, "GET", "/pos/", "")
	body := w.Body.String()
	if w.Code != 200 || !strings.Contains(body, "TILL 127.0.0.1:8010") || !strings.Contains(body, `<head><script src="/__trio/boot.js"></script>`) {
		t.Fatal("till page through the proxy (Host kept, boot.js injected):", w.Code, body)
	}
	if get(t, px, "GET", "/static/ui/app.css", "").Body.String() != "body{}" {
		t.Fatal("static")
	}
	w = get(t, px, "POST", "/pos/api/sync/", `{"orders":[1]}`)
	if w.Code != 200 || w.Body.String() != `{"echo":{"orders":[1]}}` {
		t.Fatal("sync body", w.Code, w.Body.String())
	}
	var st map[string]any
	json.Unmarshal(get(t, px, "GET", "/__trio/status", "").Body.Bytes(), &st)
	if st["online"] != true {
		t.Fatal("status online", st)
	}
	js := get(t, px, "GET", "/__trio/boot.js", "").Body.String()
	if !strings.Contains(js, `"tok-123"`) || !strings.Contains(js, "pos_token") {
		t.Fatal("boot.js carries the key", js[:80])
	}

	owner.Close() // the owner PC is off
	w = get(t, px, "GET", "/pos/", "")
	if w.Code != 200 || !strings.Contains(w.Body.String(), "TILL") || w.Header().Get("X-Trio-From") != "cache" {
		t.Fatal("till page from the cache", w.Code, w.Header())
	}
	if strings.Count(w.Body.String(), "/__trio/boot.js") != 1 {
		t.Fatal("boot.js injected once")
	}
	if w := get(t, px, "GET", "/cashier/", ""); w.Code != 302 {
		t.Fatal("cached redirect", w.Code)
	}
	if get(t, px, "GET", "/static/ui/app.css", "").Body.String() != "body{}" {
		t.Fatal("static from cache")
	}
	w = get(t, px, "POST", "/pos/api/sync/", `{}`)
	if w.Code != 503 || !strings.Contains(w.Body.String(), "offline") {
		t.Fatal("api offline", w.Code, w.Body.String())
	}
	if w := get(t, px, "GET", "/ui/play/", ""); w.Code != 503 || !strings.Contains(w.Body.String(), "جهاز المحل غير متاح") {
		t.Fatal("offline page", w.Code)
	}
	json.Unmarshal(get(t, px, "GET", "/__trio/status", "").Body.Bytes(), &st)
	if st["online"] != false {
		t.Fatal("status offline", st)
	}
}

func TestLocalControlNeedsHeader(t *testing.T) {
	p := testPair()
	f := newFinder(t.TempDir(), p)
	px := newProxy(f, p, t.TempDir())
	quit := make(chan bool, 1)
	px.quit = func() { quit <- true }
	if w := get(t, px, "POST", "/__trio/quit", ""); w.Code != 403 {
		t.Fatal("quit without the header", w.Code)
	}
	if w := get(t, px, "GET", "/__trio/quit", ""); w.Code != 403 {
		t.Fatal("quit by GET", w.Code)
	}
	r := httptest.NewRequest("POST", "http://127.0.0.1:8010/__trio/quit", nil)
	r.Header.Set("X-Trio-Local", "1")
	w := httptest.NewRecorder()
	px.ServeHTTP(w, r)
	select {
	case <-quit:
	case <-time.After(2 * time.Second):
		t.Fatal("quit not called")
	}
	get(t, px, "POST", "/__trio/q?n=4", "")
	var st map[string]any
	json.Unmarshal(get(t, px, "GET", "/__trio/status", "").Body.Bytes(), &st)
	if st["pending"] != float64(4) {
		t.Fatal("pending", st)
	}
}

func TestShortcutScript(t *testing.T) {
	s := shortcutScript(`C:\Users\a\AppData\Local\Trio Cashier`, `C:\Users\a\AppData\Local\Trio Cashier\TrioCashier.exe`, "1.26.1")
	for _, want := range []string{"Trio – الكاشير.lnk", "GetFolderPath('Desktop')", "GetFolderPath('Programs')", `HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\TrioCashier`, "-uninstall"} {
		if !strings.Contains(s, want) {
			t.Fatal("missing", want)
		}
	}
	if strings.Contains(s, "HKLM") {
		t.Fatal("must stay per-user")
	}
}
