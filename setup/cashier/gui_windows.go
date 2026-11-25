//go:build windows

package main

import (
	"os/exec"
	"syscall"
	"unsafe"
)

var user32 = syscall.NewLazyDLL("user32.dll")

func hide(c *exec.Cmd) {
	c.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: 0x08000000} // CREATE_NO_WINDOW
}

func msgBox(title, text string, warn bool) {
	t, _ := syscall.UTF16PtrFromString(text)
	h, _ := syscall.UTF16PtrFromString(title)
	flags := uintptr(0x40 | 0x2000 | 0x80000 | 0x40000 | 0x10000) // info | RTL reading | right | topmost | foreground
	if warn {
		flags = 0x30 | 0x2000 | 0x80000 | 0x40000 | 0x10000
	}
	user32.NewProc("MessageBoxW").Call(0, uintptr(unsafe.Pointer(t)), uintptr(unsafe.Pointer(h)), flags)
}

func msgYesNo(title, text string) bool {
	t, _ := syscall.UTF16PtrFromString(text)
	h, _ := syscall.UTF16PtrFromString(title)
	r, _, _ := user32.NewProc("MessageBoxW").Call(0, uintptr(unsafe.Pointer(t)), uintptr(unsafe.Pointer(h)),
		uintptr(0x4|0x20|0x2000|0x80000|0x40000|0x10000))
	return r == 6
}

func regString(root syscall.Handle, path, name string) string {
	var h syscall.Handle
	p, _ := syscall.UTF16PtrFromString(path)
	if syscall.RegOpenKeyEx(root, p, 0, syscall.KEY_READ, &h) != nil {
		return ""
	}
	defer syscall.RegCloseKey(h)
	nm, _ := syscall.UTF16PtrFromString(name)
	var typ, n uint32
	if syscall.RegQueryValueEx(h, nm, nil, &typ, nil, &n) != nil || n < 2 {
		return ""
	}
	buf := make([]uint16, n/2+1)
	if syscall.RegQueryValueEx(h, nm, nil, &typ, (*byte)(unsafe.Pointer(&buf[0])), &n) != nil {
		return ""
	}
	return syscall.UTF16ToString(buf)
}

// hasWebView2: the same check as Trio-Setup.exe (trio.iss NeedsWebView2)
func hasWebView2() bool {
	const id = `{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}`
	for _, k := range []struct {
		root syscall.Handle
		path string
	}{
		{syscall.HKEY_LOCAL_MACHINE, `SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\` + id},
		{syscall.HKEY_LOCAL_MACHINE, `SOFTWARE\Microsoft\EdgeUpdate\Clients\` + id},
		{syscall.HKEY_CURRENT_USER, `Software\Microsoft\EdgeUpdate\Clients\` + id},
	} {
		if v := regString(k.root, k.path, "pv"); v != "" && v != "0.0.0.0" {
			return true
		}
	}
	return false
}

// shellLater: a hidden «cmd /c <line>» with the line passed as is (Go would escape its quotes the C way, which cmd does not read)
func shellLater(line string) *exec.Cmd {
	c := exec.Command("cmd")
	c.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: 0x08000000, CmdLine: `cmd /c ` + line}
	return c
}
