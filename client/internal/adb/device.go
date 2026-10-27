package adb

import (
	"strconv"
	"strings"
	"time"
)

// probeTimeout 单次探测脚本的超时。脚本内含 dumpsys，慢设备上也需要留足余量。
const probeTimeout = 15 * time.Second

// DeviceInfo 描述一台设备的运行信息。
type DeviceInfo struct {
	Model        string
	Manufacturer string
	AndroidVer   string
	SDK          string
	Width        int
	Height       int
	Density      string
	Orientation  int // 0/90/180/270
	Battery      int
	Charging     bool
}

// Info 采集设备信息（只启动 1 次 adb 进程）。
func (a *Adb) Info(serial string) DeviceInfo {
	var di DeviceInfo
	di.Model = serial
	sec, err := a.probe(serial, true)
	if err != nil && len(sec) == 0 {
		return di
	}
	di.Model = firstNonEmpty(sec["MODEL"], sec["MARKET"], serial)
	di.Manufacturer = sec["MANU"]
	di.AndroidVer = sec["REL"]
	di.SDK = sec["SDK"]
	di.applyDynamic(sec)
	return di
}

// Refresher 只刷新可变信息（尺寸/方向/电池），不重复拉取静态属性。
func (a *Adb) Refresher(serial string, di *DeviceInfo) {
	sec, err := a.probe(serial, false)
	if err != nil && len(sec) == 0 {
		return
	}
	di.applyDynamic(sec)
}

// probe 用一次 adb shell 调用同时取回多个字段。
//
// 关键优化：dumpsys 的输出在设备端就已被 grep/head 过滤，
// 避免把几百 KB 的 dumpsys 全文经 USB/WiFi 传回主机。
func (a *Adb) probe(serial string, withStatic bool) (map[string]string, error) {
	var b strings.Builder
	add := func(key, cmd string) {
		b.WriteString("echo @@")
		b.WriteString(key)
		b.WriteString("@@\n")
		b.WriteString(cmd)
		b.WriteByte('\n')
	}
	if withStatic {
		add("MODEL", "getprop ro.product.model")
		add("MARKET", "getprop ro.product.marketname")
		add("MANU", "getprop ro.product.manufacturer")
		add("REL", "getprop ro.build.version.release")
		add("SDK", "getprop ro.build.version.sdk")
	}
	add("SIZE", "wm size 2>/dev/null")
	add("DENSITY", "wm density 2>/dev/null")
	add("ROT_INPUT", "dumpsys input 2>/dev/null | grep SurfaceOrientation: | head -n 1")
	add("ROT_WIN", "dumpsys window displays 2>/dev/null | grep mCurrentRotation= | head -n 1")
	add("BATTERY", "dumpsys battery 2>/dev/null | grep -E 'level:|status:'")
	// 末尾 true：任何一条子命令失败都不要让整个脚本返回非 0
	b.WriteString("true\n")

	raw, err := a.ShellScript(serial, b.String(), probeTimeout)
	return ParseSections(raw), err
}

// applyDynamic 从分段结果填充尺寸/密度/方向/电池。
func (di *DeviceInfo) applyDynamic(sec map[string]string) {
	if s, ok := sec["SIZE"]; ok {
		if w, h := parseWxH(s); w > 0 && h > 0 {
			di.Width, di.Height = w, h
		}
	}
	if d, ok := sec["DENSITY"]; ok {
		di.Density = parseDensity(d)
	}
	if o, ok := parseOrientation(sec["ROT_INPUT"], sec["ROT_WIN"]); ok {
		di.Orientation = o
	}
	if lvl, ch, ok := parseBattery(sec["BATTERY"]); ok {
		di.Battery, di.Charging = lvl, ch
	}
}

func firstNonEmpty(vals ...string) string {
	for _, v := range vals {
		if strings.TrimSpace(v) != "" {
			return strings.TrimSpace(v)
		}
	}
	return ""
}

// parseWxH 从 `wm size` 输出解析宽高，优先 Override size（截图尺寸与之对应）。
func parseWxH(out string) (int, int) {
	override := ""
	physical := ""
	for _, line := range strings.Split(out, "\n") {
		line = strings.TrimSpace(line)
		switch {
		case strings.HasPrefix(line, "Override size:"):
			override = line
		case strings.HasPrefix(line, "Physical size:"):
			physical = line
		}
	}
	target := override
	if target == "" {
		target = physical
	}
	return parseDim(target)
}

func parseDim(s string) (int, int) {
	idx := strings.Index(s, ":")
	if idx < 0 {
		return 0, 0
	}
	rest := strings.TrimSpace(s[idx+1:])
	parts := strings.SplitN(rest, "x", 2)
	if len(parts) != 2 {
		return 0, 0
	}
	w, err1 := strconv.Atoi(strings.TrimSpace(parts[0]))
	h, err2 := strconv.Atoi(strings.TrimSpace(parts[1]))
	if err1 != nil || err2 != nil || w <= 0 || h <= 0 {
		return 0, 0
	}
	return w, h
}

// parseDensity 解析 `wm density` 输出，优先 Override density。
func parseDensity(out string) string {
	fallback := ""
	for _, line := range strings.Split(out, "\n") {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, "Override density:") {
			return strings.TrimSpace(strings.TrimPrefix(line, "Override density:"))
		}
		if strings.HasPrefix(line, "Physical density:") && fallback == "" {
			fallback = strings.TrimSpace(strings.TrimPrefix(line, "Physical density:"))
		}
	}
	return fallback
}

// parseOrientation 解析方向，优先 dumpsys input 的 SurfaceOrientation，
// 其次 dumpsys window displays 的 mCurrentRotation。
func parseOrientation(rotInput, rotWin string) (int, bool) {
	for _, line := range strings.Split(rotInput, "\n") {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, "SurfaceOrientation:") {
			v := strings.TrimSpace(strings.TrimPrefix(line, "SurfaceOrientation:"))
			switch v {
			case "0", "1", "2", "3":
				return atoi(v) * 90, true
			}
		}
	}
	for _, line := range strings.Split(rotWin, "\n") {
		line = strings.TrimSpace(line)
		if i := strings.Index(line, "mCurrentRotation="); i >= 0 {
			v := line[i+len("mCurrentRotation="):]
			if j := strings.IndexAny(v, " \t"); j >= 0 {
				v = v[:j]
			}
			switch strings.TrimSpace(v) {
			case "ROTATION_0":
				return 0, true
			case "ROTATION_90":
				return 90, true
			case "ROTATION_180":
				return 180, true
			case "ROTATION_270":
				return 270, true
			}
		}
	}
	return 0, false
}

// parseBattery 解析 dumpsys battery 的 level/status。
// status: 2=charging, 5=full(插电)。
func parseBattery(out string) (int, bool, bool) {
	level, status := -1, -1
	found := false
	for _, line := range strings.Split(out, "\n") {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, "level:") {
			level = atoi(strings.TrimSpace(strings.TrimPrefix(line, "level:")))
			found = true
		}
		if strings.HasPrefix(line, "status:") {
			status = atoi(strings.TrimSpace(strings.TrimPrefix(line, "status:")))
		}
	}
	if !found {
		return -1, false, false
	}
	return level, status == 2 || status == 5, true
}

func atoi(s string) int {
	n, _ := strconv.Atoi(s)
	return n
}
