// Package server 提供 Windows 客户端的本地 Web 控制台：
//   - HTTP API：设备列表/连接/配对/设置/日志/截图
//   - WebSocket：每台设备的实时视频帧与指令通道
//   - 自动发现并管理多台 Android 设备
//
// 安全模型：仅监听 127.0.0.1，并对每个请求校验 Host 与 Origin，
// 防止恶意网页通过 DNS rebinding 或跨站 WebSocket 劫持接管手机。
package server

import (
	"context"
	"embed"
	"encoding/json"
	"fmt"
	"io/fs"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/gorilla/websocket"

	"remote-control/client/internal/adb"
	"remote-control/client/internal/ffmpeg"
)

//go:embed webui/*
var webuiFS embed.FS

// Version 客户端版本号（唯一来源；可用 -ldflags "-X ...server.Version=x.y.z" 注入）。
var Version = "1.1.0"

const (
	// maxBodyBytes JSON 请求体上限
	maxBodyBytes = 64 * 1024
	// wsReadLimit 单条 WebSocket 消息上限
	wsReadLimit = 64 * 1024
	// wsPongWait 读空闲超时
	wsPongWait = 60 * time.Second
	// wsPingPeriod 服务端心跳间隔
	wsPingPeriod = 20 * time.Second
	// wsWriteWait 单次写超时
	wsWriteWait = 10 * time.Second
)

// Server 是本地控制台服务。
type Server struct {
	cfg       Config
	cfgPath   string
	exeDir    string
	smoke     bool
	noBrowser bool

	toolMu     sync.RWMutex
	adb        *adb.Adb
	ffmpegPath string

	mu       sync.Mutex
	sessions map[string]*Session
	devices  []adb.Device

	logMu    sync.Mutex
	logLines []string

	upgrader websocket.Upgrader
	quit     chan struct{}
	httpSrv  *http.Server
}

// New 初始化服务器（adb/ffmpeg 探测）。
func New(cfg Config, smoke bool, noBrowser bool) (*Server, error) {
	s := &Server{
		cfg:       cfg,
		cfgPath:   ConfigPath(),
		sessions:  make(map[string]*Session),
		quit:      make(chan struct{}),
		smoke:     smoke,
		noBrowser: noBrowser,
		upgrader: websocket.Upgrader{
			ReadBufferSize:  4096,
			WriteBufferSize: 64 * 1024,
			// 只允许同源页面建立 WebSocket（阻断跨站 WebSocket 劫持）
			CheckOrigin: func(r *http.Request) bool {
				return originAllowed(r, fmt.Sprint(cfg.Port))
			},
		},
	}
	if exe, err := os.Executable(); err == nil {
		s.exeDir = filepath.Dir(exe)
	} else {
		s.exeDir, _ = os.Getwd()
	}

	// adb
	if cfg.AdbPath != "" {
		s.adb = &adb.Adb{Path: cfg.AdbPath}
	} else if p, err := adb.Find(); err == nil {
		s.adb = &adb.Adb{Path: p}
		s.cfg.AdbPath = p
	} else {
		s.logf("警告: %v", err)
	}
	if s.adb != nil {
		if err := s.adb.StartServer(); err != nil {
			s.logf("adb 服务启动失败: %v", err)
		} else {
			s.logf("adb: %s", s.adb.Version())
		}
	}
	// ffmpeg
	if cfg.FFmpegPath != "" {
		if _, err := os.Stat(cfg.FFmpegPath); err == nil {
			s.ffmpegPath = cfg.FFmpegPath
		} else {
			s.logf("配置的 ffmpeg 不存在: %s", cfg.FFmpegPath)
		}
	}
	if s.ffmpegPath == "" {
		if p, ok := ffmpeg.Find(); ok {
			s.ffmpegPath = p
		}
	}
	if s.ffmpegPath != "" {
		s.logf("ffmpeg: %s", s.ffmpegPath)
	} else {
		s.logf("未找到 ffmpeg，视频将使用 PNG 截图模式")
	}
	_ = s.cfg.Save()
	return s, nil
}

func (s *Server) getAdb() *adb.Adb {
	s.toolMu.RLock()
	defer s.toolMu.RUnlock()
	return s.adb
}

func (s *Server) getFFmpeg() string {
	s.toolMu.RLock()
	defer s.toolMu.RUnlock()
	return s.ffmpegPath
}

// URL 返回控制台地址。
func (s *Server) URL() string {
	return fmt.Sprintf("http://127.0.0.1:%d", s.cfg.Port)
}

// OpenBrowser 在默认浏览器打开控制台。
func (s *Server) OpenBrowser() {
	_ = exec.Command("rundll32", "url.dll,FileProtocolHandler", s.URL()).Start()
}

// Run 启动 HTTP 服务与设备轮询（阻塞直到 Shutdown）。
func (s *Server) Run() error {
	mux := http.NewServeMux()
	s.routes(mux)

	s.quit = make(chan struct{})
	go s.pollLoop()

	addr := fmt.Sprintf("127.0.0.1:%d", s.cfg.Port)
	ln, err := net.Listen("tcp", addr)
	if err != nil {
		return fmt.Errorf("无法监听 %s: %w（端口可能被占用，可用 -port 更换）", addr, err)
	}
	s.httpSrv = &http.Server{
		Handler:           s.guard(mux),
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       30 * time.Second,
		// 写超时不能设：WebSocket 是长连接
		WriteTimeout: 0,
		IdleTimeout:  60 * time.Second,
	}
	s.logf("控制台已启动: %s", s.URL())
	if !s.smoke && !s.noBrowser {
		s.OpenBrowser()
	}
	if err := s.httpSrv.Serve(ln); err != nil && err != http.ErrServerClosed {
		return err
	}
	return nil
}

// Shutdown 停止服务并回收所有子进程（ffmpeg / screenrecord）。
func (s *Server) Shutdown() {
	select {
	case <-s.quit:
	default:
		close(s.quit)
	}
	if s.httpSrv != nil {
		ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		_ = s.httpSrv.Shutdown(ctx)
		cancel()
	}
	s.mu.Lock()
	sessions := make([]*Session, 0, len(s.sessions))
	for _, sess := range s.sessions {
		sessions = append(sessions, sess)
	}
	s.sessions = make(map[string]*Session)
	s.mu.Unlock()
	for _, sess := range sessions {
		sess.Stop()
	}
	s.logf("服务已退出，子进程已回收")
}

// ---- 安全校验 ----

// guard 校验 Host（防 DNS rebinding），并对写操作用校验 Origin（防跨站请求）。
func (s *Server) guard(next http.Handler) http.Handler {
	wantPort := fmt.Sprint(s.cfg.Port)
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !hostAllowed(r.Host, wantPort) {
			http.Error(w, "forbidden host", http.StatusForbidden)
			return
		}
		if r.Method != http.MethodGet && r.Method != http.MethodHead {
			if !originAllowed(r, wantPort) {
				http.Error(w, "forbidden origin", http.StatusForbidden)
				return
			}
		}
		next.ServeHTTP(w, r)
	})
}

// hostAllowed 判断 Host 是否为本地回环地址且端口匹配。
// 攻击者把域名解析到 127.0.0.1（DNS rebinding）时，Host 仍是攻击者的域名，会被拒绝。
func hostAllowed(hostport, wantPort string) bool {
	host, p, err := net.SplitHostPort(hostport)
	if err != nil {
		host, p = hostport, ""
	}
	if wantPort != "" && p != "" && p != wantPort {
		return false
	}
	return isLoopbackName(host)
}

// originAllowed 校验 Origin：浏览器跨站发起时 Origin 是攻击者站点，
// 而 WebSocket 不受同源策略保护，必须在服务端阻断（CSWSH）。
func originAllowed(r *http.Request, wantPort string) bool {
	origin := r.Header.Get("Origin")
	if origin == "" {
		return true // 非浏览器客户端（curl / 本机脚本），Host 校验仍然生效
	}
	if origin == "null" {
		return false
	}
	u, err := url.Parse(origin)
	if err != nil {
		return false
	}
	return hostAllowed(u.Host, wantPort)
}

func isLoopbackName(host string) bool {
	switch strings.ToLower(host) {
	case "127.0.0.1", "localhost", "::1", "[::1]":
		return true
	}
	return false
}

func (s *Server) routes(mux *http.ServeMux) {
	sub, _ := fs.Sub(webuiFS, "webui")
	mux.Handle("/", http.FileServer(http.FS(sub)))

	mux.HandleFunc("/api/status", s.handleStatus)
	mux.HandleFunc("/api/devices", s.handleDevices)
	mux.HandleFunc("/api/logs", s.handleLogs)
	mux.HandleFunc("/api/connect", s.handleConnect)
	mux.HandleFunc("/api/pair", s.handlePair)
	mux.HandleFunc("/api/disconnect", s.handleDisconnect)
	mux.HandleFunc("/api/settings", s.handleSettings)
	mux.HandleFunc("/api/device/", s.handleDevice)
	mux.HandleFunc("/ws/device/", s.handleWS)
}

// ---- 设备轮询 ----

func (s *Server) pollLoop() {
	tick := time.NewTicker(3 * time.Second)
	defer tick.Stop()
	for {
		s.refreshDevices()
		select {
		case <-tick.C:
		case <-s.quit:
			return
		}
	}
}

func (s *Server) refreshDevices() {
	a := s.getAdb()
	if a == nil {
		return
	}
	devs, err := a.Devices()
	if err != nil {
		s.logf("刷新设备失败: %v", err)
		return
	}
	online := make(map[string]adb.Device, len(devs))
	for _, d := range devs {
		if d.State == "device" {
			online[d.Serial] = d
		}
	}

	s.mu.Lock()
	existing := make(map[string]*Session, len(s.sessions))
	for k, v := range s.sessions {
		existing[k] = v
	}
	s.devices = devs
	s.mu.Unlock()

	// 新设备：Info() 会跑 adb，必须在锁外执行，否则整个 API 被阻塞 1~2 秒
	for serial, d := range online {
		if _, ok := existing[serial]; ok {
			continue
		}
		start := time.Now()
		info := a.Info(serial)
		sess := NewSession(a, s.getFFmpeg(), serial, d.DisplayName(), info)
		sess.logFn = s.logf
		sess.SetVideoMode(resolveVideoMode(s.cfg.VideoMode, s.getFFmpeg()))
		sess.SetInputMode(resolveInputMode(s.cfg.InputMode))
		s.mu.Lock()
		s.sessions[serial] = sess
		s.mu.Unlock()
		sess.Start()
		s.logf("已连接 %s (%s)，信息采集耗时 %dms", serial, d.DisplayName(), time.Since(start).Milliseconds())
	}
	// 掉线设备
	for serial, sess := range existing {
		if _, ok := online[serial]; ok {
			continue
		}
		s.mu.Lock()
		delete(s.sessions, serial)
		s.mu.Unlock()
		sess.Stop()
		s.logf("设备断开: %s", serial)
	}
}

func resolveVideoMode(mode, ffmpegPath string) string {
	switch mode {
	case "png":
		return "png"
	case "agent":
		if ffmpegPath == "" {
			return "png"
		}
		return "agent"
	case "record":
		if ffmpegPath == "" {
			return "png"
		}
		return "record"
	default:
		if ffmpegPath != "" {
			return "record"
		}
		return "png"
	}
}

func resolveInputMode(mode string) string {
	if mode == "agent" {
		return "agent"
	}
	return "shell"
}

func (s *Server) getSession(serial string) *Session {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.sessions[serial]
}

// ---- HTTP 处理 ----

func writeJSON(w http.ResponseWriter, v interface{}) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	_ = json.NewEncoder(w).Encode(v)
}

// decodeBody 限制请求体大小后再解析 JSON。
func decodeBody(w http.ResponseWriter, r *http.Request, v interface{}) error {
	r.Body = http.MaxBytesReader(w, r.Body, maxBodyBytes)
	dec := json.NewDecoder(r.Body)
	dec.DisallowUnknownFields()
	return dec.Decode(v)
}

func (s *Server) handleStatus(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, map[string]interface{}{
		"version": Version,
		"adb":     s.getAdb() != nil,
		"ffmpeg":  s.getFFmpeg() != "",
		"url":     s.URL(),
		"time":    time.Now().Unix(),
	})
}

func (s *Server) handleDevices(w http.ResponseWriter, r *http.Request) {
	s.mu.Lock()
	devs := s.devices
	s.mu.Unlock()

	items := make([]map[string]interface{}, 0, len(devs))
	for _, d := range devs {
		item := map[string]interface{}{
			"serial": d.Serial,
			"state":  d.State,
			"model":  d.DisplayName(),
			"usb":    d.IsUsb(),
			"ip":     d.IPPort,
		}
		if sess := s.getSession(d.Serial); sess != nil {
			item["videoMode"] = sess.VideoMode()
			item["inputMode"] = sess.InputMode()
		}
		items = append(items, item)
	}
	writeJSON(w, map[string]interface{}{"devices": items})
}

func (s *Server) handleLogs(w http.ResponseWriter, r *http.Request) {
	s.logMu.Lock()
	lines := append([]string(nil), s.logLines...)
	s.logMu.Unlock()
	writeJSON(w, map[string]interface{}{"logs": lines})
}

func (s *Server) handleConnect(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Host string `json:"host"`
	}
	if err := decodeBody(w, r, &req); err != nil {
		writeJSON(w, map[string]interface{}{"ok": false, "error": err.Error()})
		return
	}
	a := s.getAdb()
	if a == nil {
		writeJSON(w, map[string]interface{}{"ok": false, "error": "adb 未就绪"})
		return
	}
	host := strings.TrimSpace(req.Host)
	if !validHostPort(host) {
		writeJSON(w, map[string]interface{}{"ok": false, "error": "地址格式应为 IP:端口，如 192.168.1.100:5555"})
		return
	}
	go func() {
		out, err := a.Connect(host)
		s.logf("adb connect %s → %s (%v)", host, out, err)
	}()
	writeJSON(w, map[string]interface{}{"ok": true})
}

func (s *Server) handlePair(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Host string `json:"host"`
		Code string `json:"code"`
	}
	if err := decodeBody(w, r, &req); err != nil {
		writeJSON(w, map[string]interface{}{"ok": false, "error": err.Error()})
		return
	}
	a := s.getAdb()
	if a == nil {
		writeJSON(w, map[string]interface{}{"ok": false, "error": "adb 未就绪"})
		return
	}
	host := strings.TrimSpace(req.Host)
	code := strings.TrimSpace(req.Code)
	if !validHostPort(host) {
		writeJSON(w, map[string]interface{}{"ok": false, "error": "配对地址格式不对"})
		return
	}
	if len(code) == 0 || len(code) > 12 || !digitsOnly(code) {
		writeJSON(w, map[string]interface{}{"ok": false, "error": "配对码应为 6 位数字"})
		return
	}
	go func() {
		out, err := a.Pair(host, code)
		s.logf("adb pair %s → %s (%v)", host, out, err)
	}()
	writeJSON(w, map[string]interface{}{"ok": true})
}

func (s *Server) handleDisconnect(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Host string `json:"host"`
	}
	if err := decodeBody(w, r, &req); err != nil {
		writeJSON(w, map[string]interface{}{"ok": false, "error": err.Error()})
		return
	}
	a := s.getAdb()
	if a == nil {
		writeJSON(w, map[string]interface{}{"ok": false, "error": "adb 未就绪"})
		return
	}
	host := strings.TrimSpace(req.Host)

	s.mu.Lock()
	devs := append([]adb.Device(nil), s.devices...)
	s.mu.Unlock()

	go func() {
		if host == "" || host == "*" {
			for _, d := range devs {
				if d.IPPort != "" {
					out, err := a.Disconnect(d.IPPort)
					s.logf("adb disconnect %s → %s (%v)", d.IPPort, out, err)
				}
			}
			return
		}
		if !validHostPort(host) {
			s.logf("忽略非法断开地址: %q", host)
			return
		}
		out, err := a.Disconnect(host)
		s.logf("adb disconnect %s → %s (%v)", host, out, err)
	}()
	writeJSON(w, map[string]interface{}{"ok": true})
}

func (s *Server) handleSettings(w http.ResponseWriter, r *http.Request) {
	var req struct {
		AdbPath    string `json:"adb_path"`
		FFmpegPath string `json:"ffmpeg_path"`
	}
	if err := decodeBody(w, r, &req); err != nil {
		writeJSON(w, map[string]interface{}{"ok": false, "error": err.Error()})
		return
	}
	adbPath := strings.TrimSpace(req.AdbPath)
	ffPath := strings.TrimSpace(req.FFmpegPath)

	s.toolMu.Lock()
	if adbPath != "" {
		if _, err := os.Stat(adbPath); err != nil {
			s.toolMu.Unlock()
			writeJSON(w, map[string]interface{}{"ok": false, "error": "adb 路径不存在"})
			return
		}
		s.adb = &adb.Adb{Path: adbPath}
		s.cfg.AdbPath = adbPath
	}
	if ffPath != "" {
		if _, err := os.Stat(ffPath); err != nil {
			s.toolMu.Unlock()
			writeJSON(w, map[string]interface{}{"ok": false, "error": "ffmpeg 路径不存在"})
			return
		}
		s.ffmpegPath = ffPath
		s.cfg.FFmpegPath = ffPath
	}
	newAdb := s.adb
	ff := s.ffmpegPath
	s.toolMu.Unlock()

	if adbPath != "" && newAdb != nil {
		if err := newAdb.StartServer(); err != nil {
			s.logf("重新加载 adb 失败: %v", err)
		} else {
			s.logf("adb 已更新: %s", newAdb.Version())
		}
	}
	if ffPath != "" {
		s.logf("ffmpeg 已更新: %s", ff)
	}
	if err := s.cfg.Save(); err != nil {
		s.logf("保存配置失败: %v", err)
	}
	writeJSON(w, map[string]interface{}{"ok": true})
}

func (s *Server) handleDevice(w http.ResponseWriter, r *http.Request) {
	rest := strings.TrimPrefix(r.URL.Path, "/api/device/")
	parts := strings.SplitN(rest, "/", 2)
	if len(parts) != 2 {
		http.Error(w, "bad path", http.StatusBadRequest)
		return
	}
	serial, action := parts[0], parts[1]
	sess := s.getSession(serial)
	if sess == nil {
		http.Error(w, "device not connected", http.StatusNotFound)
		return
	}
	switch action {
	case "info":
		writeJSON(w, map[string]interface{}{
			"serial":    serial,
			"model":     sess.Model(),
			"info":      sess.Info(),
			"videoMode": sess.VideoMode(),
			"inputMode": sess.InputMode(),
			"startedAt": sess.StartedAt().Unix(),
			"viewers":   sess.SubscriberCount(),
		})
	case "screenshot":
		data, err := sess.ScreenshotPNG()
		if err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		w.Header().Set("Content-Type", "image/png")
		w.Header().Set("Content-Disposition", fmt.Sprintf(`attachment; filename="%s.png"`, sanitize(serial)))
		_, _ = w.Write(data)
	default:
		http.Error(w, "unknown action", http.StatusBadRequest)
	}
}

// ---- WebSocket ----

const (
	msgFrame = 0x01 // 二进制：JPEG 帧
	msgState = 0x02 // 文本：状态 JSON（保留前缀字节以兼容旧前端）
)

func (s *Server) handleWS(w http.ResponseWriter, r *http.Request) {
	serial := strings.TrimSuffix(strings.TrimPrefix(r.URL.Path, "/ws/device/"), "/")
	sess := s.getSession(serial)
	if sess == nil {
		http.Error(w, "device not connected", http.StatusNotFound)
		return
	}
	conn, err := s.upgrader.Upgrade(w, r, nil)
	if err != nil {
		s.logf("WebSocket 升级失败: %v", err)
		return
	}
	defer conn.Close()

	conn.SetReadLimit(wsReadLimit)
	_ = conn.SetReadDeadline(time.Now().Add(wsPongWait))
	conn.SetPongHandler(func(string) error {
		return conn.SetReadDeadline(time.Now().Add(wsPongWait))
	})

	// 每个连接独立订阅：多开标签页时互不覆盖（原实现是单回调，会互相踢掉）
	sub := sess.Subscribe()
	defer sess.Unsubscribe(sub)

	readDone := make(chan struct{})
	go func() {
		defer close(readDone)
		for {
			_, data, err := conn.ReadMessage()
			if err != nil {
				return
			}
			s.handleWSCommand(sess, data)
		}
	}()

	statusTick := time.NewTicker(time.Second)
	defer statusTick.Stop()
	pingTick := time.NewTicker(wsPingPeriod)
	defer pingTick.Stop()

	for {
		select {
		case <-readDone:
			return
		case <-sub.closed:
			return
		case <-pingTick.C:
			_ = conn.SetWriteDeadline(time.Now().Add(wsWriteWait))
			if err := conn.WriteMessage(websocket.PingMessage, nil); err != nil {
				return
			}
		case frame := <-sub.Frames:
			_ = conn.SetWriteDeadline(time.Now().Add(wsWriteWait))
			// 用 NextWriter 分两段写，避免每帧都拼接出一个 copy
			wr, err := conn.NextWriter(websocket.BinaryMessage)
			if err != nil {
				return
			}
			_, _ = wr.Write([]byte{msgFrame})
			_, _ = wr.Write(frame)
			if err := wr.Close(); err != nil {
				return
			}
		case <-statusTick.C:
			st := sess.StatusMap()
			b, err := json.Marshal(st)
			if err != nil {
				continue
			}
			msg := make([]byte, 0, len(b)+1)
			msg = append(msg, msgState)
			msg = append(msg, b...)
			_ = conn.SetWriteDeadline(time.Now().Add(wsWriteWait))
			if err := conn.WriteMessage(websocket.TextMessage, msg); err != nil {
				return
			}
		}
	}
}

type wsCommand struct {
	Cmd  string `json:"cmd"`
	Type string `json:"type"`
	X    int    `json:"x,omitempty"`
	Y    int    `json:"y,omitempty"`
	X1   int    `json:"x1,omitempty"`
	Y1   int    `json:"y1,omitempty"`
	X2   int    `json:"x2,omitempty"`
	Y2   int    `json:"y2,omitempty"`
	Dur  int    `json:"duration,omitempty"`
	Key  string `json:"key,omitempty"`
	Text string `json:"text,omitempty"`
	Mode string `json:"mode,omitempty"`
}

func (s *Server) handleWSCommand(sess *Session, data []byte) {
	var cmd wsCommand
	if err := json.Unmarshal(data, &cmd); err != nil {
		return
	}
	switch cmd.Cmd {
	case "input":
		switch cmd.Type {
		case "tap":
			sess.Tap(cmd.X, cmd.Y)
		case "swipe":
			sess.Swipe(cmd.X1, cmd.Y1, cmd.X2, cmd.Y2, cmd.Dur)
		case "key":
			sess.Key(strings.ToUpper(strings.TrimSpace(cmd.Key)))
		case "text":
			sess.Text(cmd.Text)
		}
	case "wake":
		sess.WakeUp()
	case "video":
		mode := resolveVideoMode(cmd.Mode, s.getFFmpeg())
		sess.SetVideoMode(mode)
		s.logf("%s 视频模式 → %s", sess.Serial(), mode)
	case "inputmode":
		mode := resolveInputMode(cmd.Mode)
		sess.SetInputMode(mode)
		s.logf("%s 输入模式 → %s", sess.Serial(), mode)
	}
}

// validHostPort 校验 IP:端口（可含主机名），拒绝以 - 开头的参数注入。
func validHostPort(s string) bool {
	if s == "" || len(s) > 64 || strings.HasPrefix(s, "-") {
		return false
	}
	host, port, err := net.SplitHostPort(s)
	if err != nil || host == "" || port == "" {
		return false
	}
	return digitsOnly(port)
}

func digitsOnly(s string) bool {
	if s == "" {
		return false
	}
	for _, r := range s {
		if r < '0' || r > '9' {
			return false
		}
	}
	return true
}

// ---- 日志 ----

func (s *Server) logf(format string, args ...interface{}) {
	line := fmt.Sprintf("[%s] %s", time.Now().Format("15:04:05"), fmt.Sprintf(format, args...))
	s.logMu.Lock()
	s.logLines = append(s.logLines, line)
	if len(s.logLines) > 400 {
		s.logLines = s.logLines[len(s.logLines)-400:]
	}
	s.logMu.Unlock()
}
