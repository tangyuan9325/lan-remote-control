// Package capture implements on-demand, idle-parking capture workers —
// a direct Go port of rc14's ScreenCaptureThread/CameraCaptureThread with
// the same CPU-saving tricks: lazy start, idle park, frame-diff skip and
// long-edge resolution cap.
package capture

import (
	"bytes"
	"image"
	"image/color"
	"image/jpeg"
	"log"
	"sync"
	"time"

	"github.com/kbinani/screenshot"
)

const idleTimeout = 5 * time.Second

// Screen is an on-demand desktop capturer. Zero CPU cost while nobody watches.
type Screen struct {
	mu      sync.Mutex
	frame   []byte // latest encoded JPEG
	quality int
	w, h    int
	fps     int

	closed   bool
	lastView time.Time
	inflight int // number of attached consumers (multi-client support)

	setWake chan struct{}
	done    chan struct{}

	statsMu  sync.Mutex
	Captured int64
	Reused   int64

	idx        uint64 // monotonically increasing frame generation counter
	prevSample []byte
}

// NewScreen starts the singleton worker loop (it parks itself until touched).
func NewScreen(quality, w, h, fps int) *Screen {
	s := &Screen{
		quality: quality,
		w:       w, h: h,
		fps:     clampFPS(fps),
		setWake: make(chan struct{}, 1),
		done:    make(chan struct{}),
	}
	go s.run()
	return s
}

func clampFPS(f int) int {
	if f < 1 || f > 60 {
		return 15
	}
	return f
}

// Attach registers a consumer and wakes the worker. Returns detach func.
func (s *Screen) Attach() (detach func()) {
	s.mu.Lock()
	s.inflight++
	s.lastView = time.Now()
	s.mu.Unlock()
	s.wake()
	var once sync.Once
	return func() {
		once.Do(func() {
			s.mu.Lock()
			if s.inflight > 0 {
				s.inflight--
			}
			s.lastView = time.Now() // keep idle timer honest
			s.mu.Unlock()
		})
	}
}

func (s *Screen) wake() {
	select {
	case s.setWake <- struct{}{}:
	default:
	}
}

// Touch marks interest from a poll-based consumer (HTTP MJPEG handler).
func (s *Screen) Touch() {
	s.mu.Lock()
	s.lastView = time.Now()
	s.mu.Unlock()
	s.wake()
}

// UpdateSettings applies new quality/fps/resolution (resolution capped at
// maxEdge on the long side — mirrors rc14 MAX_SIZE).
func (s *Screen) UpdateSettings(quality, fps, w, h, maxEdge int) {
	s.mu.Lock()
	if quality >= 10 && quality <= 95 {
		s.quality = quality
	}
	s.fps = clampFPS(fps)
	if w >= 2 && h >= 2 && w <= 7680 && h <= 4320 {
		long := w
		if h > long {
			long = h
		}
		if maxEdge > 0 && long > maxEdge {
			scale := float64(maxEdge) / float64(long)
			w = int(float64(w) * scale)
			h = int(float64(h) * scale)
		}
		s.w, s.h = w, h
	}
	s.mu.Unlock()
	s.wake()
}

func (s *Screen) Stop() {
	s.mu.Lock()
	if !s.closed {
		s.closed = true
		close(s.done)
	}
	s.mu.Unlock()
	s.wake()
}

func (s *Screen) hasViewers() bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.inflight > 0 || time.Since(s.lastView) < idleTimeout
}

// sample produces a cheap 16x9 downscale fingerprint for change detection.
func sample(img image.Image) []byte {
	const sw, sh = 16, 9
	out := make([]byte, 0, sw*sh*3)
	b := img.Bounds()
	for y := 0; y < sh; y++ {
		for x := 0; x < sw; x++ {
			px := b.Min.X + x*b.Dx()/sw
			py := b.Min.Y + y*b.Dy()/sh
			r, g, bb, _ := img.At(px, py).RGBA()
			out = append(out, byte(r>>8), byte(g>>8), byte(bb>>8))
		}
	}
	return out
}

func (s *Screen) run() {
	ticker := time.NewTicker(time.Millisecond * 50)
	defer ticker.Stop()
	var nextCapture time.Time
	for {
		select {
		case <-s.done:
			return
		case now := <-ticker.C:
			s.mu.Lock()
			fps := s.fps
			s.mu.Unlock()
			interval := time.Second / time.Duration(fps)
			if !nextCapture.IsZero() && now.Before(nextCapture) {
				continue
			}
			if !s.hasViewers() {
				nextCapture = time.Time{} // parked: zero CPU
				continue
			}
			s.captureOnce()
			nextCapture = now.Add(interval)
		}
	}
}

func (s *Screen) captureOnce() {
	if screenshot.NumActiveDisplays() == 0 {
		return
	}
	bounds := screenshot.GetDisplayBounds(0)
	img, err := screenshot.CaptureRect(bounds)
	if err != nil {
		log.Printf("[screen] capture error: %v", err)
		return
	}

	cur := sample(img)
	s.mu.Lock()
	changed := len(cur) != len(s.prevSample)
	if !changed {
		for i := range cur {
			if cur[i] != s.prevSample[i] {
				changed = true
				break
			}
		}
	}
	if !changed && s.frame != nil {
		q, w, h := s.quality, s.w, s.h
		s.mu.Unlock()
		_ = q
		_ = w
		_ = h
		s.statsMu.Lock()
		s.Reused++
		s.statsMu.Unlock()
		return // screen unchanged: reuse last encoded JPEG, do no work
	}
	s.prevSample = cur
	s.mu.Unlock()

	// resize to stream resolution (nearest-neighbor fast path via sub-image copy)
	w, h := s.w, s.h
	target := resizeJPEG(img, w, h, s.quality)
	if target == nil {
		return
	}
	s.mu.Lock()
	s.frame = target
	s.idx++
	s.mu.Unlock()
	s.statsMu.Lock()
	s.Captured++
	s.statsMu.Unlock()
}

// resizeJPEG downscales with simple box sampling then encodes. Keeping this
// dependency-free (no stdlib resize is high-quality anyway) — bilinear-ish
// average over integer steps is plenty for remote viewing and much cheaper
// than pulling in golang.org/x/image/draw (offline builds).
func resizeJPEG(src image.Image, w, h, quality int) []byte {
	sb := src.Bounds()
	if w <= 0 || h <= 0 {
		w, h = sb.Dx(), sb.Dy()
	}
	dst := image.NewRGBA(image.Rect(0, 0, w, h))
	for y := 0; y < h; y++ {
		sy0 := sb.Min.Y + y*sb.Dy()/h
		sy1 := sb.Min.Y + (y+1)*sb.Dy()/h
		if sy1 <= sy0 {
			sy1 = sy0 + 1
		}
		for x := 0; x < w; x++ {
			sx0 := sb.Min.X + x*sb.Dx()/w
			sx1 := sb.Min.X + (x+1)*sb.Dx()/w
			if sx1 <= sx0 {
				sx1 = sx0 + 1
			}
			var r, g, b, a, n uint32
			for sy := sy0; sy < sy1; sy += 1 {
				for sx := sx0; sx < sx1; sx += 1 {
					pr, pg, pb, pa := src.At(sx, sy).RGBA()
					r += pr >> 8
					g += pg >> 8
					b += pb >> 8
					a += pa >> 8
					n++
				}
			}
			if n == 0 {
				n = 1
			}
			dst.SetRGBA(x, y, color.RGBA{R: uint8(r / n), G: uint8(g / n), B: uint8(b / n), A: uint8(a / n)})
		}
	}
	var buf bytes.Buffer
	if err := jpeg.Encode(&buf, dst, &jpeg.Options{Quality: quality}); err != nil {
		return nil
	}
	return buf.Bytes()
}

// Frame returns the latest encoded JPEG (empty when nothing captured yet).
func (s *Screen) Frame() []byte {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.frame
}

// LastIndex returns the frame generation counter. MJPEG handlers compare it
// against their previous value to skip re-sending unchanged frames.
func (s *Screen) LastIndex() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return int(s.idx)
}

// Stats returns captured/reused counters (for the status endpoint).
func (s *Screen) Stats() (int64, int64) {
	s.statsMu.Lock()
	defer s.statsMu.Unlock()
	return s.Captured, s.Reused
}
