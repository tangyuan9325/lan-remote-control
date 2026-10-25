package server

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/tangyuan9325/lan-remote-control/legacy-v2/internal/core"
)

func TestAuthOK(t *testing.T) {
	core.Cfg.Password = "s3cret"
	defer func() { core.Cfg.Password = "" }()

	cases := []struct{ q, cookie, want string }{
		{"password=s3cret", "", "ok"},
		{"password=wrong", "", "deny"},
		{"", "s3cret", "ok"},
		{"", "nope", "deny"},
		{"", "", "deny"},
	}
	for _, c := range cases {
		r := httptest.NewRequest(http.MethodGet, "/?"+c.q, nil)
		if c.cookie != "" {
			r.AddCookie(&http.Cookie{Name: "rc_token", Value: c.cookie})
		}
		got := "deny"
		if authOK(r) {
			got = "ok"
		}
		if got != c.want {
			t.Errorf("q=%q cookie=%q: got %s want %s", c.q, c.cookie, got, c.want)
		}
	}
}

func TestSafeUploadPath(t *testing.T) {
	dir := t.TempDir()
	core.Cfg.UploadDir = dir
	for _, bad := range []string{"../x", "a/../../x", "..\\evil", "/etc/passwd", ".hidden", strings.Repeat("n", 300), "a\x00b", "c:d", "p*name"} {
		if _, ok := core.SafeUploadPath(bad); ok {
			t.Errorf("must reject %q", bad)
		}
	}
	for _, good := range []string{"report.txt", "photo_2026.png", "中文文件.doc"} {
		p, ok := core.SafeUploadPath(good)
		if !ok || !strings.HasPrefix(p, dir) {
			t.Errorf("must accept %q (got %q,%v)", good, p, ok)
		}
	}
}

func TestStreamCap(t *testing.T) {
	s := &Server{}
	// fill up to cap
	atomic.StoreInt64(&s.streams, maxConcurrentStreams)
	w := httptest.NewRecorder()
	r := httptest.NewRequest(http.MethodGet, "/video_feed", nil)
	s.handleVideoFeed(w, r)
	if w.Code != http.StatusServiceUnavailable {
		t.Fatalf("expected 503 at cap, got %d", w.Code)
	}
	if got := atomic.LoadInt64(&s.streams); got != maxConcurrentStreams {
		t.Fatalf("counter must be restored on reject, got %d", got)
	}
}

func TestSecHeaders(t *testing.T) {
	h := secHeaders(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte("hi"))
	}))
	w := httptest.NewRecorder()
	h.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/", nil))
	if w.Header().Get("X-Content-Type-Options") != "nosniff" {
		t.Error("missing nosniff header")
	}
	if w.Header().Get("X-Frame-Options") != "SAMEORIGIN" {
		t.Error("missing frame-options header")
	}
}
