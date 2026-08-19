package camera

import (
	"bytes"
	"errors"
	"image"
	"image/color"
	"image/jpeg"
	"os/exec"
	"sync"
	"time"
)

type Camera struct {
	mu       sync.Mutex
	running  bool
	cmd      *exec.Cmd
	frameBuf []byte
	useFFmpeg bool
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
	go c.readFFmpeg(stdout)
	return nil
}

func (c *Camera) readFFmpeg(r interface{ Read([]byte) (int, error) }) {
	buf := make([]byte, 1024*1024)
	for c.running {
		n, err := r.Read(buf)
		if err != nil {
			break
		}
		if n > 0 {
			c.mu.Lock()
			data := buf[:n]
			if start := bytes.Index(data, []byte{0xFF, 0xD8}); start >= 0 {
				if end := bytes.Index(data[start:], []byte{0xFF, 0xD9}); end >= 0 {
					c.frameBuf = make([]byte, end+2)
					copy(c.frameBuf, data[start:start+end+2])
				}
			}
			c.mu.Unlock()
		}
	}
}

func (c *Camera) testPatternLoop() {
	ticker := time.NewTicker(200 * time.Millisecond)
	defer ticker.Stop()
	for c.running {
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
	return c.frameBuf, nil
}

func (c *Camera) Stop() {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.running = false
	if c.cmd != nil && c.cmd.Process != nil {
		c.cmd.Process.Kill()
	}
	c.cmd = nil
}

func (c *Camera) IsRunning() bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.running
}
