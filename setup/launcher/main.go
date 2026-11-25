// Trio.exe — the whole program in one click, no internet needed.
//
//   - From the flash drive (the folder has the «.acc-flash» mark): copies the program to C:\Trio (or updates the program
//     files there, keeping the client's data), puts «Trio» on the desktop and starts it.
//   - On the client's PC: first run prepares the database; a fresh copy opens the developer setup (user Trio + e-mailed code).
//     Then it runs the server, opens the browser, makes the backups, sends the daily summary and checks Trio control file.
//
// Trio Cashier.exe (same program) opens the cashier screen with silent receipt printing.
// Windows: window\TrioWindow.exe (Edge WebView2, no browser bar — see window.go); the browser only when it is missing.
//
// No console window: the program runs in the background like any app (built with -H windowsgui). While it gets ready the
// browser shows a «بنجهّز البرنامج» page; messages that need the user are Windows message boxes; everything else goes to
// data\trio.log. «Trio.exe -stop» (Start menu → Trio → «إيقاف Trio») stops it.
// Trio · 01228827915 · 01121460640
package main

import (
	"archive/zip"
	"context"
	"crypto/rand"
	_ "embed"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"io/fs"
	"net"
	"net/http"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"syscall"
	"time"
	"unicode/utf16"
)

const (
	brand     = "Trio" // what the person sees: desktop / Start-menu shortcuts, windows (the brand since 1.13)
	appName   = "Trio" // the launcher file (Trio.exe) and the install folder C:\Trio
	installTo = `C:\Trio`
	pyVersion = "3.12.7"
)

var dir string

var (
	logMu   sync.Mutex
	logf    *os.File
	pending []string // what was said since the last message box
)

// say: to the log file (and kept for the next message box — there is no console)
func say(s string) {
	logMu.Lock()
	defer logMu.Unlock()
	if logf != nil {
		fmt.Fprintln(logf, time.Now().Format("2006-01-02 15:04:05"), s)
	}
	if t := strings.TrimSpace(s); t != "" {
		pending = append(pending, t)
		if len(pending) > 10 {
			pending = pending[len(pending)-10:]
		}
	}
}

// pause: the user has to read what happened → a message box with the last lines
func pause() {
	stopLoading()
	logMu.Lock()
	txt := strings.Join(pending, "\n")
	pending = nil
	logMu.Unlock()
	msgBox("Trio", txt+"\n\nTrio · 01228827915 · 01121460640", true)
}

func openLog(path string) {
	if st, err := os.Stat(path); err == nil && st.Size() > 4<<20 {
		os.Rename(path, path+".old")
	}
	os.MkdirAll(filepath.Dir(path), 0755)
	if f, err := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0644); err == nil {
		logf = f
	}
}

func exists(p string) bool { _, err := os.Stat(filepath.Join(dir, p)); return err == nil }
func openBrowser(url string) {
	c := exec.Command("rundll32", "url.dll,FileProtocolHandler", url)
	hide(c)
	c.Start()
}
func psQuote(s string) string { return "'" + strings.ReplaceAll(s, "'", "''") + "'" }

func cmd(name string, args ...string) *exec.Cmd {
	c := exec.Command(name, args...)
	c.Dir, c.Env = dir, env()
	if logf != nil {
		c.Stdout, c.Stderr = logf, logf
	}
	hide(c)
	return c
}

func run(name string, args ...string) error { return cmd(name, args...).Run() }

func quietRun(args ...string) { cmd(py(), args...).Run() }

func py() string {
	if t := os.Getenv("ACC_TEST_PYTHON"); t != "" { // tests on the developer's machine
		return t
	}
	v := filepath.Join(dir, ".venv", "Scripts", "python.exe")
	if _, err := os.Stat(v); err == nil {
		return v
	}
	return filepath.Join(dir, "python", "python.exe")
}

// The package carries Python with every library already inside (python\.acc-ready): nothing to download.
func bundled() bool { return exists(filepath.Join("python", ".acc-ready")) }

func env() []string {
	e := os.Environ()
	key, _ := os.ReadFile(filepath.Join(dir, "data", "secret.txt"))
	e = append(e, "GG_DEBUG=0", "GG_ALLOWED_HOSTS=*", "PYTHONIOENCODING=utf-8", "PYTHONUTF8=1", "GG_SECRET_KEY="+strings.TrimSpace(string(key)))
	if u, err := os.ReadFile(filepath.Join(dir, "data", "public_url.txt")); err == nil && len(strings.TrimSpace(string(u))) > 0 {
		e = append(e, "GG_CSRF_ORIGINS="+strings.TrimSpace(string(u)), "GG_PROXY_HTTPS=1")
	}
	return e
}

// ---------------------------------------------------------------- flash drive → C:\Trio
func copyTree(src, dst string) (int, error) {
	n := 0
	err := filepath.WalkDir(src, func(p string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		rel, _ := filepath.Rel(src, p)
		top := strings.SplitN(rel, string(os.PathSeparator), 2)[0]
		if top == "data" || top == "backups" || rel == ".acc-flash" || top == "staticfiles" {
			if d.IsDir() {
				return filepath.SkipDir
			}
			return nil
		}
		target := filepath.Join(dst, rel)
		if d.IsDir() {
			return os.MkdirAll(target, 0755)
		}
		in, err := os.Open(p)
		if err != nil {
			return err
		}
		defer in.Close()
		out, err := os.Create(target)
		if err != nil {
			return err
		}
		_, err = io.Copy(out, in)
		out.Close()
		n++
		return err
	})
	return n, err
}

func installFromFlash() {
	target := installTo
	if err := os.MkdirAll(target, 0755); err != nil {
		home, _ := os.UserHomeDir()
		target = filepath.Join(home, appName)
		os.MkdirAll(target, 0755)
	}
	_, err := os.Stat(filepath.Join(target, "data", ".installed"))
	update := err == nil
	if update {
		say("البرنامج متسطب على الجهاز ده في " + target + " — هحدّث ملفات البرنامج وبيانات المحل زي ما هي.")
	} else {
		say("بسطّب " + appName + " على الجهاز في " + target + " ...")
	}
	if up() && !stopRunning(target) { // an old copy running from C:\Trio keeps its files busy
		say("البرنامج شغال دلوقتي ومقدرتش أقفله — اقفل الشباك الأسود بتاعه لو ظاهر، أو اعمل Restart للجهاز، وبعدين شغّل Trio.exe من الفلاشة تاني.")
		pause()
		return
	}
	welcome := "أهلًا بيك في Trio 👋 — بنسطّب البرنامج على جهازك (أول مرة بس، حوالي دقيقتين).<br>بعدها تقدر تجرّبه 14 يوم مجانًا — ولما تحب تشترك هتلاقي «⭐ اشترك» جوه البرنامج."
	if update {
		welcome = "بنحدّث Trio على الجهاز ده — بيانات المحل زي ما هي. ثواني والبرنامج يفتح."
	}
	opened := startLoading(welcome)
	if opened {
		openBrowser("http://localhost:8000")
	}
	n, err := copyTree(dir, target)
	say("")
	if err != nil {
		say("النسخ وقف: " + err.Error())
		pause()
		return
	}
	say(fmt.Sprintf("اتنسخ %d ملف ✔", n))
	dir = target
	shortcuts()
	say("بشغّل البرنامج من الجهاز...")
	stopLoading() // the installed copy shows its own «بنجهّز» page; the open browser tab keeps waiting
	c := exec.Command(filepath.Join(target, appName+".exe"))
	if opened {
		c.Args = append(c.Args, "-quiet") // the browser tab is already open and waiting
	}
	c.Dir = target
	if err := c.Start(); err != nil {
		say("مقدرتش أشغله: " + err.Error())
		pause()
	}
}

// ---------------------------------------------------------------- desktop shortcuts «Trio» with Trio logo
func powershell(script string) error {
	u := utf16.Encode([]rune(script))
	b := make([]byte, len(u)*2)
	for i, v := range u {
		b[2*i], b[2*i+1] = byte(v), byte(v>>8)
	}
	c := exec.Command("powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-EncodedCommand",
		base64.StdEncoding.EncodeToString(b))
	hide(c)
	return c.Run()
}

func shortcuts() {
	if exists("unins000.exe") { // installed by Trio-Setup.exe: it made the shortcuts itself
		return
	}
	exe, cashier := filepath.Join(dir, appName+".exe"), filepath.Join(dir, "Trio Cashier.exe")
	script := "$d=[Environment]::GetFolderPath('Desktop');$w=New-Object -ComObject WScript.Shell;" +
		"$s=$w.CreateShortcut((Join-Path $d " + psQuote(brand+".lnk") + "));$s.TargetPath=" + psQuote(exe) + ";$s.WorkingDirectory=" +
		psQuote(dir) + ";$s.IconLocation=" + psQuote(exe+",0") + ";$s.Description='Trio';$s.Save();"
	if _, err := os.Stat(cashier); err == nil {
		script += "$c=$w.CreateShortcut((Join-Path $d " + psQuote(brand+" - الكاشير.lnk") + "));$c.TargetPath=" + psQuote(cashier) +
			";$c.WorkingDirectory=" + psQuote(dir) + ";$c.IconLocation=" + psQuote(cashier+",0") + ";$c.Save();"
	}
	// Start menu → Trio: open, cashier, stop (the stop is not on the desktop so nobody presses it by mistake)
	script += "$p=Join-Path ([Environment]::GetFolderPath('Programs')) 'Trio';New-Item -ItemType Directory -Force -Path $p|Out-Null;" +
		"$a=$w.CreateShortcut((Join-Path $p " + psQuote(brand+".lnk") + "));$a.TargetPath=" + psQuote(exe) + ";$a.WorkingDirectory=" + psQuote(dir) +
		";$a.IconLocation=" + psQuote(exe+",0") + ";$a.Save();" +
		"$x=$w.CreateShortcut((Join-Path $p 'إيقاف Trio.lnk'));$x.TargetPath=" + psQuote(exe) + ";$x.Arguments='-stop';$x.WorkingDirectory=" +
		psQuote(dir) + ";$x.IconLocation=" + psQuote(exe+",0") + ";$x.Description='إيقاف البرنامج';$x.Save();"
	// the old «ACC ERP» shortcuts and Start-menu folder give way to «Trio»
	script += "Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $d 'ACC ERP.lnk'),(Join-Path $d 'ACC ERP - الكاشير.lnk');" +
		"Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path ([Environment]::GetFolderPath('Programs')) 'ACC ERP');"
	if powershell(script) == nil {
		say("اختصار «" + brand + "» على سطح المكتب ✔")
	}
}

// rebrandShortcuts: a copy installed before «Trio» still has «ACC ERP» shortcuts — they are replaced once, quietly.
func rebrandShortcuts() {
	if runtime.GOOS != "windows" {
		return
	}
	home := os.Getenv("USERPROFILE")
	for _, d := range []string{filepath.Join(home, "Desktop"), filepath.Join(home, "OneDrive", "Desktop")} {
		if _, err := os.Stat(filepath.Join(d, "ACC ERP.lnk")); err == nil {
			shortcuts()
			return
		}
	}
}

// ---------------------------------------------------------------- server
func up() bool {
	c, err := net.DialTimeout("tcp", "127.0.0.1:8000", 700*time.Millisecond)
	if err == nil {
		c.Close()
		return true
	}
	return false
}

func runningDir() string {
	r, err := http.Get("http://127.0.0.1:8000/healthz/")
	if err != nil {
		return "?"
	}
	defer r.Body.Close()
	var v struct{ Dir string }
	if json.NewDecoder(r.Body).Decode(&v) != nil || v.Dir == "" {
		return "(نسخة قديمة)"
	}
	return v.Dir
}

func mainExe() string {
	for _, n := range []string{appName + ".exe", "ACC-ERP.exe", "ShopERP.exe"} {
		if exists(n) {
			return filepath.Join(dir, n)
		}
	}
	exe, _ := os.Executable()
	return exe
}

func kiosk() {
	url := "http://localhost:8000/cashier/"
	for _, b := range []string{"chrome", "msedge"} {
		c := exec.Command("cmd", "/c", "start", "", b, "--kiosk-printing", "--app="+url)
		hide(c)
		if c.Run() == nil {
			return
		}
	}
	openBrowser(url)
}

// Only when the package has no bundled Python (developer builds): download the embeddable one.
func download(url, to string) error {
	r, err := http.Get(url)
	if err != nil {
		return err
	}
	defer r.Body.Close()
	if r.StatusCode != 200 {
		return fmt.Errorf("%s: %s", url, r.Status)
	}
	f, err := os.Create(to)
	if err != nil {
		return err
	}
	defer f.Close()
	_, err = io.Copy(f, r.Body)
	return err
}

func unzip(src, dest string) error {
	z, err := zip.OpenReader(src)
	if err != nil {
		return err
	}
	defer z.Close()
	for _, f := range z.File {
		p := filepath.Join(dest, f.Name)
		if !strings.HasPrefix(p, filepath.Clean(dest)+string(os.PathSeparator)) {
			continue
		}
		if f.FileInfo().IsDir() {
			os.MkdirAll(p, 0755)
			continue
		}
		os.MkdirAll(filepath.Dir(p), 0755)
		rc, err := f.Open()
		if err != nil {
			return err
		}
		w, err := os.Create(p)
		if err != nil {
			rc.Close()
			return err
		}
		io.Copy(w, rc)
		w.Close()
		rc.Close()
	}
	return nil
}

func downloadPython() bool {
	pdir := filepath.Join(dir, "python")
	if _, err := os.Stat(filepath.Join(pdir, "python.exe")); err != nil {
		say("بنزّل Python (مرة واحدة)...")
		zipPath := filepath.Join(dir, "python-embed.zip")
		if err := download("https://www.python.org/ftp/python/"+pyVersion+"/python-"+pyVersion+"-embed-amd64.zip", zipPath); err != nil {
			say("التحميل فشل: " + err.Error())
			return false
		}
		if err := unzip(zipPath, pdir); err != nil {
			return false
		}
		os.Remove(zipPath)
		pths, _ := filepath.Glob(filepath.Join(pdir, "python*._pth"))
		for _, p := range pths {
			b, _ := os.ReadFile(p)
			t := strings.ReplaceAll(string(b), "#import site", "import site")
			if !strings.Contains(t, "Lib\\site-packages") {
				t += "\r\nLib\\site-packages\r\n..\r\n"
			}
			os.WriteFile(p, []byte(t), 0644)
		}
	}
	if exec.Command(py(), "-m", "pip", "--version").Run() != nil {
		gp := filepath.Join(pdir, "get-pip.py")
		if download("https://bootstrap.pypa.io/get-pip.py", gp) != nil || run(py(), gp, "--no-warn-script-location", "-q") != nil {
			return false
		}
	}
	return run(py(), "-m", "pip", "install", "-q", "--no-warn-script-location", "-r", "requirements-server.txt") == nil
}

func install() bool {
	say("=== أول تشغيل على الجهاز ده: تجهيز البرنامج (مرة واحدة) ===")
	if !bundled() && !exists(".venv") && !downloadPython() {
		say("النسخة دي مش فيها Python جاهز ومحتاجة نت — استخدم نسخة الفلاشة الكاملة.")
		return false
	}
	if err := run(py(), "manage.py", "migrate", "--noinput"); err != nil {
		return false
	}
	if exists(filepath.Join("client", "client.json")) {
		if err := run(py(), "manage.py", "seed_shop", "--config", filepath.Join("client", "client.json")); err != nil {
			return false
		}
	} else if err := run(py(), "manage.py", "acc_init"); err != nil { // the developer sets it up from the browser
		return false
	}
	os.WriteFile(filepath.Join(dir, "data", ".installed"), []byte(time.Now().Format(time.RFC3339)), 0644)
	// phones / other PCs on the shop network (needs admin — ignored if it fails)
	fw := exec.Command("netsh", "advfirewall", "firewall", "add", "rule", "name=Trio 8000", "dir=in", "action=allow", "protocol=TCP", "localport=8000")
	hide(fw)
	fw.Run()
	udpRule() // 1.26.1: cashier PCs find this PC on the network (UDP 8001)
	say("=== الجهاز جاهز ✔ ===")
	return true
}

func main() {
	exe, _ := os.Executable()
	dir = filepath.Dir(exe)
	if len(os.Args) > 1 && os.Args[1] == "-stop" {
		if !up() {
			msgBox("Trio", "البرنامج مش شغال.", false)
		} else if stopRunning(dir) {
			msgBox("Trio", "البرنامج اتقفل ✔\nلما تحب تشغّله: اختصار «Trio» على سطح المكتب.", false)
		} else {
			msgBox("Trio", "مقدرتش أقفل البرنامج — اعمل Restart للجهاز.", true)
		}
		return
	}
	if exists(".acc-flash") {
		openLog(filepath.Join(os.TempDir(), "acc-erp-install.log"))
	} else {
		openLog(filepath.Join(dir, "data", "trio.log"))
	}
	cashier := len(os.Args) > 1 && os.Args[1] == "-cashier" || strings.Contains(strings.ToLower(filepath.Base(exe)), "cashier")
	if cashier {
		if !up() {
			if windowExe() != "" { // the launcher that runs the server opens the till window itself (and knows when it closes)
				exec.Command(mainExe(), "-till").Start()
				return
			}
			exec.Command(mainExe(), "-quiet").Start()
			for i := 0; i < 90 && !up(); i++ {
				time.Sleep(time.Second)
			}
		}
		if !askOpen("/cashier/", true) {
			openApp("/cashier/", true)
		}
		return
	}
	say("Trio · 01228827915 · 01121460640")
	if exists(".acc-flash") {
		installFromFlash()
		return
	}
	go rebrandShortcuts()
	quiet := len(os.Args) > 1 && os.Args[1] == "-quiet"
	startPath, till := "/", len(os.Args) > 1 && os.Args[1] == "-till" // -till: started by Trio Cashier.exe
	if till {
		startPath = "/cashier/"
	}
	if up() {
		if other := runningDir(); other != "" && !strings.EqualFold(filepath.Clean(other), filepath.Clean(dir)) {
			say("فيه نسخة تانية من البرنامج شغالة من: " + other)
			say("اقفلها من «ابدأ ← Trio ← إيقاف Trio» (أو اعمل Restart للجهاز) وبعدين شغّل البرنامج تاني.")
			pause()
			return
		}
		if !quiet && !askOpen(startPath, till) {
			openApp(startPath, till) // already running: just open it, like any app
		}
		return
	}
	msg := "بنفتح البرنامج..."
	if !exists(filepath.Join("data", ".installed")) { // the very first start on this PC: welcome + what comes next
		msg = "أهلًا بيك في Trio 👋 — بنجهّز البرنامج على جهازك (أول مرة بس، حوالي دقيقتين).<br>بعدها تقدر تجرّبه 14 يوم مجانًا — ولما تحب تشترك هتلاقي «⭐ اشترك» جوه البرنامج."
	}
	loading := startLoading(msg)
	if loading && !quiet {
		openApp(startPath, till)
	}
	os.MkdirAll(filepath.Join(dir, "data"), 0755)
	if !exists(filepath.Join("data", "secret.txt")) {
		b := make([]byte, 48)
		rand.Read(b)
		os.WriteFile(filepath.Join(dir, "data", "secret.txt"), []byte(base64.RawURLEncoding.EncodeToString(b)), 0600)
	}
	if !exists(filepath.Join("data", ".installed")) || !exists(filepath.Join("python", "python.exe")) && !exists(".venv") {
		if !install() {
			pause()
			return
		}
		shortcuts()
	}
	if !bundled() { // developer builds: new versions may add libraries
		run(py(), "-m", "pip", "install", "-q", "--no-warn-script-location", "-r", "requirements-server.txt")
	}
	applyUpdate()
	run(py(), "manage.py", "migrate", "--noinput")
	run(py(), "manage.py", "collectstatic", "--noinput", "-v", "0")
	var kids []*exec.Cmd
	if tok, err := os.ReadFile(filepath.Join(dir, "data", "cloudflare_token.txt")); err == nil && exists("cloudflared.exe") {
		c := cmd(filepath.Join(dir, "cloudflared.exe"), "tunnel", "--no-autoupdate", "run", "--token", strings.TrimSpace(string(tok)))
		if c.Start() == nil {
			kids = append(kids, c)
			say("الوصول من برة المحل شغال (Cloudflare).")
		}
	}
	var srvMu sync.Mutex
	var srv *exec.Cmd
	restarting, stopping := false, false
	startSrv := func() error {
		c := cmd(py(), "-m", "waitress", "--listen=0.0.0.0:8000", "--threads=8", "config.wsgi:application")
		if err := c.Start(); err != nil {
			return err
		}
		srv = c
		return nil
	}
	stopLoading()
	if err := startSrv(); err != nil {
		say("السيرفر مشتغلش: " + err.Error())
		pause()
		return
	}
	for i := 0; i < 60 && !up(); i++ {
		time.Sleep(500 * time.Millisecond)
	}
	if _, err := http.Get("http://127.0.0.1:8000/healthz/"); err == nil && !quiet && !loading {
		openApp(startPath, till) // (with the «بنجهّز» page the open window reloads by itself)
	}
	go func() { // every hour: remote stop / updates / upgrades, backup (if the last is older than 12 hours), the daily summary
		for {
			quietRun("manage.py", "acc_control")
			quietRun("manage.py", "backup_now", "--if-older-than-hours", "12")
			quietRun("manage.py", "send_daily_report", "--auto")
			time.Sleep(time.Hour)
		}
	}()
	go func() { // a downloaded update: installed at night (2–5) or right away when the owner pressed «حدّث دلوقتي»
		for {
			time.Sleep(30 * time.Second)
			if !updateDue() {
				continue
			}
			srvMu.Lock()
			restarting = true
			say("بركّب تحديث البرنامج — دقيقة وراجع ...")
			if srv != nil && srv.Process != nil {
				srv.Process.Kill()
			}
			time.Sleep(2 * time.Second)
			applyUpdate()
			run(py(), "manage.py", "collectstatic", "--noinput", "-v", "0")
			if err := startSrv(); err != nil {
				say("السيرفر مشتغلش بعد التحديث: " + err.Error())
			}
			restarting = false
			srvMu.Unlock()
		}
	}()
	say("البرنامج شغال — http://localhost:8000 (ومن موبايل على نفس الواي فاي: http://اسم-الجهاز:8000)")
	logMu.Lock()
	pending = nil
	logMu.Unlock()
	sig := make(chan os.Signal, 1)
	signal.Notify(sig, os.Interrupt, syscall.SIGTERM)
	stopAll := func() { stopping = true; sig <- os.Interrupt }
	controlPort(stopAll)
	if pc, err := discoveryListen(":"+discoverPort, func() string { return shopID(dir) }); err == nil { // 1.26.1 cashier PCs
		defer pc.Close()
	} else {
		say("البحث عن هذا الجهاز من أجهزة الكاشير غير متاح: " + err.Error())
	}
	go udpRule()
	go watchWindows(stopAll) // the last window closed → stop (or keep running for other devices)
	go func() {              // the server stopped by itself: start it again (the user has no window to see it)
		fails := 0
		for {
			cur := srv
			start := time.Now()
			cur.Wait()
			if stopping {
				return
			}
			if restarting {
				for srv == cur || restarting {
					time.Sleep(time.Second)
				}
				continue
			}
			if time.Since(start) > 10*time.Minute {
				fails = 0
			}
			fails++
			say(fmt.Sprintf("السيرفر وقف — بشغّله تاني (%d)", fails))
			if fails > 5 {
				say("السيرفر بيقف على طول — كلّم Trio")
				pause()
				sig <- os.Interrupt
				return
			}
			time.Sleep(3 * time.Second)
			srvMu.Lock()
			if err := startSrv(); err != nil {
				say("السيرفر مشتغلش: " + err.Error())
			}
			srvMu.Unlock()
		}
	}()
	<-sig
	stopping = true
	if srv != nil && srv.Process != nil {
		srv.Process.Kill()
	}
	for _, k := range kids {
		if k.Process != nil {
			k.Process.Kill()
		}
	}
}

// ---------------------------------------------------------------- remote updates (staged by «manage.py acc_control»)
func updateDue() bool {
	if !exists(filepath.Join("data", "update", "ready.json")) {
		return false
	}
	if exists(filepath.Join("data", "update", "now.flag")) {
		return true
	}
	if exists(filepath.Join("data", "update", "failed.txt")) {
		return false // a failed version waits for the owner / Trio
	}
	h := time.Now().Hour()
	return h >= 2 && h < 5
}

func applyUpdate() {
	if !exists(filepath.Join("data", "update", "ready.json")) {
		return
	}
	say("بركّب التحديث (نسخة احتياطية الأول) ...")
	if run(py(), "manage.py", "acc_apply_update") != nil {
		say("التحديث متركّبش — البرنامج زي ما هو.")
		os.Remove(filepath.Join(dir, "data", "update", "now.flag"))
		return
	}
	if run(py(), "manage.py", "migrate", "--noinput") != nil {
		say("في مشكلة في التحديث — برجّع النسخة القديمة ...")
		run(py(), "manage.py", "acc_apply_update", "--rollback")
		return
	}
	run(py(), "manage.py", "acc_apply_update", "--done")
	say("اتحدّث ✔")
}

// ---------------------------------------------------------------- «بنجهّز البرنامج» page while the program gets ready (no console window)
//
//go:embed loading-logo.png
var logoPNG []byte

var loadSrv *http.Server

func startLoading(msg string) bool {
	ln, err := net.Listen("tcp", "127.0.0.1:8000")
	if err != nil {
		return false
	}
	logo := base64.StdEncoding.EncodeToString(logoPNG)
	page := strings.ReplaceAll(strings.ReplaceAll(loadingHTML, "{{MSG}}", msg), "{{LOGO}}", logo)
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz/", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusServiceUnavailable)
		json.NewEncoder(w).Encode(map[string]any{"Dir": dir, "loading": true})
	})
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		w.Header().Set("Cache-Control", "no-store")
		io.WriteString(w, page)
	})
	loadSrv = &http.Server{Handler: mux}
	go loadSrv.Serve(ln)
	return true
}

func stopLoading() {
	if loadSrv != nil {
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		loadSrv.Shutdown(ctx)
		cancel()
		loadSrv = nil
	}
}

const loadingHTML = `<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><title>Trio</title>
<style>body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;background:radial-gradient(900px 600px at 85% 0%,#1C7F8C,transparent 60%),linear-gradient(160deg,#0A3A66,#020D1C);
font-family:"Segoe UI",Tahoma,sans-serif;color:#F3F8FC}.c{text-align:center;max-width:560px;padding:24px}img{width:120px;height:120px}
h1{font-size:30px;margin:14px 0 6px}p{color:#B6CADF;font-size:18px;line-height:1.7;margin:6px 0}
.sp{width:46px;height:46px;border-radius:50%;border:5px solid rgba(255,255,255,.18);border-top-color:#5CD6CB;margin:22px auto;animation:r 1s linear infinite}
@keyframes r{to{transform:rotate(360deg)}}small{color:#7F98B3}</style></head>
<body><div class="c"><img src="data:image/png;base64,{{LOGO}}" alt=""><h1>Trio</h1><p>{{MSG}}</p><div class="sp"></div>
<p id="t">ماتقفلش الصفحة دي — هتفتح البرنامج لوحدها</p><small>Trio · 01228827915 · 01121460640</small></div>
<script>let n=0;setInterval(async()=>{n++;document.getElementById('t').textContent='ماتقفلش الصفحة دي — هتفتح البرنامج لوحدها ('+n+')';
try{const r=await fetch('/healthz/',{cache:'no-store'});if(r.ok)location.reload()}catch(e){}},1500)</script></body></html>`

// ---------------------------------------------------------------- stop from the Start menu («Trio.exe -stop»)
func stopKey(base string) string {
	b, _ := os.ReadFile(filepath.Join(base, "data", "secret.txt"))
	k := strings.TrimSpace(string(b))
	if len(k) > 16 {
		k = k[:16]
	}
	return k
}

func controlPort(stop func()) {
	ln, err := net.Listen("tcp", "127.0.0.1:8001")
	if err != nil {
		return
	}
	key := stopKey(dir)
	mux := http.NewServeMux()
	mux.HandleFunc("/stop", func(w http.ResponseWriter, r *http.Request) {
		if key == "" || r.Method != http.MethodPost || r.URL.Query().Get("k") != key {
			http.Error(w, "no", http.StatusForbidden)
			return
		}
		io.WriteString(w, "ok")
		go func() { time.Sleep(300 * time.Millisecond); stop() }()
	})
	mux.HandleFunc("/open", func(w http.ResponseWriter, r *http.Request) { // another click on Trio.exe / Trio Cashier.exe
		q := r.URL.Query()
		if key == "" || r.Method != http.MethodPost || q.Get("k") != key {
			http.Error(w, "no", http.StatusForbidden)
			return
		}
		p := q.Get("u")
		if !strings.HasPrefix(p, "/") || strings.HasPrefix(p, "//") {
			p = "/"
		}
		io.WriteString(w, "ok")
		go openApp(p, q.Get("till") == "1")
	})
	go http.Serve(ln, mux)
}

// asks the copy running from base to stop; true when port 8000 is free again
func stopRunning(base string) bool {
	r, err := http.Post("http://127.0.0.1:8001/stop?k="+stopKey(base), "text/plain", nil)
	if err != nil {
		return false
	}
	r.Body.Close()
	if r.StatusCode != 200 {
		return false
	}
	for i := 0; i < 40 && up(); i++ {
		time.Sleep(500 * time.Millisecond)
	}
	return !up()
}
