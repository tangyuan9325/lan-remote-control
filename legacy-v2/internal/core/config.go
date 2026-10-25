// Package core holds shared configuration, auth and path-safety helpers
// for Legacy V2 (Go rewrite of the rc14 Python refactor).
package core

import (
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"net"
	"os"
	"path/filepath"
	"regexp"
	"strings"
)

const Version = "2.0.0-go"

// Config is the global runtime configuration (CLI flags fill it once at boot).
type Config struct {
	Port          int
	Password      string // empty = no auth (a warning is printed at boot)
	EnableCmd     bool   // RCE surface: opt-in only, mirrors rc14 --enable-cmd
	FPS           int
	Quality       int
	ResW, ResH    int
	CameraEnabled bool
	MicEnabled    bool
	UploadDir     string
	Token         string // random per-process token used by the web console
	MaxStreamEdge int    // hard cap on encoded long edge (CPU saver)
}

var Cfg = Config{
	Port:          5000,
	FPS:           15,
	Quality:       60,
	ResW:          1280,
	ResH:          720,
	MaxStreamEdge: 1920,
}

// ClampSettings normalizes out-of-range values coming from CLI or clients.
func (c *Config) ClampSettings() {
	if c.FPS < 1 || c.FPS > 60 {
		c.FPS = 15
	}
	if c.Quality < 10 || c.Quality > 95 {
		c.Quality = 60
	}
	if c.ResW < 2 || c.ResH < 2 || c.ResW > 7680 || c.ResH > 4320 {
		c.ResW, c.ResH = 1280, 720
	}
	// cap encode resolution on the long edge — big CPU saver (parity with rc14)
	long := c.ResW
	if c.ResH > long {
		long = c.ResH
	}
	if long > c.MaxStreamEdge {
		scale := float64(c.MaxStreamEdge) / float64(long)
		c.ResW = int(float64(c.ResW) * scale)
		c.ResH = int(float64(c.ResH) * scale)
	}
}

// InitUploadDir resolves and creates the uploads directory next to the binary.
func (c *Config) InitUploadDir(base string) error {
	if c.UploadDir == "" {
		c.UploadDir = filepath.Join(base, "uploads")
	}
	return os.MkdirAll(c.UploadDir, 0o750)
}

// CheckPassword performs a constant-time comparison against the configured
// password. An empty configured password means "auth disabled".
// Both sides are SHA-256 digest first so lengths do not leak.
func CheckPassword(candidate string) bool {
	if Cfg.Password == "" {
		return true
	}
	a := sha256.Sum256([]byte(candidate))
	b := sha256.Sum256([]byte(Cfg.Password))
	return subtle.ConstantTimeCompare(a[:], b[:]) == 1
}

// GenToken returns a 128-bit hex token used for the cookie-based web session.
func GenToken() string {
	buf := make([]byte, 16)
	if _, err := rand.Read(buf); err != nil {
		// crypto/rand failure is catastrophic and rare; return a non-secret
		// fallback so the server can still boot (password auth unaffected).
		return "fallback-" + hex.EncodeToString([]byte{byte(os.Getpid()), byte(os.Getpid() >> 8)})
	}
	return hex.EncodeToString(buf)
}

// GetLocalIP returns the primary LAN IPv4 of this host (UDP-trick, no traffic).
func GetLocalIP() string {
	conn, err := net.Dial("udp", "8.8.8.8:80")
	if err != nil {
		return "127.0.0.1"
	}
	defer conn.Close()
	if addr, ok := conn.LocalAddr().(*net.UDPAddr); ok {
		return addr.IP.String()
	}
	return "127.0.0.1"
}

// ---------------------------------------------------------------------------
// Path safety (port of rc14.safe_upload_path)
// ---------------------------------------------------------------------------

var badChars = regexp.MustCompile(`[<>:"/\\|?*\x00-\x1f]`)

// SafeUploadPath maps a client-supplied filename into UploadDir. It rejects
// traversal sequences, absolute paths, hidden dot-files, control chars and
// over-long names, then verifies the resolved path stays inside uploads/.
// Returns ("", false) when the name must not be accepted.
func SafeUploadPath(filename string) (string, bool) {
	if filename == "" {
		return "", false
	}
	// reject BEFORE basename(): "a/../../x" would otherwise sanitize to "x"
	if strings.ContainsAny(filename, `/\`) || strings.Contains(filename, "..") {
		return "", false
	}
	name := strings.TrimSpace(filename)
	if name == "" || strings.HasPrefix(name, ".") || len(name) > 255 {
		return "", false
	}
	if badChars.MatchString(name) {
		return "", false
	}
	base, err := filepath.EvalSymlinks(Cfg.UploadDir)
	if err != nil {
		base = filepath.Clean(Cfg.UploadDir)
	}
	target := filepath.Clean(filepath.Join(base, name))
	if target != base && !strings.HasPrefix(target, base+string(os.PathSeparator)) {
		return "", false
	}
	// refuse symlinked targets escaping the dir
	if real, err := filepath.EvalSymlinks(target); err == nil && real != target {
		if !strings.HasPrefix(real, base+string(os.PathSeparator)) && real != base {
			return "", false
		}
	}
	return target, true
}
