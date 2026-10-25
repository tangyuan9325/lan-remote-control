//go:build windows

package inputinject

import (
	"syscall"
	"time"
	"unicode/utf16"

	"github.com/tangyuan9325/lan-remote-control/go-server/screen"
)

var (
	user32               = syscall.NewLazyDLL("user32.dll")
	procSetCursorPos     = user32.NewProc("SetCursorPos")
	procMouseEvent       = user32.NewProc("mouse_event")
	procKeyBD            = user32.NewProc("keybd_event")
	procVkKeyScanW       = user32.NewProc("VkKeyScanW")
	procGetSystemMetrics = user32.NewProc("GetSystemMetrics")
)

const (
	MOUSEEVENTF_MOVE       = 0x0001
	MOUSEEVENTF_LEFTDOWN   = 0x0002
	MOUSEEVENTF_LEFTUP     = 0x0004
	MOUSEEVENTF_RIGHTDOWN  = 0x0008
	MOUSEEVENTF_RIGHTUP    = 0x0010
	MOUSEEVENTF_MIDDLEDOWN = 0x0020
	MOUSEEVENTF_MIDDLEUP   = 0x0040
	MOUSEEVENTF_WHEEL      = 0x0800

	KEYEVENTF_UNICODE = 0x0004
	KEYEVENTF_KEYUP   = 0x0002

	SM_CXSCREEN = 0
	SM_CYSCREEN = 1
	WHEEL_DELTA = 120
)

type Injector struct{}

func New() *Injector { return &Injector{} }

// screenPixels 返回主显示器像素尺寸；优先使用采集器记录的分辨率，
// 若尚未初始化则回退到 GetSystemMetrics。
func screenPixels() (int, int) {
	if w, h := screen.PrimarySize(); w > 0 && h > 0 {
		return w, h
	}
	cx, _, _ := procGetSystemMetrics.Call(SM_CXSCREEN)
	cy, _, _ := procGetSystemMetrics.Call(SM_CYSCREEN)
	return int(cx), int(cy)
}

// toPixels 将归一化坐标 (0..1) 转换为屏幕像素坐标并做边界钳制
func toPixels(nx, ny float64) (int32, int32) {
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
	w, h := screenPixels()
	if w <= 0 {
		w = 1
	}
	if h <= 0 {
		h = 1
	}
	return int32(nx * float64(w-1)), int32(ny * float64(h-1))
}

func buttonFlags(button string) (down uint32, up uint32) {
	switch button {
	case "right":
		return MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP
	case "middle":
		return MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP
	default:
		return MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP
	}
}

func (in *Injector) moveTo(nx, ny float64) {
	x, y := toPixels(nx, ny)
	procSetCursorPos.Call(uintptr(x), uintptr(y))
	procMouseEvent.Call(MOUSEEVENTF_MOVE, uintptr(x), uintptr(y), 0, 0)
}

func (in *Injector) HandleCommand(cmd map[string]interface{}) {
	switch cmd["type"] {
	case "mouse_move":
		in.moveTo(floatOf(cmd["x"]), floatOf(cmd["y"]))

	case "mouse_down":
		d, _ := buttonFlags(stringOf(cmd["button"]))
		in.moveTo(floatOf(cmd["x"]), floatOf(cmd["y"]))
		procMouseEvent.Call(uintptr(d), 0, 0, 0, 0)

	case "mouse_up":
		_, u := buttonFlags(stringOf(cmd["button"]))
		in.moveTo(floatOf(cmd["x"]), floatOf(cmd["y"]))
		procMouseEvent.Call(uintptr(u), 0, 0, 0, 0)

	case "mouse_click":
		d, u := buttonFlags(stringOf(cmd["button"]))
		in.moveTo(floatOf(cmd["x"]), floatOf(cmd["y"]))
		procMouseEvent.Call(uintptr(d), 0, 0, 0, 0)
		time.Sleep(30 * time.Millisecond)
		procMouseEvent.Call(uintptr(u), 0, 0, 0, 0)

	case "mouse_double":
		d, u := buttonFlags(stringOf(cmd["button"]))
		in.moveTo(floatOf(cmd["x"]), floatOf(cmd["y"]))
		for i := 0; i < 2; i++ {
			procMouseEvent.Call(uintptr(d), 0, 0, 0, 0)
			time.Sleep(30 * time.Millisecond)
			procMouseEvent.Call(uintptr(u), 0, 0, 0, 0)
			time.Sleep(30 * time.Millisecond)
		}

	case "mouse_scroll":
		dy := int(floatOf(cmd["dy"]))
		if dy == 0 {
			return
		}
		delta := int32(dy) * WHEEL_DELTA
		procMouseEvent.Call(MOUSEEVENTF_WHEEL, 0, 0, uintptr(uint32(delta)), 0)

	case "key_down":
		in.keyDown(stringOf(cmd["key"]))

	case "key_up":
		in.keyUp(stringOf(cmd["key"]))

	case "key_type":
		in.typeText(stringOf(cmd["text"]))
	}
}

func vkFor(key string) (vk byte, shift bool, ok bool) {
	if key == "" {
		return 0, false, false
	}
	if code, found := virtualKeys[key]; found {
		return code, false, true
	}
	r := []rune(key)
	if len(r) != 1 {
		return 0, false, false
	}
	encoded := utf16.Encode(r)
	res, _, _ := procVkKeyScanW.Call(uintptr(encoded[0]))
	// -1 (0xFFFF..) 表示当前键盘布局无法映射该字符
	if res&0xFFFFFFFFFFFF0000 != 0 || res == 0xFFFF {
		return 0, false, false
	}
	vk = byte(res & 0xFF)
	shift = (res>>8)&0x01 == 1
	if vk == 0xFF {
		return 0, false, false
	}
	return vk, shift, true
}

func (in *Injector) keyDown(key string) {
	vk, shift, ok := vkFor(key)
	if !ok {
		return
	}
	if shift {
		procKeyBD.Call(0x10, 0, 0, 0) // VK_SHIFT down
	}
	procKeyBD.Call(uintptr(vk), 0, 0, 0)
}

func (in *Injector) keyUp(key string) {
	vk, shift, ok := vkFor(key)
	if !ok {
		return
	}
	procKeyBD.Call(uintptr(vk), 0, KEYEVENTF_KEYUP, 0)
	if shift {
		procKeyBD.Call(0x10, 0, KEYEVENTF_KEYUP, 0)
	}
}

func (in *Injector) typeText(text string) {
	if text == "" {
		return
	}
	for _, u := range utf16.Encode([]rune(text)) {
		procKeyBD.Call(0, uintptr(u), KEYEVENTF_UNICODE, 0)
		procKeyBD.Call(0, uintptr(u), KEYEVENTF_UNICODE|KEYEVENTF_KEYUP, 0)
	}
}

func (in *Injector) Close() {}
