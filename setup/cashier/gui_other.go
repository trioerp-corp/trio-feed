//go:build !windows

package main

import (
	"fmt"
	"os/exec"
)

func hide(c *exec.Cmd) {}

func msgBox(title, text string, warn bool) { fmt.Println("[" + title + "] " + text) }

func msgYesNo(title, text string) bool {
	fmt.Println("[" + title + "] " + text + " → yes")
	return true
}

func hasWebView2() bool { return true }

func shellLater(line string) *exec.Cmd { return exec.Command("sh", "-c", "true") }
