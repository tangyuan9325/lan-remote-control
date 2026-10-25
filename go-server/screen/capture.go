package screen

import (
	"bytes"
	"image/jpeg"
	"sync"
	"time"

	"github.com/kbinani/screenshot"
)

type Capturer struct {
	mu      sync.Mutex
	quality int
	width   int
	height  int
}

func NewCapturer(quality int) *Capturer {
	c := &Capturer{quality: quality}
	if quality <= 0 {
		c.quality = 50
	}
	if n := screenshot.NumActiveDisplays(); n > 0 {
		bounds := screenshot.GetDisplayBounds(0)
		c.width = bounds.Dx()
		c.height = bounds.Dy()
	}
	return c
}

func (c *Capturer) Size() (int, int) {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.width, c.height
}

// PrimarySize 返回主显示器当前像素尺寸，并顺带同步 Capturer 内部缓存，
// 解决分辨率变更后 hello_ok/输入映射使用过期尺寸的问题。
func PrimarySize() (int, int) {
	if screenshot.NumActiveDisplays() == 0 {
		return 0, 0
	}
	bounds := screenshot.GetDisplayBounds(0)
	return bounds.Dx(), bounds.Dy()
}

func (c *Capturer) SetQuality(q int) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if q >= 10 && q <= 100 {
		c.quality = q
	}
}

// CaptureJPEG captures the screen and returns JPEG-encoded bytes.
// Uses GDI-based capture via kbinani/screenshot (fast, zero-CGO).
// 每次采集都会检测显示器分辨率变化并更新缓存尺寸。
func (c *Capturer) CaptureJPEG() ([]byte, error) {
	c.mu.Lock()
	q := c.quality
	c.mu.Unlock()

	if screenshot.NumActiveDisplays() == 0 {
		return nil, bytes.ErrTooLarge // 无可用显示器
	}
	bounds := screenshot.GetDisplayBounds(0)
	img, err := screenshot.CaptureRect(bounds)
	if err != nil {
		return nil, err
	}

	// 分辨率变更时同步缓存（供新会话 hello_ok 与输入坐标映射使用）
	c.mu.Lock()
	if c.width != bounds.Dx() || c.height != bounds.Dy() {
		c.width = bounds.Dx()
		c.height = bounds.Dy()
	}
	c.mu.Unlock()

	var buf bytes.Buffer
	err = jpeg.Encode(&buf, img, &jpeg.Options{Quality: q})
	if err != nil {
		return nil, err
	}
	return buf.Bytes(), nil
}

func (c *Capturer) StartStream(fps int, frameChan chan<- []byte, stop <-chan struct{}) {
	if fps <= 0 {
		fps = 30
	}
	interval := time.Second / time.Duration(fps)
	ticker := time.NewTicker(interval)
	defer ticker.Stop()

	for {
		select {
		case <-stop:
			return
		case <-ticker.C:
			frame, err := c.CaptureJPEG()
			if err == nil && frame != nil {
				select {
				case frameChan <- frame:
				default:
				}
			}
		}
	}
}
