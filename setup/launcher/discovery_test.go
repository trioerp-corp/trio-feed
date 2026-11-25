package main

import (
	"encoding/json"
	"net"
	"testing"
	"time"
)

func TestDiscoveryAnswer(t *testing.T) {
	if discoveryAnswer([]byte("TRIO-FIND 1 abc"), "abc", 8000, "SHOP-PC") == nil {
		t.Fatal("own shop id: no answer")
	}
	var v map[string]any
	json.Unmarshal(discoveryAnswer([]byte("TRIO-FIND 1 abc\n"), "abc", 8000, "SHOP-PC"), &v)
	if v["shop"] != "abc" || v["port"] != float64(8000) || v["host"] != "SHOP-PC" || v["trio"] != float64(1) {
		t.Fatalf("answer %v", v)
	}
	for _, req := range []string{"TRIO-FIND 1 other", "TRIO-FIND 2 abc", "HELLO", "", "TRIO-FIND 1", "TRIO-FIND 1 abc extra"} {
		if discoveryAnswer([]byte(req), "abc", 8000, "h") != nil {
			t.Fatalf("answered %q", req)
		}
	}
	if discoveryAnswer([]byte("TRIO-FIND 1 "), "", 8000, "h") != nil {
		t.Fatal("no shop id yet must not answer")
	}
	long := make([]byte, 300)
	if discoveryAnswer(long, "abc", 8000, "h") != nil {
		t.Fatal("long packet answered")
	}
}

func TestDiscoveryListenLoopback(t *testing.T) {
	pc, err := discoveryListen("127.0.0.1:0", func() string { return "s1" })
	if err != nil {
		t.Fatal(err)
	}
	defer pc.Close()
	c, err := net.Dial("udp4", pc.LocalAddr().String())
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	c.Write([]byte("TRIO-FIND 1 wrong"))
	c.Write([]byte("TRIO-FIND 1 s1"))
	c.SetReadDeadline(time.Now().Add(2 * time.Second))
	buf := make([]byte, 512)
	n, err := c.Read(buf)
	if err != nil {
		t.Fatal(err)
	}
	var v map[string]any
	if json.Unmarshal(buf[:n], &v) != nil || v["shop"] != "s1" {
		t.Fatalf("reply %q", buf[:n])
	}
	c.SetReadDeadline(time.Now().Add(300 * time.Millisecond))
	if _, err := c.Read(buf); err == nil {
		t.Fatal("the wrong shop got an answer too")
	}
}
