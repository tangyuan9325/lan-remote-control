package main

import (
	"crypto/subtle"
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"net"
	"net/http"
	"os"
	"os/signal"
	"sync"
	"syscall"
	"time"

	"github.com/tangyuan9325/lan-remote-control/go-server/camera"
	"github.com/tangyuan9325/lan-remote-control/go-server/discovery"
	"github.com/tangyuan9325/lan-remote-control/go-server/inputinject"
	"github.com/tangyuan9325/lan-remote-control/go-server/protocol"
	"github.com/tangyuan9325/lan-remote-control/go-server/screen"
	"github.com/tangyuan9325/lan-remote-control/go-server/webui"
)

const (
	// MaxConnections 最大并发连接数限制，防止 DoS 攻击
	MaxConnections = 100
	// streamWriteTimeout 单帧写入超时：慢客户端在超时后会被断开，
	// 而不是无限期挂起推流 goroutine
	streamWriteTimeout = 5 * time.Second
)

// checkPassword 使用恒定时间比较校验密码，避免时序侧信道泄露。
func checkPassword(got string, want string) bool {
	return subtle.ConstantTimeCompare([]byte(got), []byte(want)) == 1
}

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

	injector := inputinject.New()

	disc := discovery.NewServer(*controlPort, *hostname, *password != "")
	if err := disc.Start(); err != nil {
		log.Printf("[Discovery] warn: %v", err)
	} else {
		log.Println("[Discovery] UDP :9000 active")
	}

	webSrv := webui.NewServer(capture, cam, injector, *webPort, *password)
	go func() {
		if err := webSrv.Start(); err != nil && err != http.ErrServerClosed {
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
		webSrv.Shutdown()
		// 给已接受连接一点时间收尾，然后退出
		time.Sleep(200 * time.Millisecond)
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
				handleClient(c, capture, injector)
			}(conn)
		default:
			log.Printf("[Server] connection limit reached, rejecting %s", conn.RemoteAddr())
			conn.Close()
		}
	}
}

func handleClient(conn net.Conn, capture *screen.Capturer, injector *inputinject.Injector) {
	defer conn.Close()
	addr := conn.RemoteAddr().String()
	log.Printf("[Session] %s connected", addr)

	// 握手阶段读超时；建立会话后改为按"空闲超时"续期（见下方 ping/pong 保活）
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
	if *password != "" {
		got, _ := hello["password"].(string)
		// 恒定时间比较，防止时序侧信道
		if !checkPassword(got, *password) {
			protocol.SendJSON(conn, map[string]interface{}{"type": "hello_fail", "reason": "wrong_password"})
			return
		}
	}

	w, h := capture.Size()
	protocol.SendJSON(conn, map[string]interface{}{"type": "hello_ok", "width": w, "height": h})
	log.Printf("[Session %s] streaming %dx%d", addr, w, h)

	stop := make(chan struct{})
	var stopOnce sync.Once
	closeStop := func() { stopOnce.Do(func() { close(stop) }) }
	defer closeStop()

	go streamScreen(conn, capture, stop, closeStop)

	// 会话期间设置读超时：60 秒无任何消息（含 ping）则判定为死连接并断开，
	// 避免 NAT/防火墙静默断连后 session 与推流 goroutine 永久泄漏
	for {
		conn.SetReadDeadline(time.Now().Add(60 * time.Second))
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
				switch cmd["type"] {
				case "ping":
					protocol.SendJSON(conn, map[string]interface{}{"type": "pong"})
				case "set_quality":
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
				default:
					// 鼠标/键盘输入事件注入（mouse_move/mouse_down/mouse_up/
					// mouse_click/mouse_double/mouse_scroll/key_down/key_up/key_type）
					injector.HandleCommand(cmd)
				}
			}
		}
	}
	log.Printf("[Session %s] disconnected", addr)
}

func streamScreen(conn net.Conn, capture *screen.Capturer, stop <-chan struct{}, onClose func()) {
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
			// 写入超时不再被重置为无限：慢客户端会触发超时并断开，
			// 而不是让本 goroutine 永久挂起
			conn.SetWriteDeadline(time.Now().Add(streamWriteTimeout))
			if _, err := conn.Write(protocol.PackJPEG(frame)); err != nil {
				onClose()
				return
			}
		}
	}
}
