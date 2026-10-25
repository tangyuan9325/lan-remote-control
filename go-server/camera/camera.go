package camera

import (
	"bytes"
	"errors"
	"image"
	"image/color"
	"image/jpeg"
	"io"
	"os/exec"
	"sync"
	"time"
)

type Camera struct {
	mu        sync.Mutex
	running   bool
	cmd       *exec.Cmd
	frameBuf  []byte
	useFFmpeg bool
	readDone  chan struct{} // FFmpeg stdout 读取 goroutine 结束信号
}

func NewCamera() *Camera {
	return &Camera{}
}

func (c *Camera) Available() bool {
	_, err := exec.LookPath("ffmpeg")
	return err == nil
}

func (c *Camera) Start() error {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.running {
		return nil
	}

	cmd := exec.Command("ffmpeg",
		"-f", "dshow",
		"-i", "video=Integrated Camera",
		"-f", "mjpeg",
		"-r", "15",
		"-q:v", "5",
		"-",
	)

	stdout, err := cmd.StdoutPipe()
	if err != nil {
		c.useFFmpeg = false
		c.running = true
		go c.testPatternLoop()
		return nil
	}
	if err := cmd.Start(); err != nil {
		c.useFFmpeg = false
		c.running = true
		go c.testPatternLoop()
		return nil
	}

	c.cmd = cmd
	c.useFFmpeg = true
	c.running = true
	c.readDone = make(chan struct{})
	go c.readFFmpeg(stdout, cmd, c.readDone)
	return nil
}

// readFFmpeg 从 FFmpeg stdout 中按 JPEG SOI/EOI 标记正确切帧。
// 使用滑动窗口 + carry-over 缓冲，避免原实现中"单次 Read 内必须同时含
// 0xFFD8 与 0xFFD9 否则丢帧"以及跨读取边界产生坏帧的问题。
func (c *Camera) readFFmpeg(r io.Reader, cmd *exec.Cmd, done chan<- struct{}) {
	defer close(done)
	const maxFrame = 8 * 1024 * 1024 // 单帧上限 8MB，防止异常数据撑爆内存
	window := make([]byte, 0, 1024*1024)
	chunk := make([]byte, 64*1024)
	for {
		n, err := r.Read(chunk)
		if n > 0 {
			window = append(window, chunk[:n]...)
			// 丢弃 SOI 之前的垃圾数据
			if start := bytes.Index(window, []byte{0xFF, 0xD8}); start > 0 {
				window = window[start:]
			} else if start < 0 {
				if len(window) > 4096 {
					window = window[len(window)-1:]
				}
				continue
			}
			// 查找 EOI（跳过 SOI 本身）
			if end := indexOfEOI(window); end >= 0 {
				frame := make([]byte, end+2)
				copy(frame, window[:end+2])
				c.mu.Lock()
				c.frameBuf = frame
				c.mu.Unlock()
				window = window[end+2:]
			} else if len(window) > maxFrame {
				// 帧超长，视为损坏，重置窗口
				window = window[:0]
			}
		}
		if err != nil {
			return
		}
	}
}

// indexOfEOI 在 buf 中查找 0xFF 0xD9（JPEG EOI 标记），返回其起始下标；
// 要求至少越过 SOI 两字节之后才开始搜索。找不到返回 -1。
func indexOfEOI(buf []byte) int {
	for i := 2; i+1 < len(buf); i++ {
		if buf[i] == 0xFF && buf[i+1] == 0xD9 {
			return i
		}
	}
	return -1
}

func (c *Camera) testPatternLoop() {
	ticker := time.NewTicker(200 * time.Millisecond)
	defer ticker.Stop()
	for {
		c.mu.Lock()
		running := c.running
		c.mu.Unlock()
		if !running {
			return
		}
		<-ticker.C
		img := image.NewRGBA(image.Rect(0, 0, 640, 480))
		now := time.Now()
		for y := 0; y < 480; y++ {
			for x := 0; x < 640; x++ {
				r := uint8((x + now.Second()*10) % 256)
				g := uint8((y + now.Second()*10) % 256)
				b := uint8(128)
				img.Set(x, y, color.RGBA{r, g, b, 255})
			}
		}
		var buf bytes.Buffer
		jpeg.Encode(&buf, img, &jpeg.Options{Quality: 50})
		c.mu.Lock()
		c.frameBuf = buf.Bytes()
		c.mu.Unlock()
	}
}

func (c *Camera) GetFrame() ([]byte, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.frameBuf == nil {
		return nil, errors.New("no frame available")
	}
	// 返回副本：调用方可能在锁外持有该切片，直接共享会与新帧写入竞争
	out := make([]byte, len(c.frameBuf))
	copy(out, c.frameBuf)
	return out, nil
}

func (c *Camera) Stop() {
	c.mu.Lock()
	if !c.running {
		c.mu.Unlock()
		return
	}
	c.running = false
	cmd := c.cmd
	done := c.readDone
	c.cmd = nil
	c.readDone = nil
	c.mu.Unlock()

	// 在锁外清理子进程，避免 Wait 阻塞持锁
	if cmd != nil && cmd.Process != nil {
		cmd.Process.Kill()
		if done != nil {
			select {
			case <-done:
			case <-time.After(2 * time.Second):
			}
		}
		// 等待回收，防止僵尸进程句柄泄漏
		cmd.Wait()
	}
}

func (c *Camera) IsRunning() bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.running
}
