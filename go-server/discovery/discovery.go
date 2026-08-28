package discovery

import (
	"encoding/json"
	"net"
	"os"
	"strings"
	"sync"
	"time"
)

const DiscoveryPort = 9000

var magic = []byte("DISCOVER")

type Server struct {
	controlPort int
	hostname    string
	password    bool
	conn        *net.UDPConn
	running     bool
	// 速率限制：每个 IP 每秒最多响应 5 次发现请求，防止 DoS
	rateLimit   map[string][]time.Time
	rateMu      sync.Mutex
}

func NewServer(controlPort int, hostname string, passwordRequired bool) *Server {
	if hostname == "" {
		hostname, _ = os.Hostname()
	}
	return &Server{
		controlPort: controlPort,
		hostname:    hostname,
		password:    passwordRequired,
		rateLimit:   make(map[string][]time.Time),
	}
}

func (s *Server) Start() error {
	addr, err := net.ResolveUDPAddr("udp", ":9000")
	if err != nil {
		return err
	}
	conn, err := net.ListenUDP("udp", addr)
	if err != nil {
		return err
	}
	s.conn = conn
	s.running = true
	go s.listen()
	return nil
}

func (s *Server) listen() {
	buf := make([]byte, 1024)
	for s.running {
		n, remoteAddr, err := s.conn.ReadFromUDP(buf)
		if err != nil {
			if !s.running {
				return
			}
			continue
		}
		if n >= len(magic) && strings.HasPrefix(string(buf[:n]), string(magic)) {
			// 安全检查：速率限制，防止 DoS
			if s.checkRateLimit(remoteAddr.IP.String()) {
				s.reply(remoteAddr)
			}
		}
	}
}

// checkRateLimit 检查 IP 是否在速率限制内，返回 true 表示允许响应
func (s *Server) checkRateLimit(ip string) bool {
	s.rateMu.Lock()
	defer s.rateMu.Unlock()

	now := time.Now()
	// 清理过期记录（超过 1 秒的）
	if times, ok := s.rateLimit[ip]; ok {
		valid := []time.Time{}
		for _, t := range times {
			if now.Sub(t) < time.Second {
				valid = append(valid, t)
			}
		}
		s.rateLimit[ip] = valid
	}

	times := s.rateLimit[ip]
	if len(times) >= 5 {
		return false
	}
	s.rateLimit[ip] = append(times, now)
	return true
}

func (s *Server) reply(addr *net.UDPAddr) {
	localIP := getLocalIP()
	resp := map[string]interface{}{
		"type":              "discovery_response",
		"hostname":          s.hostname,
		"ip":                localIP,
		"port":              s.controlPort,
		"os":                "Windows",
		"version":           "1.3.0",
		"password_required": s.password,
	}
	data, _ := json.Marshal(resp)
	s.conn.WriteToUDP(data, addr)
}

func getLocalIP() string {
	addrs, err := net.InterfaceAddrs()
	if err != nil {
		return "0.0.0.0"
	}
	for _, addr := range addrs {
		if ipnet, ok := addr.(*net.IPNet); ok && !ipnet.IP.IsLoopback() {
			if ipnet.IP.To4() != nil {
				return ipnet.IP.String()
			}
		}
	}
	return "0.0.0.0"
}

func (s *Server) Stop() {
	s.running = false
	if s.conn != nil {
		s.conn.Close()
	}
}
