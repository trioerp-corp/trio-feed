package main

// Trio in its own window: window\TrioWindow.exe (Edge WebView2, tools/window). The launcher that runs the server starts
// every window (other clicks on Trio.exe / Trio Cashier.exe ask it through the control port /open) so it knows when the
// LAST one closes: then it stops the server — unless other cashier devices / phones use this PC, then it asks first.
// No TrioWindow.exe or no WebView2 runtime (exit code 3) → the browser, as before (the server then keeps running).

import (
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"
)

const base = "http://localhost:8000"

var (
	winMu      sync.Mutex
	winOpen    int
	lastClosed = make(chan struct{}, 1)
	noWebView  bool
)

func windowExe() string {
	p := filepath.Join(dir, "window", "TrioWindow.exe")
	if _, err := os.Stat(p); err == nil && !noWebView {
		return p
	}
	return ""
}

// openApp: path on the local server ("/" or "/cashier/"); till = silent printing on every page (Trio Cashier)
func openApp(path string, till bool) {
	if openWindow(path, till) {
		return
	}
	if till {
		kiosk()
	} else {
		openBrowser(base + path)
	}
}

func openWindow(path string, till bool) bool {
	exe := windowExe()
	if exe == "" {
		return false
	}
	args := []string{base + path, "--title", brand, "--key", "main"}
	if till {
		args = []string{base + path, "--title", brand, "--key", "till", "--till"}
	}
	c := exec.Command(exe, args...)
	c.Dir = filepath.Dir(exe)
	if c.Start() != nil {
		return false
	}
	winMu.Lock()
	winOpen++
	winMu.Unlock()
	start := time.Now()
	done := make(chan int, 1)
	go func() {
		c.Wait()
		code := 0
		if c.ProcessState != nil {
			code = c.ProcessState.ExitCode()
		}
		winMu.Lock()
		winOpen--
		left := winOpen
		if code == 3 { // no WebView2 runtime: it opened the browser itself; from now on the old ways
			noWebView = true
		}
		winMu.Unlock()
		done <- code
		if left == 0 && code != 3 {
			select {
			case lastClosed <- struct{}{}:
			default:
			}
		}
	}()
	// a window that dies at once (broken install): fall back to the browser right away
	select {
	case code := <-done:
		if code == 3 {
			return true
		}
		if time.Since(start) < 3*time.Second && code != 0 {
			say("شباك البرنامج قفل فجأة (" + strconv.Itoa(code) + ") — هفتحه في المتصفح")
			noWebView = true
			return false
		}
	case <-time.After(3 * time.Second):
	}
	return true
}

func windowsOpen() int {
	winMu.Lock()
	defer winMu.Unlock()
	return winOpen
}

// askOpen: another click while the program runs → the running launcher opens (and tracks) the window
func askOpen(path string, till bool) bool {
	q := url.Values{"k": {stopKey(dir)}, "u": {path}}
	if till {
		q.Set("till", "1")
	}
	r, err := http.Post("http://127.0.0.1:8001/open?"+q.Encode(), "text/plain", nil)
	if err != nil {
		return false
	}
	r.Body.Close()
	return r.StatusCode == 200
}

// watchWindows: the last window closed → stop the server (the same way as «Trio.exe -stop»)
func watchWindows(stop func()) {
	for range lastClosed {
		time.Sleep(1500 * time.Millisecond) // a window that reopens right away (e.g. Trio Cashier after Trio)
		if windowsOpen() > 0 {
			continue
		}
		if othersLinked() && !msgYesNo(brand, "أجهزة كاشير أخرى متصلة بهذا الجهاز — هل تريد إيقاف البرنامج؟\n\n«لا» = البرنامج يفضل شغال في الخلفية للأجهزة التانية.") {
			say("الشباك اتقفل — البرنامج فاضل شغال للأجهزة التانية")
			continue
		}
		if windowsOpen() > 0 {
			continue
		}
		say("آخر شباك اتقفل — بقفل البرنامج")
		stop()
		return
	}
}

// othersLinked: another device uses this PC's server — a live connection to :8000 from the network now, or more than one
// till (pos.Device) synced in the last 12 hours (this PC's own till is one of them).
func othersLinked() bool {
	ns := exec.Command("netstat", "-an", "-p", "TCP")
	hide(ns)
	if out, err := ns.Output(); err == nil {
		for _, l := range strings.Split(string(out), "\n") {
			f := strings.Fields(l)
			if len(f) < 4 || !strings.EqualFold(f[3], "ESTABLISHED") || !strings.HasSuffix(f[1], ":8000") {
				continue
			}
			if r := f[2]; !strings.HasPrefix(r, "127.") && !strings.HasPrefix(r, "[::1]") && !strings.HasPrefix(r, "[::ffff:127.") {
				return true
			}
		}
	}
	q := cmd(py(), "manage.py", "shell", "-c", "from pos.models import Device;from django.utils import timezone as t;import datetime as d;"+
		"print('N=%d'%Device.objects.filter(active=True,last_sync__gte=t.now()-d.timedelta(hours=12)).count())")
	q.Stdout = nil
	if out, err := q.Output(); err == nil {
		s := string(out)
		if i := strings.LastIndex(s, "N="); i >= 0 {
			n, _ := strconv.Atoi(strings.TrimSpace(s[i+2:]))
			return n > 1
		}
	}
	return false
}
