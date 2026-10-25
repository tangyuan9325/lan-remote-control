package core

import "testing"

func TestClampSettings(t *testing.T) {
	c := Config{FPS: 999, Quality: 200, ResW: 10000, ResH: 9000, MaxStreamEdge: 1920}
	c.ClampSettings()
	if c.FPS != 15 || c.Quality != 60 || c.ResW != 1280 || c.ResH != 720 {
		t.Fatalf("clamp failed: %+v", c)
	}
	c2 := Config{FPS: 30, Quality: 75, ResW: 3840, ResH: 2160, MaxStreamEdge: 1920}
	c2.ClampSettings()
	if c2.ResW != 1920 || c2.ResH != 1080 {
		t.Fatalf("long-edge cap failed: %dx%d", c2.ResW, c2.ResH)
	}
}

func TestCheckPassword(t *testing.T) {
	old := Cfg.Password
	defer func() { Cfg.Password = old }()
	Cfg.Password = ""
	if !CheckPassword("anything") {
		t.Error("empty password must mean auth-disabled")
	}
	Cfg.Password = "hunter2"
	for _, bad := range []string{"", "wrong", "Hunter2", "hunter", "hunter2 "} {
		if CheckPassword(bad) {
			t.Errorf("must reject %q", bad)
		}
	}
	if !CheckPassword("hunter2") {
		t.Error("must accept correct password")
	}
}
