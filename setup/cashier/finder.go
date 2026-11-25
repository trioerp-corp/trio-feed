package main

import (
	"encoding/json"
	"net"
	"net/http"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"
)

// Finder keeps the owner PC's address: the last good one, then its name, then the addresses it had when the file was made,
// then a UDP broadcast. A candidate counts only when it accepts this device's key (/pos/api/ping/ → 200).
type Finder struct {
	mu      sync.Mutex
	dir     string
	pair    *Pair
	st      State
	cur     string // host:port in use ("" = not connected)
	stopped bool   // the owner PC answered 401: this device is stopped («إيقاف») or its key is no longer valid
	kick    chan struct{}
	client  *http.Client
	targets func() []string
}

func newFinder(dir string, p *Pair) *Finder {
	return &Finder{dir: dir, pair: p, st: loadState(dir), kick: make(chan struct{}, 1),
		client:  &http.Client{Timeout: 4 * time.Second, Transport: &http.Transport{Proxy: nil, DialContext: (&net.Dialer{Timeout: 2500 * time.Millisecond}).DialContext}},
		targets: discoverTargets}
}

func (f *Finder) shopID() string {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.st.ShopID != "" {
		return f.st.ShopID
	}
	return f.pair.ShopID
}

// Current: the address to use, or "" when the owner PC is not reachable now.
func (f *Finder) Current() string {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.cur
}

func (f *Finder) Status() (online, stopped bool, addr string) {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.cur != "", f.stopped, f.cur
}

// Lost: a request to addr failed (connection refused / timeout) → look again now.
func (f *Finder) Lost(addr string) {
	f.mu.Lock()
	if f.cur == addr {
		f.cur = ""
	}
	f.mu.Unlock()
	select {
	case f.kick <- struct{}{}:
	default:
	}
}

func (f *Finder) candidates() []string {
	f.mu.Lock()
	defer f.mu.Unlock()
	port := strconv.Itoa(f.pair.Port)
	var out []string
	seen := map[string]bool{}
	add := func(a string) {
		if a != "" && !seen[a] {
			seen[a] = true
			out = append(out, a)
		}
	}
	add(f.st.Last)
	if f.pair.Host != "" {
		add(net.JoinHostPort(f.pair.Host, port))
	}
	for _, ip := range f.pair.IPs {
		add(net.JoinHostPort(ip, port))
	}
	return out
}

// verify: 200 = the right shop and an active device; 401 = reachable but this device is refused; 0 = not reachable.
func (f *Finder) verify(addr string) int {
	req, _ := http.NewRequest("GET", "http://"+addr+"/pos/api/ping/", nil)
	req.Header.Set("X-Device-Token", f.pair.Token)
	r, err := f.client.Do(req)
	if err != nil {
		return 0
	}
	defer r.Body.Close()
	if r.StatusCode == 200 {
		var v struct {
			OK     bool   `json:"ok"`
			ShopID string `json:"shop_id"`
		}
		if json.NewDecoder(r.Body).Decode(&v) != nil || !v.OK {
			return 0 // some other web server
		}
		f.mu.Lock()
		if v.ShopID != "" && v.ShopID != f.st.ShopID {
			f.st.ShopID = v.ShopID // a shop restored on a new PC gets a new id: follow it
		}
		f.mu.Unlock()
	}
	return r.StatusCode
}

func (f *Finder) use(addr string) {
	f.mu.Lock()
	changed := f.cur != addr || f.st.Last != addr
	f.cur, f.stopped = addr, false
	f.st.Last = addr
	st := f.st
	f.mu.Unlock()
	if changed {
		logLine("متصل بجهاز المحل: " + addr)
		saveState(f.dir, st)
	}
}

// search: one pass over every way of finding the owner PC; true when connected.
func (f *Finder) search() bool {
	refused := false
	try := func(list []string) bool {
		for _, a := range list {
			switch f.verify(a) {
			case 200:
				f.use(a)
				return true
			case 401:
				refused = true
			}
		}
		return false
	}
	if try(f.candidates()) {
		return true
	}
	if try(discover(f.shopID(), f.targets(), 1500*time.Millisecond)) {
		return true
	}
	f.mu.Lock()
	f.cur, f.stopped = "", refused
	f.mu.Unlock()
	return false
}

// Run: checks the connection every few seconds (and right away after a failed request) until stop is closed.
func (f *Finder) Run(stop <-chan struct{}) {
	for {
		wait := 5 * time.Second
		if cur := f.Current(); cur != "" {
			if code := f.verify(cur); code != 200 {
				logLine("انقطع الاتصال بجهاز المحل: " + cur + " (" + strconv.Itoa(code) + ")")
				f.search()
			} else {
				wait = 10 * time.Second
			}
		} else {
			f.search()
		}
		select {
		case <-stop:
			return
		case <-f.kick:
		case <-time.After(wait):
		}
	}
}

// ---------------------------------------------------------------- UDP discovery (the owner's launcher answers on 8001)

func discoverTargets() []string {
	out := []string{"255.255.255.255:8001"}
	if ifs, err := net.Interfaces(); err == nil {
		for _, it := range ifs {
			if it.Flags&net.FlagUp == 0 || it.Flags&net.FlagBroadcast == 0 {
				continue
			}
			addrs, _ := it.Addrs()
			for _, a := range addrs {
				n, ok := a.(*net.IPNet)
				if !ok || n.IP.To4() == nil || n.IP.IsLoopback() {
					continue
				}
				ip, m := n.IP.To4(), n.Mask
				if len(m) == 16 {
					m = m[12:]
				}
				b := make(net.IP, 4)
				for i := range b {
					b[i] = ip[i] | ^m[i]
				}
				out = append(out, b.String()+":8001")
			}
		}
	}
	for _, x := range strings.Split(os.Getenv("TRIO_DISCOVER_EXTRA"), ",") {
		if x = strings.TrimSpace(x); x != "" {
			if !strings.Contains(x, ":") {
				x += ":8001"
			}
			out = append(out, x)
		}
	}
	return out
}

// parseReply: the owner PC's address from one answer, only when it carries our shop id.
func parseReply(b []byte, shop string, from net.IP) (string, bool) {
	var v struct {
		Trio int    `json:"trio"`
		Shop string `json:"shop"`
		Port int    `json:"port"`
	}
	if shop == "" || json.Unmarshal(b, &v) != nil || v.Trio != 1 || v.Shop != shop || v.Port <= 0 || v.Port > 65535 || from == nil {
		return "", false
	}
	return net.JoinHostPort(from.String(), strconv.Itoa(v.Port)), true
}

// discover: asks every target, collects the matching answers for wait.
func discover(shop string, targets []string, wait time.Duration) []string {
	if shop == "" {
		return nil
	}
	pc, err := net.ListenPacket("udp4", ":0")
	if err != nil {
		return nil
	}
	defer pc.Close()
	msg := []byte("TRIO-FIND 1 " + shop)
	for _, t := range targets {
		if a, err := net.ResolveUDPAddr("udp4", t); err == nil {
			pc.WriteTo(msg, a)
		}
	}
	var out []string
	seen := map[string]bool{}
	deadline := time.Now().Add(wait)
	buf := make([]byte, 512)
	for time.Now().Before(deadline) {
		pc.SetReadDeadline(deadline)
		n, from, err := pc.ReadFrom(buf)
		if err != nil {
			if ne, ok := err.(net.Error); ok && ne.Timeout() {
				break
			}
			continue
		}
		ua, _ := from.(*net.UDPAddr)
		if ua == nil {
			continue
		}
		if a, ok := parseReply(buf[:n], shop, ua.IP); ok && !seen[a] {
			seen[a] = true
			out = append(out, a)
		}
	}
	return out
}
