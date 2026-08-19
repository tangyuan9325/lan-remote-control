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
	bounds := screenshot.GetDisplayBounds(0)
	c.width = bounds.Dx()
	c.height = bounds.Dy()
	return c
}

func (c *Capturer) Size() (int, int) {
	return c.width, c.height
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
func (c *Capturer) CaptureJPEG() ([]byte, error) {
	c.mu.Lock()
	q := c.quality
	c.mu.Unlock()

	bounds := screenshot.GetDisplayBounds(0)
	img, err := screenshot.CaptureRect(bounds)
	if err != nil {
		return nil, err
	}

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
