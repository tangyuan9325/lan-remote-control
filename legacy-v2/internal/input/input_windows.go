//go:build windows

// Package input provides Win32 keyboard/mouse injection for Legacy V2.
// It reuses the hardened implementation from go-server/inputinject by
// exposing a command adapter compatible with the rc14 web protocol
// ({x, y, screen_width, screen_height} in *display pixels*, letterbox-aware).
package input

import (
	"math"

	inject "github.com/tangyuan9325/lan-remote-control/go-server/inputinject"
)

// Injector wraps the go-server Win32 injector.
type Injector struct{ inner *inject.Injector }

func New() *Injector { return &Injector{inner: inject.New()} }

// MapPoint converts client coordinates inside the streamed image box to
// normalized 0..1 coordinates, accounting for object-fit: contain letterboxing.
// (Port of rc14._map_to_screen — fixes the legacy fullscreen ratio bug.)
func MapPoint(x, y, sw, sh float64, imgW, imgH int) (float64, float64) {
	if sw <= 0 || sh <= 0 || imgW <= 0 || imgH <= 0 {
		return 0, 0
	}
	nx := math.Max(0, math.Min(1, x/sw))
	ny := math.Max(0, math.Min(1, y/sh))
	imgAspect := float64(imgW) / float64(imgH)
	boxAspect := sw / sh
	var relX, relY float64
	if boxAspect > imgAspect {
		visW := sh * imgAspect
		offX := (sw - visW) / 2.0
		if visW > 0 {
			relX = math.Max(0, math.Min(1, (x-offX)/visW))
		} else {
			relX = nx
		}
		relY = ny
	} else {
		visH := sw / imgAspect
		offY := (sh - visH) / 2.0
		relX = nx
		if visH > 0 {
			relY = math.Max(0, math.Min(1, (y-offY)/visH))
		} else {
			relY = ny
		}
	}
	return relX, relY
}

// HandleCommand dispatches one JSON command (rc14 web shape) into Win32 events.
func (in *Injector) HandleCommand(cmd map[string]interface{}, imgW, imgH int) {
	t, _ := cmd["type"].(string)
	switch t {
	case "mouse_move", "mouse_down", "mouse_up", "mouse_click", "mouse_double":
		x := numOf(cmd["x"])
		y := numOf(cmd["y"])
		sw := numOf(cmd["screen_width"])
		sh := numOf(cmd["screen_height"])
		rx, ry := MapPoint(x, y, sw, sh, imgW, imgH)
		m := map[string]interface{}{"type": t, "x": rx, "y": ry}
		if b, ok := cmd["button"].(string); ok {
			m["button"] = b
		}
		in.inner.HandleCommand(m)
	case "mouse_scroll":
		in.inner.HandleCommand(map[string]interface{}{
			"type":  "mouse_scroll",
			"delta": numOf(cmd["delta"]),
		})
	case "key_down", "key_up":
		k, _ := cmd["key"].(string)
		in.inner.HandleCommand(map[string]interface{}{"type": t, "key": k})
	case "key_type", "text_input":
		txt, _ := cmd["text"].(string)
		in.inner.HandleCommand(map[string]interface{}{"type": "key_type", "text": txt})
	}
}

func numOf(v interface{}) float64 {
	switch n := v.(type) {
	case float64:
		return n
	case int:
		return float64(n)
	}
	return 0
}

func (in *Injector) Close() { in.inner.Close() }
