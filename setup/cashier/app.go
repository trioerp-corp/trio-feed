package main

import (
	"context"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"sync"
	"time"
)

func localURL(path string) string { return "http://127.0.0.1:" + port() + path }

// askRunning: a copy of this app already serves the port → it opens / closes its window; true when it answered.
func askRunning(what string) bool {
	req, _ := http.NewRequest("POST", localURL("/__trio/"+what), nil)
	req.Header.Set("X-Trio-Local", "1")
	c := &http.Client{Timeout: 3 * time.Second, Transport: &http.Transport{Proxy: nil}}
	r, err := c.Do(req)
	if err != nil {
		return false
	}
	r.Body.Close()
	return r.StatusCode == 200
}

func runApp(dir string, p *Pair, window bool) {
	ln, err := net.Listen("tcp", "127.0.0.1:"+port())
	if err != nil {
		if askRunning("open") {
			return // already running: it shows the till
		}
		tell("تعذّر تشغيل شاشة الكاشير: المنفذ "+port()+" مستخدم من برنامج آخر على هذا الجهاز.\nأعد تشغيل الجهاز ثم افتح «"+title+"» مرة أخرى.", true)
		return
	}
	f := newFinder(dir, p)
	px := newProxy(f, p, filepath.Join(dir, "cache"))
	srv := &http.Server{Handler: px, ReadHeaderTimeout: 20 * time.Second}
	stop := make(chan struct{})
	var once sync.Once
	quit := func() {
		once.Do(func() {
			close(stop)
			ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
			srv.Shutdown(ctx)
			cancel()
		})
	}
	w := &windows{dir: dir, quit: quit}
	px.quit = func() { w.closeAll(); quit() }
	px.open = func() {
		if window {
			w.open()
		}
	}
	go f.Run(stop)
	f.search() // the first look before the window opens (the till then opens online when it can)
	logLine("شاشة الكاشير على " + localURL("/") + " — الجهاز: " + p.DeviceName)
	if window {
		go w.open()
	}
	srv.Serve(ln)
}

// windows: the till window (window\TrioWindow.exe, WebView2). Its last close stops this app; without WebView2 the till opens
// in Edge (silent printing) and this app keeps serving until Windows signs out.
type windows struct {
	mu   sync.Mutex
	dir  string
	n    int
	cmds []*exec.Cmd
	quit func()
	edge bool
}

func (w *windows) open() {
	w.mu.Lock()
	if w.n > 0 || w.edge {
		w.mu.Unlock()
		if w.edge {
			openEdge(localURL("/cashier/"))
		}
		return // one till window per PC: two would share (and race on) the same saved sales
	}
	w.mu.Unlock()
	exe := filepath.Join(w.dir, "window", "TrioWindow.exe")
	if _, err := os.Stat(exe); err != nil || !hasWebView2() {
		w.mu.Lock()
		w.edge = true
		w.mu.Unlock()
		openEdge(localURL("/cashier/"))
		return
	}
	c := exec.Command(exe, localURL("/cashier/"), "--title", title, "--key", "cashier", "--till")
	c.Dir = filepath.Dir(exe)
	if err := c.Start(); err != nil {
		logLine("الشاشة لم تفتح: " + err.Error())
		openEdge(localURL("/cashier/"))
		return
	}
	w.mu.Lock()
	w.n++
	w.cmds = append(w.cmds, c)
	w.mu.Unlock()
	c.Wait()
	code := 0
	if c.ProcessState != nil {
		code = c.ProcessState.ExitCode()
	}
	w.mu.Lock()
	w.n--
	left := w.n
	if code == 3 { // no WebView2 runtime: TrioWindow opened the browser itself
		w.edge = true
	}
	w.mu.Unlock()
	logLine("الشاشة أُغلقت (" + strconv.Itoa(code) + ")")
	if left == 0 && code != 3 {
		w.quit()
	}
}

func (w *windows) closeAll() {
	w.mu.Lock()
	defer w.mu.Unlock()
	for _, c := range w.cmds {
		if c.Process != nil && c.ProcessState == nil {
			c.Process.Kill()
		}
	}
}

func openEdge(url string) {
	for _, b := range []string{"msedge", "chrome"} {
		c := exec.Command("cmd", "/c", "start", "", b, "--kiosk-printing", "--app="+url)
		hide(c)
		if c.Run() == nil {
			return
		}
	}
	c := exec.Command("rundll32", "url.dll,FileProtocolHandler", url)
	hide(c)
	c.Start()
}
