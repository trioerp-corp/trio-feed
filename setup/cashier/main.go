// «Trio – الكاشير»: a cashier PC of a shop, with no Python and no server of its own (1.26.1).
//
// The owner makes «Trio Cashier Setup - <device>.exe» in the program (الإعدادات ← أجهزة الكاشير): this program + a payload
// appended at its end (a zip: window\ = TrioWindow.exe + WebView2 dlls, pair.json = shop, device key, the owner PC's name
// and addresses). Run on the cashier PC:
//
//   - the setup file (payload inside): installs for this Windows user in %LOCALAPPDATA%\Trio Cashier (no admin), one desktop
//     shortcut «Trio – الكاشير», a Start-menu entry and an entry in «Apps» (HKCU Uninstall); run again = update / re-pair.
//   - the installed TrioCashier.exe: serves 127.0.0.1:8010 — a proxy to the owner PC (/cashier/, /pos/…, /static/…, same
//     origin so the till's own storage and cookies work), finds the owner PC (last address → its name → its addresses →
//     UDP broadcast on 8001), keeps a copy of the till page and its files so the till opens with the owner PC off (the till
//     already keeps its sales in its storage and sends them later), and opens window\TrioWindow.exe on it.
//   - «TrioCashier.exe -uninstall» (from «Apps»): removes it (the till's saved sales stay in %LOCALAPPDATA%\Trio).
//
// Flags for tests: -silent (no message boxes), -nolaunch (install only), -nowindow (serve only), -find <out.json>.
// Env: TRIO_CASHIER_HOME (install folder), TRIO_CASHIER_PORT (8010), TRIO_DISCOVER_EXTRA (more UDP targets, e.g. 127.0.0.1).
package main

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

const (
	title     = "Trio – الكاشير"
	exeName   = "TrioCashier.exe"
	support   = "Trio · 01228827915 · 01121460640"
	ownerPort = 8000
)

var (
	silent bool
	logMu  sync.Mutex
	logf   *os.File
)

func logLine(s string) {
	logMu.Lock()
	defer logMu.Unlock()
	if logf != nil {
		fmt.Fprintln(logf, time.Now().Format("2006-01-02 15:04:05"), s)
	}
}

func tell(text string, warn bool) {
	logLine(text)
	if !silent {
		msgBox(title, text+"\n\n"+support, warn)
	}
}

func openLog(path string) {
	if st, err := os.Stat(path); err == nil && st.Size() > 2<<20 {
		os.Rename(path, path+".old")
	}
	os.MkdirAll(filepath.Dir(path), 0755)
	if f, err := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0644); err == nil {
		logf = f
	}
}

func home() string {
	if h := os.Getenv("TRIO_CASHIER_HOME"); h != "" {
		return h
	}
	base := os.Getenv("LOCALAPPDATA")
	if base == "" {
		base, _ = os.UserCacheDir()
	}
	return filepath.Join(base, "Trio Cashier")
}

func port() string {
	if p := os.Getenv("TRIO_CASHIER_PORT"); p != "" {
		return p
	}
	return "8010"
}

func has(args []string, f string) bool {
	for _, a := range args {
		if strings.EqualFold(a, f) {
			return true
		}
	}
	return false
}

func argAfter(args []string, f string) string {
	for i, a := range args {
		if strings.EqualFold(a, f) && i+1 < len(args) {
			return args[i+1]
		}
	}
	return ""
}

func main() {
	args := os.Args[1:]
	silent = has(args, "-silent")
	exe, _ := os.Executable()
	if has(args, "-uninstall") {
		openLog(filepath.Join(os.TempDir(), "trio-cashier-uninstall.log"))
		uninstall(filepath.Dir(exe))
		return
	}
	if zipData, exeLen, err := readPayload(exe); err == nil {
		openLog(filepath.Join(os.TempDir(), "trio-cashier-setup.log"))
		install(exe, zipData, exeLen, !has(args, "-nolaunch"))
		return
	}
	dir := filepath.Dir(exe)
	openLog(filepath.Join(dir, "cashier.log"))
	p, err := loadPair(filepath.Join(dir, "pair.json"))
	if err != nil {
		tell("لم يُعثر على بيانات ربط هذا الجهاز بالمحل.\nاطلب من صاحب المحل ملف تجهيز جهاز كاشير جديدًا وشغّله على هذا الجهاز.", true)
		return
	}
	if out := argAfter(args, "-find"); out != "" { // test: the discovery alone
		found := discover(p.ShopID, discoverTargets(), 3*time.Second)
		os.WriteFile(out, []byte(strings.Join(found, "\n")), 0644)
		return
	}
	runApp(dir, p, !has(args, "-nowindow"))
}
