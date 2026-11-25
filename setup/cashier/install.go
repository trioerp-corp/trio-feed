package main

import (
	"encoding/base64"
	"io"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
	"unicode/utf16"
)

const uninstallKey = `HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\TrioCashier`

func powershell(script string) error {
	u := utf16.Encode([]rune(script))
	b := make([]byte, len(u)*2)
	for i, v := range u {
		b[2*i], b[2*i+1] = byte(v), byte(v>>8)
	}
	c := exec.Command("powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
		"-EncodedCommand", base64.StdEncoding.EncodeToString(b))
	hide(c)
	if logf != nil {
		c.Stdout, c.Stderr = logf, logf
	}
	return c.Run()
}

func psQuote(s string) string { return "'" + strings.ReplaceAll(s, "'", "''") + "'" }

func portFree() bool {
	c, err := net.DialTimeout("tcp", "127.0.0.1:"+port(), 500*time.Millisecond)
	if err != nil {
		return true
	}
	c.Close()
	return false
}

// stopApp: a running copy keeps TrioCashier.exe busy → ask it to close (its window too), wait for the port.
func stopApp() {
	if portFree() {
		return
	}
	askRunning("quit")
	for i := 0; i < 20 && !portFree(); i++ {
		time.Sleep(300 * time.Millisecond)
	}
	time.Sleep(500 * time.Millisecond)
}

// copyExe: the program part of the setup file (without its payload) → dst
func copyExe(src string, n int64, dst string) error {
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()
	tmp := dst + ".new"
	out, err := os.Create(tmp)
	if err != nil {
		return err
	}
	_, err = io.Copy(out, io.LimitReader(in, n))
	out.Close()
	if err != nil {
		os.Remove(tmp)
		return err
	}
	os.Remove(dst + ".old")
	os.Rename(dst, dst+".old") // a running exe can be renamed but not overwritten on Windows
	if err := os.Rename(tmp, dst); err != nil {
		return err
	}
	os.Remove(dst + ".old")
	return nil
}

func shortcutScript(dir, exe, version string) string {
	lnk := title + ".lnk"
	return "$ErrorActionPreference='Stop';$w=New-Object -ComObject WScript.Shell;" +
		"$d=[Environment]::GetFolderPath('Desktop');$p=[Environment]::GetFolderPath('Programs');" +
		"foreach($f in @($d,$p)){$s=$w.CreateShortcut((Join-Path $f " + psQuote(lnk) + "));$s.TargetPath=" + psQuote(exe) +
		";$s.WorkingDirectory=" + psQuote(dir) + ";$s.IconLocation=" + psQuote(exe+",0") + ";$s.Description=" + psQuote(title) + ";$s.Save()};" +
		"$k=" + psQuote(uninstallKey) + ";New-Item -Path $k -Force|Out-Null;" +
		"New-ItemProperty -Path $k -Name DisplayName -Value " + psQuote(title) + " -PropertyType String -Force|Out-Null;" +
		"New-ItemProperty -Path $k -Name DisplayVersion -Value " + psQuote(version) + " -PropertyType String -Force|Out-Null;" +
		"New-ItemProperty -Path $k -Name Publisher -Value 'Trio' -PropertyType String -Force|Out-Null;" +
		"New-ItemProperty -Path $k -Name DisplayIcon -Value " + psQuote(exe+",0") + " -PropertyType String -Force|Out-Null;" +
		"New-ItemProperty -Path $k -Name InstallLocation -Value " + psQuote(dir) + " -PropertyType String -Force|Out-Null;" +
		"New-ItemProperty -Path $k -Name UninstallString -Value " + psQuote(`"`+exe+`" -uninstall`) + " -PropertyType String -Force|Out-Null;" +
		"New-ItemProperty -Path $k -Name QuietUninstallString -Value " + psQuote(`"`+exe+`" -uninstall -silent`) + " -PropertyType String -Force|Out-Null;" +
		"New-ItemProperty -Path $k -Name NoModify -Value 1 -PropertyType DWord -Force|Out-Null;" +
		"New-ItemProperty -Path $k -Name NoRepair -Value 1 -PropertyType DWord -Force|Out-Null;" +
		"New-ItemProperty -Path $k -Name URLInfoAbout -Value 'https://trioerp.pages.dev' -PropertyType String -Force|Out-Null;" +
		"New-ItemProperty -Path $k -Name HelpTelephone -Value '01228827915' -PropertyType String -Force|Out-Null"
}

func install(self string, z []byte, exeLen int64, launch bool) {
	dir := home()
	exe := filepath.Join(dir, exeName)
	logLine("تثبيت «" + title + "» في " + dir)
	if strings.EqualFold(filepath.Clean(filepath.Dir(self)), filepath.Clean(dir)) {
		tell("شغّل ملف التجهيز من مكانه (الفلاشة أو مجلد التنزيلات)، لا من مجلد البرنامج.", true)
		return
	}
	stopApp()
	if err := os.MkdirAll(dir, 0755); err != nil {
		tell("تعذّر إنشاء مجلد البرنامج: "+err.Error(), true)
		return
	}
	old, _ := loadPair(filepath.Join(dir, "pair.json"))
	p, err := extract(z, dir)
	if err != nil {
		tell("ملف التجهيز تالف أو غير مكتمل ("+err.Error()+").\nاطلب من صاحب المحل ملفًا جديدًا.", true)
		return
	}
	if old != nil && old.ShopID != p.ShopID { // this PC moves to another shop: its saved copy of the old shop's till goes
		os.RemoveAll(filepath.Join(dir, "cache"))
		os.Remove(filepath.Join(dir, "state.json"))
	}
	if err := copyExe(self, exeLen, exe); err != nil {
		tell("تعذّر نسخ البرنامج: "+err.Error(), true)
		return
	}
	if err := powershell(shortcutScript(dir, exe, p.Version)); err != nil {
		logLine("الاختصار أو قائمة البرامج: " + err.Error())
	}
	msg := "تم تثبيت «" + title + "» على هذا الجهاز ✔\n\nالمحل: " + p.Shop + "\nالجهاز: " + p.DeviceName +
		"\n\nافتحه من اختصار «" + title + "» على سطح المكتب."
	if old != nil {
		msg = "تم تحديث «" + title + "» وربطه من جديد ✔\n\nالمحل: " + p.Shop + "\nالجهاز: " + p.DeviceName
	}
	if !hasWebView2() {
		msg += "\n\nتنبيه: هذا الجهاز لا يحتوي على مكوّن «Microsoft Edge WebView2»، فستفتح شاشة الكاشير في متصفح Edge مؤقتًا." +
			"\nلتعمل في نافذتها الخاصة: صِل الجهاز بالإنترنت مرة واحدة وثبّت WebView2 من https://go.microsoft.com/fwlink/p/?LinkId=2124703 ثم أعد تشغيل هذا الملف."
	}
	tell(msg, false)
	if launch {
		c := exec.Command(exe)
		c.Dir = dir
		c.Start()
	}
}

func uninstall(dir string) {
	if !silent && !msgYesNo(title, "هل تريد إزالة «"+title+"» من هذا الجهاز؟\n\nالفواتير المحفوظة على الجهاز ولم تُرسل بعد تبقى محفوظة، وتُرسل إذا ثبّت البرنامج مرة أخرى.") {
		return
	}
	stopApp()
	script := "$w=[Environment]::GetFolderPath('Desktop');$p=[Environment]::GetFolderPath('Programs');" +
		"Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $w " + psQuote(title+".lnk") + "),(Join-Path $p " + psQuote(title+".lnk") + ");" +
		"Remove-Item -Recurse -Force -ErrorAction SilentlyContinue " + psQuote(uninstallKey)
	powershell(script)
	// the folder holds this running exe: a hidden cmd removes it a moment after this program ends
	c := exec.Command("cmd", "/c", "ping -n 3 127.0.0.1 >nul & rmdir /s /q \""+dir+"\"")
	hide(c)
	c.Start()
	tell("تمت إزالة «"+title+"» ✔", false)
}
