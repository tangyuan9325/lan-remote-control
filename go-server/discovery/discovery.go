package discovery

import (
	"encoding/json"
	"net"
	"os"
	"strings"
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
}

func NewServer(controlPort int, hostname string, passwordRequired bool) *Server {
	if hostname == "" {
		hostname, _ = os.Hostname()
	}
	return &Server{
		controlPort: controlPort,
		hostname:    hostname,
		password:    passwordRequired,
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
			s.reply(remoteAddr)
		}
	}
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
