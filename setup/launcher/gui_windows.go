//go:build windows

package main

import (
	"os/exec"
	"syscall"
	"unsafe"
)

var user32 = syscall.NewLazyDLL("user32.dll")

// no console: child programs (python, netsh, powershell, cmd) start hidden as well
func hide(c *exec.Cmd) {
	c.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: 0x08000000} // CREATE_NO_WINDOW
}

// a normal Windows message box, right-to-left, on top
func msgBox(title, text string, warn bool) {
	t, _ := syscall.UTF16PtrFromString(text)
	h, _ := syscall.UTF16PtrFromString(title)
	flags := uintptr(0x40 | 0x2000 | 0x80000 | 0x40000 | 0x10000) // info | RTL reading | right | topmost | foreground
	if warn {
		flags = 0x30 | 0x2000 | 0x80000 | 0x40000 | 0x10000
	}
	user32.NewProc("MessageBoxW").Call(0, uintptr(unsafe.Pointer(t)), uintptr(unsafe.Pointer(h)), flags)
}

// a yes / no question (Arabic, right-to-left); true = yes
func msgYesNo(title, text string) bool {
	t, _ := syscall.UTF16PtrFromString(text)
	h, _ := syscall.UTF16PtrFromString(title)
	r, _, _ := user32.NewProc("MessageBoxW").Call(0, uintptr(unsafe.Pointer(t)), uintptr(unsafe.Pointer(h)),
		uintptr(0x4|0x20|0x2000|0x80000|0x40000|0x10000)) // yes/no | question | RTL | right | topmost | foreground
	return r == 6
}
