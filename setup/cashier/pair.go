package main

import (
	"archive/zip"
	"bytes"
	"encoding/binary"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"strings"
)

// The setup file = TrioCashier.exe + zip + 8 bytes zip length (little endian) + «TRIOCSH1» (pos/cashier_setup.py builds it).
const magic = "TRIOCSH1"

// Pair: pair.json, written by the owner's program for one cashier device.
type Pair struct {
	V          int      `json:"v"`
	Shop       string   `json:"shop"`
	ShopID     string   `json:"shop_id"`
	Device     int      `json:"device"`
	DeviceName string   `json:"device_name"`
	Token      string   `json:"token"`
	Host       string   `json:"host"`
	IPs        []string `json:"ips"`
	Port       int      `json:"port"`
	Made       string   `json:"made"`
	Version    string   `json:"version"`
}

func parsePair(b []byte) (*Pair, error) {
	var p Pair
	if err := json.Unmarshal(b, &p); err != nil {
		return nil, err
	}
	if p.V != 1 || p.Token == "" || p.ShopID == "" {
		return nil, errors.New("pair.json incomplete")
	}
	if p.Port == 0 {
		p.Port = ownerPort
	}
	return &p, nil
}

func loadPair(path string) (*Pair, error) {
	b, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	return parsePair(b)
}

// readPayload: the zip at the end of the file and the length of the program part before it; an error = no payload.
func readPayload(path string) ([]byte, int64, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, 0, err
	}
	defer f.Close()
	st, err := f.Stat()
	if err != nil {
		return nil, 0, err
	}
	size := st.Size()
	if size < 16 {
		return nil, 0, errors.New("no payload")
	}
	tail := make([]byte, 16)
	if _, err := f.ReadAt(tail, size-16); err != nil {
		return nil, 0, err
	}
	if string(tail[8:]) != magic {
		return nil, 0, errors.New("no payload")
	}
	n := int64(binary.LittleEndian.Uint64(tail[:8]))
	if n <= 0 || n > size-16 {
		return nil, 0, errors.New("bad payload")
	}
	z := make([]byte, n)
	if _, err := f.ReadAt(z, size-16-n); err != nil {
		return nil, 0, err
	}
	return z, size - 16 - n, nil
}

// extract writes the payload into dir (window\…, pair.json) and returns the pair; nothing outside dir is ever written.
func extract(z []byte, dir string) (*Pair, error) {
	r, err := zip.NewReader(bytes.NewReader(z), int64(len(z)))
	if err != nil {
		return nil, err
	}
	var pair *Pair
	for _, f := range r.File {
		if f.Name == "pair.json" {
			rc, err := f.Open()
			if err != nil {
				return nil, err
			}
			b, _ := io.ReadAll(io.LimitReader(rc, 1<<20))
			rc.Close()
			if pair, err = parsePair(b); err != nil {
				return nil, err
			}
		}
	}
	if pair == nil {
		return nil, errors.New("pair.json missing")
	}
	clean := filepath.Clean(dir) + string(os.PathSeparator)
	for _, f := range r.File {
		name := strings.ReplaceAll(f.Name, "\\", "/")
		if f.FileInfo().IsDir() || strings.Contains(name, "..") {
			continue
		}
		target := filepath.Join(dir, filepath.FromSlash(name))
		if !strings.HasPrefix(target, clean) {
			continue
		}
		os.MkdirAll(filepath.Dir(target), 0755)
		rc, err := f.Open()
		if err != nil {
			return nil, err
		}
		tmp := target + ".new"
		w, err := os.Create(tmp)
		if err != nil {
			rc.Close()
			return nil, err
		}
		_, err = io.Copy(w, rc)
		w.Close()
		rc.Close()
		if err != nil {
			os.Remove(tmp)
			return nil, err
		}
		os.Remove(target)
		if err := os.Rename(tmp, target); err != nil {
			return nil, err
		}
	}
	return pair, nil
}

// State: what the app learnt (the owner PC's last good address; the shop id the server reports).
type State struct {
	Last   string `json:"last"`
	ShopID string `json:"shop_id"`
}

func loadState(dir string) State {
	var s State
	b, _ := os.ReadFile(filepath.Join(dir, "state.json"))
	json.Unmarshal(b, &s)
	return s
}

func saveState(dir string, s State) {
	b, _ := json.Marshal(s)
	tmp := filepath.Join(dir, "state.json.new")
	if os.WriteFile(tmp, b, 0644) == nil {
		os.Remove(filepath.Join(dir, "state.json"))
		os.Rename(tmp, filepath.Join(dir, "state.json"))
	}
}
