//go:build !windows

// Package input — non-Windows stub. Injection needs Win32 APIs; on other
// platforms commands are acknowledged but ignored (mirrors rc14 behavior when
// pyautogui has no display).
package input

type Injector struct{}

func New() *Injector { return &Injector{} }

func MapPoint(x, y, sw, sh float64, imgW, imgH int) (float64, float64) {
	if sw <= 0 || sh <= 0 {
		return 0, 0
	}
	nx := x / sw
	ny := y / sh
	if nx < 0 {
		nx = 0
	} else if nx > 1 {
		nx = 1
	}
	if ny < 0 {
		ny = 0
	} else if ny > 1 {
		ny = 1
	}
	return nx, ny
}

func (in *Injector) HandleCommand(cmd map[string]interface{}, imgW, imgH int) {}

func (in *Injector) Close() {}
