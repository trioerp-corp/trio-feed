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
