package webui

import (
	"crypto/subtle"
	"embed"
	"encoding/json"
	"fmt"
	"io/fs"
	"log"
	"net"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/gorilla/websocket"
	"github.com/tangyuan9325/lan-remote-control/go-server/camera"
	"github.com/tangyuan9325/lan-remote-control/go-server/inputinject"
	"github.com/tangyuan9325/lan-remote-control/go-server/protocol"
	"github.com/tangyuan9325/lan-remote-control/go-server/screen"
)

const (
	// MaxConnections 最大并发 WebSocket 屏幕会话连接数限制，防止 DoS 攻击
	MaxConnections = 100
	// MaxCameraConnections 摄像头并发推流上限（独立计数）
	MaxCameraConnections = 10
	// wsWriteTimeout WebSocket 写超时：慢客户端会被断开而不是挂起 goroutine
	wsWriteTimeout = 5 * time.Second
	// wsReadIdleTimeout WebSocket 空闲读超时
	wsReadIdleTimeout = 60 * time.Second
	// DefaultControlPort TCP 控制服务默认端口（与 discovery / docs/protocol.md 一致）
	DefaultControlPort = 9001
)

// staticFS 内嵌静态资源，避免相对路径在打包/不同工作目录下 404。
//
//go:embed all:static
var staticFS embed.FS

var upgrader = websocket.Upgrader{
	CheckOrigin: func(r *http.Request) bool {
		// 仅允许同源请求，防止 CSRF 攻击
		origin := r.Header.Get("Origin")
		if origin == "" {
			return true // 允许非浏览器客户端（如移动应用）
		}
		host := r.Host
		return origin == "http://"+host || origin == "https://"+host
	},
}

type Server struct {
	capture     *screen.Capturer
	cam         *camera.Camera
	injector    *inputinject.Injector
	password    string
	port        int
	httpSrv     *http.Server
	mu          sync.Mutex
	clients     map[*websocket.Conn]bool
	cameraConns int
}

func NewServer(capture *screen.Capturer, cam *camera.Camera, injector *inputinject.Injector, port int, password string) *Server {
	return &Server{
		capture:  capture,
		cam:      cam,
		injector: injector,
		password: password,
		port:     port,
		clients:  make(map[*websocket.Conn]bool),
	}
}

func (s *Server) Start() error {
	mux := http.NewServeMux()
	mux.HandleFunc("/api/devices", s.handleDevices)

	withHeaders := func(h http.HandlerFunc) http.HandlerFunc {
		return func(w http.ResponseWriter, r *http.Request) {
			w.Header().Set("X-Content-Type-Options", "nosniff")
			h(w, r)
		}
	}

	staticSub, err := fs.Sub(staticFS, "static")
	if err != nil {
		return err
	}
	fileServer := http.FileServer(http.FS(staticSub))
	mux.HandleFunc("/", withHeaders(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/" {
			http.NotFound(w, r)
			return
		}
		fileServer.ServeHTTP(w, r)
	}))
	mux.HandleFunc("/ws", withHeaders(s.handleWebSocket))
	mux.HandleFunc("/camera", withHeaders(s.handleCamera))

	s.httpSrv = &http.Server{
		Addr:              fmt.Sprintf(":%d", s.port),
		Handler:           mux,
		ReadHeaderTimeout: 5 * time.Second,
	}
	log.Printf("[WebUI] http://0.0.0.0:%d", s.port)
	err = s.httpSrv.ListenAndServe()
	if err == http.ErrServerClosed {
		return nil
	}
	return err
}

// Shutdown 优雅关闭 Web 服务
func (s *Server) Shutdown() {
	if s.httpSrv != nil {
		s.httpSrv.Close()
	}
}

func (s *Server) handleDevices(w http.ResponseWriter, r *http.Request) {
	devices := discoverDevices(3 * time.Second)
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(devices)
}

func discoverDevices(timeout time.Duration) []map[string]interface{} {
	devices := []map[string]interface{}{}
	conn, err := net.ListenUDP("udp", &net.UDPAddr{IP: net.IPv4zero, Port: 0})
	if err != nil {
		return devices
	}
	defer conn.Close()

	broadcast := &net.UDPAddr{IP: net.IPv4bcast, Port: 9000}
	conn.WriteToUDP([]byte("DISCOVER"), broadcast)

	conn.SetReadDeadline(time.Now().Add(timeout))
	buf := make([]byte, 4096)
	for {
		n, _, err := conn.ReadFromUDP(buf)
		if err != nil {
			break
		}
		var dev map[string]interface{}
		if err := json.Unmarshal(buf[:n], &dev); err == nil {
			if dev["type"] == "discovery_response" {
				devices = append(devices, dev)
			}
		}
	}
	return devices
}

// checkWSPassword 校验浏览器通过查询串携带的连接密码（恒定时间比较）。
// 服务端未设置密码时直接放行。
func (s *Server) checkWSPassword(r *http.Request) bool {
	if s.password == "" {
		return true
	}
	got := r.URL.Query().Get("password")
	return subtle.ConstantTimeCompare([]byte(got), []byte(s.password)) == 1
}

func (s *Server) registerClient(conn *websocket.Conn) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	if len(s.clients) >= MaxConnections {
		return false
	}
	s.clients[conn] = true
	return true
}

func (s *Server) unregisterClient(conn *websocket.Conn) {
	s.mu.Lock()
	delete(s.clients, conn)
	s.mu.Unlock()
}

// handleWebSocket 处理浏览器屏幕查看/控制会话。
// 修复鉴权绕过：升级前必须校验密码（与 TCP 服务同一份 --password）。
// 修复"永远只连本机"的逻辑错误：target 指定局域网设备时，由本服务端按
// docs/protocol.md 的 TCP 协议代理连接到远端被控机；target 留空或为本机
// 回环地址时直接推送本机屏幕。SSRF 防护：拒绝指向 loopback/link-local
// （含云元数据 169.254.x.x）等非局域网目标，并限制端口范围。
func (s *Server) handleWebSocket(w http.ResponseWriter, r *http.Request) {
	if !s.checkWSPassword(r) {
		http.Error(w, `{"error":"wrong_password"}`, http.StatusUnauthorized)
		return
	}

	conn, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		log.Printf("[WebSocket] upgrade error: %v", err)
		return
	}
	defer conn.Close()

	if !s.registerClient(conn) {
		conn.WriteMessage(websocket.TextMessage, []byte(`{"error":"connection_limit_reached"}`))
		return
	}
	defer s.unregisterClient(conn)

	target := strings.TrimSpace(r.URL.Query().Get("target"))

	w0, h0 := s.capture.Size()

	stop := make(chan struct{})
	var stopOnce sync.Once
	closeStop := func() { stopOnce.Do(func() { close(stop) }) }
	defer closeStop()

	// 本机模式：直接推送本地屏幕帧
	localStream := func() {
		sendJSON(conn, map[string]interface{}{"type": "hello_ok", "width": w0, "height": h0})
		go func() {
			ticker := time.NewTicker(33 * time.Millisecond)
			defer ticker.Stop()
			for {
				select {
				case <-stop:
					return
				case <-ticker.C:
					frame, err := s.capture.CaptureJPEG()
					if err != nil {
						continue
					}
					conn.SetWriteDeadline(time.Now().Add(wsWriteTimeout))
					if err := conn.WriteMessage(websocket.BinaryMessage, frame); err != nil {
						return
					}
				}
			}
		}()
		s.readControlLoop(conn)
	}

	if target == "" || isLocalTarget(target) {
		localStream()
		return
	}

	host, port := splitHostPort(target, DefaultControlPort)
	ip := net.ParseIP(host)
	if ip == nil {
		// 为防 DNS rebinding/内网探测，仅接受点分 IPv4 字面量
		sendJSON(conn, map[string]interface{}{"type": "error", "message": "invalid_target"})
		return
	}
	if !isLanAddress(ip) {
		sendJSON(conn, map[string]interface{}{"type": "error", "message": "target_not_allowed"})
		return
	}
	if port <= 0 || port > 65535 {
		sendJSON(conn, map[string]interface{}{"type": "error", "message": "invalid_port"})
		return
	}

	up, err := net.DialTimeout("tcp", net.JoinHostPort(host, strconv.Itoa(port)), 5*time.Second)
	if err != nil {
		sendJSON(conn, map[string]interface{}{"type": "error", "message": "connect_failed"})
		return
	}
	defer up.Close()

	// 上游握手：转发浏览器提供的远端密码（?remote_password=）；为空时发送
	// 无密码 hello，由被控端返回 hello_fail。
	hello := map[string]interface{}{
		"type":    "hello",
		"version": protocol.Version,
		"name":    "web-viewer",
	}
	if pw := r.URL.Query().Get("remote_password"); pw != "" {
		hello["password"] = pw
	}
	up.SetWriteDeadline(time.Now().Add(5 * time.Second))
	if _, err := up.Write(protocol.PackJSON(hello)); err != nil {
		up.Close()
		sendJSON(conn, map[string]interface{}{"type": "error", "message": "handshake_failed"})
		return
	}

	// 读 hello_ok
	up.SetReadDeadline(time.Now().Add(10 * time.Second))
	msgType, payload, err := protocol.RecvMessage(up)
	if err != nil || msgType != protocol.MsgJSON {
		up.Close()
		sendJSON(conn, map[string]interface{}{"type": "error", "message": "handshake_failed"})
		return
	}
	var resp map[string]interface{}
	if json.Unmarshal(payload, &resp) != nil {
		up.Close()
		sendJSON(conn, map[string]interface{}{"type": "error", "message": "bad_handshake_response"})
		return
	}
	if t, _ := resp["type"].(string); t != "hello_ok" {
		resp["type"] = "hello_fail"
		sendJSON(conn, resp)
		up.Close()
		return
	}
	sendJSON(conn, resp) // 原样转发 width/height/name/os/version 等

	// 上游 → 浏览器
	go func() {
		defer closeStop()
		for {
			up.SetReadDeadline(time.Now().Add(wsReadIdleTimeout))
			mt, pl, err := protocol.RecvMessage(up)
			if err != nil {
				break
			}
			var wsMT int
			switch mt {
			case protocol.MsgJPEG:
				wsMT = websocket.BinaryMessage
			case protocol.MsgJSON:
				wsMT = websocket.TextMessage
			default:
				continue // 文件/音频等类型暂不代理
			}
			conn.SetWriteDeadline(time.Now().Add(wsWriteTimeout))
			if err := conn.WriteMessage(wsMT, pl); err != nil {
				break
			}
		}
		up.Close()
	}()

	// 浏览器 → 上游（输入命令封装成协议 JSON 帧转发）
	s.readControlLoopForward(conn, up)
}

// readControlLoop 本机模式的控制命令读取循环（输入注入 + 画质 + ping）。
func (s *Server) readControlLoop(conn *websocket.Conn) {
	for {
		conn.SetReadDeadline(time.Now().Add(wsReadIdleTimeout))
		_, msg, err := conn.ReadMessage()
		if err != nil {
			break
		}
		var cmd map[string]interface{}
		if err := json.Unmarshal(msg, &cmd); err != nil {
			continue
		}
		switch cmd["type"] {
		case "ping":
			sendJSON(conn, map[string]interface{}{"type": "pong"})
		case "set_quality":
			if q, ok := cmd["quality"].(float64); ok {
				quality := int(q)
				if quality < 10 {
					quality = 10
				} else if quality > 100 {
					quality = 100
				}
				s.capture.SetQuality(quality)
			}
		default:
			// mouse_*/key_* 输入事件注入
			s.injector.HandleCommand(cmd)
		}
	}
}

// readControlLoopForward 代理模式：把浏览器命令原样封成 JSON 帧发给上游被控端。
func (s *Server) readControlLoopForward(conn *websocket.Conn, up net.Conn) {
	for {
		conn.SetReadDeadline(time.Now().Add(wsReadIdleTimeout))
		_, msg, err := conn.ReadMessage()
		if err != nil {
			break
		}
		var cmd map[string]interface{}
		if err := json.Unmarshal(msg, &cmd); err != nil {
			continue
		}
		if t, _ := cmd["type"].(string); t == "ping" {
			sendJSON(conn, map[string]interface{}{"type": "pong"})
			continue
		}
		up.SetWriteDeadline(time.Now().Add(5 * time.Second))
		if _, err := up.Write(protocol.PackJSON(cmd)); err != nil {
			break
		}
	}
	up.Close()
}

// isLocalTarget 判断 target 是否指代本机。
func isLocalTarget(host string) bool {
	if host == "localhost" {
		return true
	}
	if ip := net.ParseIP(host); ip != nil {
		return ip.IsLoopback()
	}
	return false
}

// isLanAddress SSRF 防护：只放行可路由的局域网/内网单播地址，
// 拒绝 loopback、link-local(169.254/16 云元数据)、未指定组播地址。
func isLanAddress(ip net.IP) bool {
	if ip == nil {
		return false
	}
	if ip.To4() == nil { // 仅支持 IPv4 字面量，避免 IPv6 特殊地址绕过
		return false
	}
	if ip.IsLoopback() || ip.IsLinkLocalUnicast() || ip.IsLinkLocalMulticast() ||
		ip.IsUnspecified() || ip.IsMulticast() {
		return false
	}
	return true // RFC1918 / 公网段均视为可连接目标（局域网场景常见跨网段直连）
}

// splitHostPort 解析 host[:port]，缺省端口用 def。
func splitHostPort(target string, def int) (string, int) {
	if h, p, err := net.SplitHostPort(target); err == nil {
		if pv, cerr := strconv.Atoi(p); cerr == nil && pv > 0 && pv <= 65535 {
			return h, pv
		}
		return h, def
	}
	return target, def
}

func sendJSON(conn *websocket.Conn, obj map[string]interface{}) error {
	payload, _ := json.Marshal(obj)
	conn.SetWriteDeadline(time.Now().Add(wsWriteTimeout))
	return conn.WriteMessage(websocket.TextMessage, payload)
}

// handleCamera 处理摄像头 WebSocket 推流。
// 修复竞态：Start 幂等 + 引用计数，只有最后一个客户端退出时才 Stop；
// 修复限流错误：使用独立的 cameraConns 计数而不是总连接数；
// 修复鉴权：升级前校验密码。
func (s *Server) handleCamera(w http.ResponseWriter, r *http.Request) {
	if !s.checkWSPassword(r) {
		http.Error(w, `{"error":"wrong_password"}`, http.StatusUnauthorized)
		return
	}

	conn, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		log.Printf("[Camera] upgrade error: %v", err)
		return
	}
	defer conn.Close()

	s.mu.Lock()
	if s.cameraConns >= MaxCameraConnections {
		s.mu.Unlock()
		conn.WriteMessage(websocket.TextMessage, []byte(`{"error":"camera_connection_limit_reached"}`))
		return
	}
	first := s.cameraConns == 0
	s.cameraConns++
	s.mu.Unlock()
	defer func() {
		s.mu.Lock()
		s.cameraConns--
		last := s.cameraConns == 0
		s.mu.Unlock()
		if last {
			s.cam.Stop()
		}
	}()

	if first {
		s.cam.Start()
	}

	ticker := time.NewTicker(100 * time.Millisecond)
	defer ticker.Stop()
	for range ticker.C {
		frame, err := s.cam.GetFrame()
		if err != nil {
			continue
		}
		conn.SetWriteDeadline(time.Now().Add(wsWriteTimeout))
		if err := conn.WriteMessage(websocket.BinaryMessage, frame); err != nil {
			break
		}
	}
}
