package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"net"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/tangyuan9325/lan-remote-control/go-server/camera"
	"github.com/tangyuan9325/lan-remote-control/go-server/discovery"
	"github.com/tangyuan9325/lan-remote-control/go-server/protocol"
	"github.com/tangyuan9325/lan-remote-control/go-server/screen"
	"github.com/tangyuan9325/lan-remote-control/go-server/webui"
)

const (
	// MaxConnections 最大并发连接数限制，防止 DoS 攻击
	MaxConnections = 100
)

var (
	controlPort = flag.Int("port", 9001, "TCP control port")
	webPort     = flag.Int("web", 8080, "Web UI port")
	quality     = flag.Int("quality", 50, "JPEG quality (10-100)")
	hostname    = flag.String("name", "", "Device hostname")
	password    = flag.String("password", "", "Connection password")
)

func main() {
	flag.Parse()
	log.SetFlags(log.LstdFlags)
	log.Println("=== LAN Remote Control v1.3.0 (Go Engine) ===")

	capture := screen.NewCapturer(*quality)
	w, h := capture.Size()
	log.Printf("[Screen] %dx%d quality=%d", w, h, *quality)

	cam := camera.NewCamera()
	log.Printf("[Camera] available=%v", cam.Available())

	disc := discovery.NewServer(*controlPort, *hostname, *password != "")
	if err := disc.Start(); err != nil {
		log.Printf("[Discovery] warn: %v", err)
	} else {
		log.Println("[Discovery] UDP :9000 active")
	}

	webSrv := webui.NewServer(capture, cam, *webPort)
	go func() {
		if err := webSrv.Start(); err != nil {
			log.Printf("[WebUI] %v", err)
		}
	}()

	listener, err := net.Listen("tcp", fmt.Sprintf("0.0.0.0:%d", *controlPort))
	if err != nil {
		log.Fatalf("[Server] listen: %v", err)
	}
	log.Printf("[Server] TCP :%d ready", *controlPort)
	log.Printf("[Server] WebUI http://localhost:%d", *webPort)

	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, syscall.SIGINT, syscall.SIGTERM)
	go func() {
		<-sigChan
		log.Println("\n[Server] shutting down")
		listener.Close()
		disc.Stop()
		cam.Stop()
		os.Exit(0)
	}()

	// 连接数限制信号量
	connSemaphore := make(chan struct{}, MaxConnections)

	for {
		conn, err := listener.Accept()
		if err != nil {
			break
		}
		// 检查连接数限制
		select {
		case connSemaphore <- struct{}{}:
			go func(c net.Conn) {
				defer func() { <-connSemaphore }()
				handleClient(c, capture)
			}(conn)
		default:
			log.Printf("[Server] connection limit reached, rejecting %s", conn.RemoteAddr())
			conn.Close()
		}
	}
}

func handleClient(conn net.Conn, capture *screen.Capturer) {
	defer conn.Close()
	addr := conn.RemoteAddr().String()
	log.Printf("[Session] %s connected", addr)

	conn.SetReadDeadline(time.Now().Add(10 * time.Second))
	msgType, payload, err := protocol.RecvMessage(conn)
	if err != nil {
		if err == protocol.ErrMessageTooLarge {
			log.Printf("[Session %s] rejected: message too large", addr)
		} else {
			log.Printf("[Session %s] handshake: %v", addr, err)
		}
		return
	}
	conn.SetReadDeadline(time.Time{})

	if msgType != protocol.MsgJSON {
		return
	}
	var hello map[string]interface{}
	if err := json.Unmarshal(payload, &hello); err != nil {
		return
	}
	if hello["type"] != "hello" {
		return
	}
	if *password != "" && hello["password"] != *password {
		protocol.SendJSON(conn, map[string]interface{}{"type": "hello_fail", "reason": "wrong_password"})
		return
	}

	w, h := capture.Size()
	protocol.SendJSON(conn, map[string]interface{}{"type": "hello_ok", "width": w, "height": h})
	log.Printf("[Session %s] streaming %dx%d", addr, w, h)

	stop := make(chan struct{})
	go streamScreen(conn, capture, stop)

	for {
		msgType, payload, err := protocol.RecvMessage(conn)
		if err != nil {
			if err == protocol.ErrMessageTooLarge {
				log.Printf("[Session %s] error: message too large", addr)
			}
			break
		}
		if msgType == protocol.MsgJSON {
			var cmd map[string]interface{}
			if json.Unmarshal(payload, &cmd) == nil {
				if cmd["type"] == "set_quality" {
					if q, ok := cmd["quality"].(float64); ok {
						// 输入验证：限制 quality 范围在 10-100
						quality := int(q)
						if quality < 10 {
							quality = 10
						} else if quality > 100 {
							quality = 100
						}
						capture.SetQuality(quality)
					}
				}
			}
		}
	}
	close(stop)
	log.Printf("[Session %s] disconnected", addr)
}

func streamScreen(conn net.Conn, capture *screen.Capturer, stop <-chan struct{}) {
	ticker := time.NewTicker(33 * time.Millisecond)
	defer ticker.Stop()
	for {
		select {
		case <-stop:
			return
		case <-ticker.C:
			frame, err := capture.CaptureJPEG()
			if err != nil {
				continue
			}
			conn.SetWriteDeadline(time.Now().Add(2 * time.Second))
			if _, err := conn.Write(protocol.PackJPEG(frame)); err != nil {
				return
			}
			conn.SetWriteDeadline(time.Time{})
		}
	}
}
