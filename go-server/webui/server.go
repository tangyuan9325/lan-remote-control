package webui

import (
	"encoding/json"
	"fmt"
	"log"
	"net"
	"net/http"
	"sync"
	"time"

	"github.com/gorilla/websocket"
	"github.com/tangyuan9325/lan-remote-control/go-server/camera"
	"github.com/tangyuan9325/lan-remote-control/go-server/screen"
)

const (
	// MaxConnections 最大并发连接数限制，防止 DoS 攻击
	MaxConnections = 100
)

var upgrader = websocket.Upgrader{
	CheckOrigin: func(r *http.Request) bool {
		// 仅允许同源请求，防止 CSRF 攻击
		origin := r.Header.Get("Origin")
		if origin == "" {
			return true // 允许非浏览器客户端（如移动应用）
		}
		host := r.Host
		// 简单的同源检查：比较 Origin 的 host 部分和请求的 Host
		// 在生产环境中应该使用更严格的白名单机制
		return origin == "http://"+host || origin == "https://"+host
	},
}

type Server struct {
	capture *screen.Capturer
	cam     *camera.Camera
	port    int
	mu      sync.Mutex
	clients map[*websocket.Conn]bool
}

func NewServer(capture *screen.Capturer, cam *camera.Camera, port int) *Server {
	return &Server{
		capture: capture,
		cam:     cam,
		port:    port,
		clients: make(map[*websocket.Conn]bool),
	}
}

func (s *Server) Start() error {
	mux := http.NewServeMux()
	mux.HandleFunc("/", s.handleIndex)
	mux.HandleFunc("/api/devices", s.handleDevices)
	mux.HandleFunc("/ws", s.handleWebSocket)
	mux.HandleFunc("/camera", s.handleCamera)

	addr := fmt.Sprintf(":%d", s.port)
	log.Printf("[WebUI] http://0.0.0.0:%d", s.port)
	return http.ListenAndServe(addr, mux)
}

func (s *Server) handleIndex(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	http.ServeFile(w, r, "webui/static/index.html")
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

func (s *Server) handleWebSocket(w http.ResponseWriter, r *http.Request) {
	conn, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		log.Printf("[WebSocket] upgrade error: %v", err)
		return
	}
	defer conn.Close()

	s.mu.Lock()
	// 检查 WebSocket 连接数限制
	if len(s.clients) >= MaxConnections {
		s.mu.Unlock()
		conn.WriteMessage(websocket.TextMessage, []byte(`{"error":"connection_limit_reached"}`))
		conn.Close()
		return
	}
	s.clients[conn] = true
	s.mu.Unlock()
	defer func() {
		s.mu.Lock()
		delete(s.clients, conn)
		s.mu.Unlock()
	}()

	stop := make(chan struct{})
	go s.streamScreen(conn, stop)

	for {
		_, msg, err := conn.ReadMessage()
		if err != nil {
			close(stop)
			break
		}
		var cmd map[string]interface{}
		if err := json.Unmarshal(msg, &cmd); err != nil {
			continue
		}
		if cmd["type"] == "set_quality" {
			if q, ok := cmd["quality"].(float64); ok {
				// 输入验证：限制 quality 范围在 10-100
				quality := int(q)
				if quality < 10 {
					quality = 10
				} else if quality > 100 {
					quality = 100
				}
				s.capture.SetQuality(quality)
			}
		}
	}
}

func (s *Server) streamScreen(conn *websocket.Conn, stop <-chan struct{}) {
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
			conn.WriteMessage(websocket.BinaryMessage, frame)
		}
	}
}

func (s *Server) handleCamera(w http.ResponseWriter, r *http.Request) {
	conn, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		log.Printf("[Camera] upgrade error: %v", err)
		return
	}
	defer conn.Close()

	s.mu.Lock()
	// 简单的摄像头连接数限制（最多 10 个并发摄像头连接）
	cameraConnections := len(s.clients) // 保守估计：所有 WebSocket 连接都可能是摄像头
	if cameraConnections >= 10 {
		s.mu.Unlock()
		conn.WriteMessage(websocket.TextMessage, []byte(`{"error":"camera_connection_limit_reached"}`))
		conn.Close()
		return
	}
	s.mu.Unlock()

	if !s.cam.IsRunning() {
		s.cam.Start()
		defer s.cam.Stop()
	}

	ticker := time.NewTicker(100 * time.Millisecond)
	defer ticker.Stop()
	for range ticker.C {
		frame, err := s.cam.GetFrame()
		if err != nil {
			continue
		}
		if err := conn.WriteMessage(websocket.BinaryMessage, frame); err != nil {
			break
		}
	}
}
