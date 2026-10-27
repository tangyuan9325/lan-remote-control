package server

import (
	"net/http"
	"testing"
	"time"

	"remote-control/client/internal/adb"
)

func TestHostAllowed(t *testing.T) {
	cases := []struct {
		hostport string
		want     bool
	}{
		{"127.0.0.1:8500", true},
		{"localhost:8500", true},
		{"[::1]:8500", true},
		// DNS rebinding：攻击者域名解析到 127.0.0.1，Host 仍是攻击者域名
		{"evil.com:8500", false},
		{"attacker.example.com", false},
		{"192.168.1.10:8500", false},
		// 端口不一致
		{"127.0.0.1:9999", false},
	}
	for _, c := range cases {
		if got := hostAllowed(c.hostport, "8500"); got != c.want {
			t.Errorf("hostAllowed(%q) = %v，期望 %v", c.hostport, got, c.want)
		}
	}
}

func reqWithOrigin(origin string) *http.Request {
	r, _ := http.NewRequest(http.MethodGet, "http://127.0.0.1:8500/api/status", nil)
	if origin != "" {
		r.Header.Set("Origin", origin)
	}
	return r
}

func TestOriginAllowed(t *testing.T) {
	cases := []struct {
		name   string
		origin string
		want   bool
	}{
		// 同源页面：正常放行
		{"同源 127.0.0.1", "http://127.0.0.1:8500", true},
		{"同源 localhost", "http://localhost:8500", true},
		// 非浏览器客户端没有 Origin
		{"无 Origin", "", true},
		// 跨站 WebSocket 劫持（CSWSH）：必须拒绝
		{"跨站 https", "http://evil.com", false},
		{"跨站 http", "https://attacker.io:8500", false},
		{"跨站子域", "http://127.0.0.1.evil.com:8500", false},
		// 沙箱 iframe / 本地文件常发 null
		{"null Origin", "null", false},
	}
	for _, c := range cases {
		if got := originAllowed(reqWithOrigin(c.origin), "8500"); got != c.want {
			t.Errorf("%s: originAllowed(%q) = %v，期望 %v", c.name, c.origin, got, c.want)
		}
	}
}

func TestValidHostPort(t *testing.T) {
	good := []string{"192.168.1.100:5555", "127.0.0.1:37000", "my-phone.local:5555"}
	bad := []string{"", "-s", "192.168.1.100", "host:port", ":5555", "host:", "a:1;reboot"}
	for _, s := range good {
		if !validHostPort(s) {
			t.Errorf("应接受 %q", s)
		}
	}
	for _, s := range bad {
		if validHostPort(s) {
			t.Errorf("应拒绝 %q", s)
		}
	}
}

func TestDigitsOnly(t *testing.T) {
	if !digitsOnly("123456") {
		t.Error("纯数字应通过")
	}
	for _, s := range []string{"", "12a", "1 2", "-1"} {
		if digitsOnly(s) {
			t.Errorf("应拒绝 %q", s)
		}
	}
}

func TestValidPoint(t *testing.T) {
	// 已知屏幕尺寸
	if !validPoint(0, 0, 1080, 2400) {
		t.Error("左上角应合法")
	}
	if !validPoint(1080, 2400, 1080, 2400) {
		t.Error("右下角边界应合法")
	}
	if validPoint(1081, 100, 1080, 2400) {
		t.Error("越界 X 应被拒绝")
	}
	if validPoint(100, 2401, 1080, 2400) {
		t.Error("越界 Y 应被拒绝")
	}
	// 负值一律拒绝（原实现会把负数直接拼进 adb input 命令）
	if validPoint(-1, 10, 1080, 2400) {
		t.Error("负坐标应被拒绝")
	}
	// 尺寸未知时只做上限保护
	if !validPoint(100, 200, 0, 0) {
		t.Error("尺寸未知时应放行合理坐标")
	}
	if validPoint(40000, 10, 0, 0) {
		t.Error("超大坐标应被拒绝")
	}
}

func TestClampInt(t *testing.T) {
	if clampInt(5, 20, 3000) != 20 {
		t.Error("下界钳制失败")
	}
	if clampInt(999999, 20, 3000) != 3000 {
		t.Error("上界钳制失败（原实现会把超大 duration 传给 adb，导致命令挂住 30 秒）")
	}
	if clampInt(300, 20, 3000) != 300 {
		t.Error("区间内的值不应被修改")
	}
}

func TestResolveVideoMode(t *testing.T) {
	// 无 ffmpeg 时任何需要解码的模式都必须降级为 png
	for _, m := range []string{"auto", "record", "agent"} {
		if got := resolveVideoMode(m, ""); got != "png" {
			t.Errorf("无 ffmpeg 时 %s 应降级 png，实际 %s", m, got)
		}
	}
	if got := resolveVideoMode("png", "/ffmpeg.exe"); got != "png" {
		t.Errorf("png 应保持 png，实际 %s", got)
	}
	if got := resolveVideoMode("auto", "/ffmpeg.exe"); got != "record" {
		t.Errorf("auto 有 ffmpeg 时应为 record，实际 %s", got)
	}
	if got := resolveVideoMode("agent", "/ffmpeg.exe"); got != "agent" {
		t.Errorf("agent 应保持，实际 %s", got)
	}
}

func TestResolveInputMode(t *testing.T) {
	if resolveInputMode("agent") != "agent" {
		t.Error("agent 应保持")
	}
	for _, m := range []string{"", "auto", "shell", "乱写的"} {
		if got := resolveInputMode(m); got != "shell" {
			t.Errorf("%q 应回落到 shell，实际 %s", m, got)
		}
	}
}

// 关键回归测试：多标签页同时观看时，两路订阅都必须收到帧。
// 原实现每个 Session 只有一个 broadcast 回调，第二个连接会覆盖第一个。
func TestSessionMultiSubscriber(t *testing.T) {
	sess := NewSession(nil, "", "serial-test", "Model", adb.DeviceInfo{Width: 100, Height: 200})
	sub1 := sess.Subscribe()
	sub2 := sess.Subscribe()
	defer sess.Unsubscribe(sub1)
	defer sess.Unsubscribe(sub2)

	if n := sess.SubscriberCount(); n != 2 {
		t.Fatalf("期望 2 个订阅者，实际 %d", n)
	}
	sess.pushFrame([]byte("jpeg-A"))
	for i, sub := range []*Subscriber{sub1, sub2} {
		select {
		case f := <-sub.Frames:
			if string(f) != "jpeg-A" {
				t.Errorf("订阅者 %d 收到内容不符: %q", i, f)
			}
		case <-time.After(time.Second):
			t.Errorf("订阅者 %d 未收到帧", i)
		}
	}
}

// 队列满时应保留最新帧（低延迟优先），而不是把新帧丢掉
func TestSessionPushFrameKeepsLatest(t *testing.T) {
	sess := NewSession(nil, "", "s", "M", adb.DeviceInfo{})
	sub := sess.Subscribe()
	defer sess.Unsubscribe(sub)

	sess.pushFrame([]byte("old"))
	sess.pushFrame([]byte("new")) // 队列里已有一帧，应挤掉旧的
	select {
	case f := <-sub.Frames:
		if string(f) != "new" {
			t.Errorf("应保留最新帧 new，实际 %q", f)
		}
	case <-time.After(time.Second):
		t.Fatal("未收到帧")
	}
}

// Stop 必须关闭所有订阅者，否则 WebSocket 协程会永久阻塞
func TestSessionStopClosesSubscribers(t *testing.T) {
	sess := NewSession(nil, "", "s", "M", adb.DeviceInfo{})
	sess.Start()
	sub := sess.Subscribe()
	sess.Stop()
	select {
	case <-sub.closed:
	case <-time.After(2 * time.Second):
		t.Fatal("Stop 后订阅者未被关闭")
	}
}

func TestSanitize(t *testing.T) {
	if got := sanitize("192.168.1.5:5555"); got != "192_168_1_5_5555" {
		t.Errorf("sanitize 结果 = %q", got)
	}
}
