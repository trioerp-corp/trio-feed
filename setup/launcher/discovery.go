package main

// 1.26.1 cashier PCs (tools/cashier «Trio – الكاشير»): a cashier PC looks for this PC on the shop network when the last
// address it knew and this PC's name both fail (the router gave a new IP). It sends «TRIO-FIND 1 <shop id>» to UDP 8001
// (broadcast); this launcher answers only a request carrying ITS shop id (data/shop_id.txt, written by the program when the
// owner makes a cashier file) with {"trio":1,"shop":…,"port":8000,"host":…}. The cashier then checks the server with its
// device key (/pos/api/ping/) before using it — the answer itself gives nothing away.

import (
	"encoding/json"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

const discoverPort = "8001"

func shopID(base string) string {
	b, _ := os.ReadFile(filepath.Join(base, "data", "shop_id.txt"))
	return strings.TrimSpace(string(b))
}

// discoveryAnswer: the reply to one packet, or nil (not a Trio request, another shop, or no shop id yet)
func discoveryAnswer(req []byte, shop string, port int, host string) []byte {
	if len(req) > 200 || shop == "" {
		return nil
	}
	f := strings.Fields(string(req))
	if len(f) != 3 || f[0] != "TRIO-FIND" || f[1] != "1" || f[2] != shop {
		return nil
	}
	b, _ := json.Marshal(map[string]any{"trio": 1, "shop": shop, "port": port, "host": host})
	return b
}

// discoveryListen answers on addr (":8001" in the program) until the connection is closed.
func discoveryListen(addr string, shop func() string) (net.PacketConn, error) {
	pc, err := net.ListenPacket("udp4", addr)
	if err != nil {
		return nil, err
	}
	host, _ := os.Hostname()
	go func() {
		buf := make([]byte, 512)
		for {
			n, from, err := pc.ReadFrom(buf)
			if err != nil {
				if ne, ok := err.(net.Error); ok && ne.Timeout() {
					continue
				}
				if strings.Contains(err.Error(), "closed") {
					return
				}
				continue // Windows reports ICMP «port unreachable» of an earlier reply as a read error: keep listening
			}
			if ans := discoveryAnswer(buf[:n], shop(), 8000, host); ans != nil {
				pc.WriteTo(ans, from)
			}
		}
	}()
	return pc, nil
}

// udpRule: the firewall rule for the answers (needs admin: Trio-Setup.exe adds it; a copy updated through the channel tries
// once per start until it works — when it cannot, cashier PCs still reach this PC by its name / last address on TCP 8000).
func udpRule() {
	mark := filepath.Join(dir, "data", ".fw-udp8001")
	if _, err := os.Stat(mark); err == nil {
		return
	}
	del := exec.Command("netsh", "advfirewall", "firewall", "delete", "rule", "name=Trio 8001 UDP")
	hide(del)
	del.Run()
	fw := exec.Command("netsh", "advfirewall", "firewall", "add", "rule", "name=Trio 8001 UDP", "dir=in", "action=allow", "protocol=UDP", "localport=8001")
	hide(fw)
	if fw.Run() == nil {
		os.WriteFile(mark, []byte("ok"), 0644)
	}
}
