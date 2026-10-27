package adb

import (
	"reflect"
	"testing"
)

func TestParseDevices(t *testing.T) {
	// 含横幅、守护进程提示、空行、无线设备、未授权设备的真实输出样式
	out := `* daemon not running; starting now at tcp:5037
* daemon started successfully
List of devices attached
a388e93	device usb:1-1 product:razor model:Nexus_7 device:flo transport_id:1
192.168.1.5:5555	device product:p model:M2012K11AC device:alioth transport_id:2
emulator-5554	offline
0123456789ABCDEF	unauthorized

`
	got := parseDevices(out)
	if len(got) != 4 {
		t.Fatalf("期望 4 台设备，实际 %d：%+v", len(got), got)
	}
	if got[0].Serial != "a388e93" || got[0].State != "device" || got[0].Model != "Nexus_7" {
		t.Errorf("USB 设备解析错误: %+v", got[0])
	}
	if got[0].IsUsb() != true {
		t.Errorf("a388e93 应判定为 USB")
	}
	if got[1].IPPort != "192.168.1.5:5555" || got[1].IsUsb() {
		t.Errorf("无线设备解析错误: %+v", got[1])
	}
	if got[1].DisplayName() != "M2012K11AC" {
		t.Errorf("DisplayName 应取 model，实际 %q", got[1].DisplayName())
	}
	if got[2].State != "offline" || got[3].State != "unauthorized" {
		t.Errorf("状态解析错误: %+v %+v", got[2], got[3])
	}
}

func TestParseSections(t *testing.T) {
	raw := "@@MODEL@@\nPixel 7\n@@SIZE@@\nPhysical size: 1080x2400\nOverride size: 720x1600\n@@BATTERY@@\n  level: 88\n  status: 2\ntrue\n"
	got := ParseSections(raw)
	want := map[string]string{
		"MODEL":   "Pixel 7",
		"SIZE":    "Physical size: 1080x2400\nOverride size: 720x1600",
		"BATTERY": "level: 88\nstatus: 2\ntrue",
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("ParseSections 结果不符\n got=%v\nwant=%v", got, want)
	}
}

func TestParseSectionsEmptyValue(t *testing.T) {
	// 某个字段无输出时不能影响后续字段（固定行号解析会在这里错位）
	got := ParseSections("@@A@@\n@@B@@\nhello\n@@C@@\n")
	if got["A"] != "" {
		t.Errorf("A 应为空，实际 %q", got["A"])
	}
	if got["B"] != "hello" {
		t.Errorf("B 应为 hello，实际 %q", got["B"])
	}
	if got["C"] != "" {
		t.Errorf("C 应为空，实际 %q", got["C"])
	}
}

func TestParseWxH(t *testing.T) {
	cases := []struct {
		in   string
		w, h int
	}{
		{"Physical size: 1080x2400\n", 1080, 2400},
		// 有 Override 时以 Override 为准（截图尺寸跟随 Override）
		{"Physical size: 1080x2400\nOverride size: 720x1600\n", 720, 1600},
		{"", 0, 0},
		{"Physical size: 0x0\n", 0, 0},
		{"Physical size: 1080x\n", 0, 0},
	}
	for _, c := range cases {
		w, h := parseWxH(c.in)
		if w != c.w || h != c.h {
			t.Errorf("parseWxH(%q) = %d,%d，期望 %d,%d", c.in, w, h, c.w, c.h)
		}
	}
}

func TestParseDensity(t *testing.T) {
	if got := parseDensity("Physical density: 420\n"); got != "420" {
		t.Errorf("期望 420，实际 %q", got)
	}
	if got := parseDensity("Physical density: 420\nOverride density: 320\n"); got != "320" {
		t.Errorf("有 Override 时应用 320，实际 %q", got)
	}
	if got := parseDensity(""); got != "" {
		t.Errorf("期望空，实际 %q", got)
	}
}

func TestParseOrientation(t *testing.T) {
	cases := []struct {
		name          string
		rotIn, rotWin string
		want          int
		ok            bool
	}{
		{"SurfaceOrientation 0", "SurfaceOrientation: 0\n", "", 0, true},
		{"SurfaceOrientation 1", "SurfaceOrientation: 1\n", "", 90, true},
		{"SurfaceOrientation 3", "SurfaceOrientation: 3\n", "", 270, true},
		{"回退到 window", "", "  mCurrentRotation=ROTATION_90 mDisplayRotation=2\n", 90, true},
		{"两者都没有", "", "", 0, false},
		{"非法值忽略", "SurfaceOrientation: 9\n", "", 0, false},
	}
	for _, c := range cases {
		got, ok := parseOrientation(c.rotIn, c.rotWin)
		if ok != c.ok || (ok && got != c.want) {
			t.Errorf("%s: 得到 %d,%v 期望 %d,%v", c.name, got, ok, c.want, c.ok)
		}
	}
}

func TestParseBattery(t *testing.T) {
	lvl, ch, ok := parseBattery("  level: 85\n  status: 2\n")
	if !ok || lvl != 85 || !ch {
		t.Errorf("充电状态解析错误: %d %v %v", lvl, ch, ok)
	}
	lvl, ch, ok = parseBattery("  level: 30\n  status: 3\n")
	if !ok || lvl != 30 || ch {
		t.Errorf("放电状态解析错误: %d %v %v", lvl, ch, ok)
	}
	if _, _, ok := parseBattery(""); ok {
		t.Errorf("无输出时应返回 ok=false")
	}
}
