// Package agent 实现与 Android Agent 应用的通信协议：
//   - 控制端口 8586：换行分隔的 JSON 指令，服务端回 {"ok":true} 等确认
//   - 视频端口 8585：4 字节大端长度 + NAL 数据（首个 '{' 开头者为 JSON 配置消息，需跳过）
package agent

import (
	"bufio"
	"encoding/binary"
	"encoding/json"
	"errors"
	"io"
	"net"
	"strings"
	"sync"
	"time"
)

const (
	VideoPort   = 8585
	ControlPort = 8586
)

// FreePort 返回一个本机空闲端口（用于 adb forward 的本地端）。
func FreePort() int {
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return 0
	}
	defer l.Close()
	return l.Addr().(*net.TCPAddr).Port
}

// Control 是与 Agent 控制服务端的一条连接。
type Control struct {
	conn   net.Conn
	r      *bufio.Reader
	mu     sync.Mutex
	broken bool
}

// ConnectControl 连接控制端口。
func ConnectControl(addr string) (*Control, error) {
	conn, err := net.DialTimeout("tcp", addr, 3*time.Second)
	if err != nil {
		return nil, err
	}
	return &Control{conn: conn, r: bufio.NewReader(conn)}, nil
}

// Healthy 返回连接是否仍然可用。
// 原实现把连接永久缓存，一旦对端断开就再也不会重建，
// 之后每次输入都先失败一次 Agent 再退回 shell（表现为明显卡顿 + 日志刷屏）。
func (c *Control) Healthy() bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	return !c.broken
}

type ctrlMsg struct {
	Type     string `json:"type"`
	X        int    `json:"x,omitempty"`
	Y        int    `json:"y,omitempty"`
	X1       int    `json:"x1,omitempty"`
	Y1       int    `json:"y1,omitempty"`
	X2       int    `json:"x2,omitempty"`
	Y2       int    `json:"y2,omitempty"`
	Duration int    `json:"duration,omitempty"`
	Key      string `json:"key,omitempty"`
}

type ctrlAck struct {
	Ok    bool   `json:"ok"`
	Error string `json:"error,omitempty"`
}

func (c *Control) send(m ctrlMsg) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.broken {
		return errors.New("agent 控制连接已断开")
	}
	b, err := json.Marshal(m)
	if err != nil {
		return err
	}
	b = append(b, '\n')
	_ = c.conn.SetWriteDeadline(time.Now().Add(5 * time.Second))
	if _, err := c.conn.Write(b); err != nil {
		c.markBroken()
		return err
	}
	_ = c.conn.SetReadDeadline(time.Now().Add(5 * time.Second))
	line, err := c.r.ReadString('\n')
	if err != nil {
		c.markBroken()
		return err
	}
	var ack ctrlAck
	if err := json.Unmarshal([]byte(strings.TrimSpace(line)), &ack); err != nil {
		c.markBroken()
		return err
	}
	if !ack.Ok {
		if ack.Error == "" {
			ack.Error = "agent 拒绝指令"
		}
		return errors.New(ack.Error)
	}
	return nil
}

// markBroken 标记连接不可用并关闭底层连接（调用方已持锁）。
func (c *Control) markBroken() {
	c.broken = true
	if c.conn != nil {
		_ = c.conn.Close()
	}
}

// Tap 通过 Agent 手势服务模拟点击。
func (c *Control) Tap(x, y int) error { return c.send(ctrlMsg{Type: "tap", X: x, Y: y}) }

// Swipe 通过 Agent 手势服务模拟滑动。
func (c *Control) Swipe(x1, y1, x2, y2, durMS int) error {
	return c.send(ctrlMsg{Type: "swipe", X1: x1, Y1: y1, X2: x2, Y2: y2, Duration: durMS})
}

// Key 通过 Agent 无障碍全局动作发送按键。
func (c *Control) Key(k string) error { return c.send(ctrlMsg{Type: "key", Key: k}) }

// Close 关闭连接。
func (c *Control) Close() {
	if c.conn != nil {
		c.conn.Close()
	}
}

// VideoConn 连接 Agent 视频流，读取长度前缀的 NAL 块。
type VideoConn struct {
	conn net.Conn
	r    *bufio.Reader
	buf  []byte // 复用的接收缓冲，避免每帧都新分配（30fps 下是持续的 GC 压力）
}

// ConnectVideo 连接视频端口。
func ConnectVideo(addr string) (*VideoConn, error) {
	conn, err := net.DialTimeout("tcp", addr, 3*time.Second)
	if err != nil {
		return nil, err
	}
	return &VideoConn{conn: conn, r: bufio.NewReaderSize(conn, 256*1024)}, nil
}

// nextChunk 读取一个长度前缀数据块（返回的切片在下一次调用前有效）。
func (v *VideoConn) nextChunk() ([]byte, error) {
	var hdr [4]byte
	if _, err := io.ReadFull(v.r, hdr[:]); err != nil {
		return nil, err
	}
	n := binary.BigEndian.Uint32(hdr[:])
	if n == 0 || n > 8*1024*1024 {
		return nil, errors.New("agent 视频块长度非法")
	}
	if cap(v.buf) < int(n) {
		v.buf = make([]byte, n)
	}
	buf := v.buf[:n]
	if _, err := io.ReadFull(v.r, buf); err != nil {
		return nil, err
	}
	return buf, nil
}

// Close 关闭视频连接。
func (v *VideoConn) Close() {
	if v.conn != nil {
		v.conn.Close()
	}
}

// annexBReader 将长度前缀 NAL 块转换为带起始码的 Annex-B 字节流，
// 跳过以 '{' 开头的 JSON 配置消息，可直接喂给 ffmpeg。
type annexBReader struct {
	vc  *VideoConn
	cur []byte
	pos int
}

// NewAnnexBReader 返回 Annex-B 适配流。
func (v *VideoConn) NewAnnexBReader() io.Reader {
	return &annexBReader{vc: v}
}

func (r *annexBReader) Read(p []byte) (int, error) {
	for r.pos >= len(r.cur) {
		chunk, err := r.vc.nextChunk()
		if err != nil {
			return 0, err
		}
		if len(chunk) > 0 && chunk[0] == '{' {
			continue // 配置 JSON
		}
		// 复用 r.cur 的容量：先写 4 字节起始码，再追加 NAL
		r.cur = append(r.cur[:0], 0x00, 0x00, 0x00, 0x01)
		r.cur = append(r.cur, chunk...)
		r.pos = 0
	}
	n := copy(p, r.cur[r.pos:])
	r.pos += n
	return n, nil
}
