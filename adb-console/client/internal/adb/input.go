package adb

import (
	"fmt"
	"strconv"
	"strings"
	"unicode/utf8"
)

// MaxTextLen 单次文本输入的最大字符数（超过后 adb 命令过长，容易被设备端截断）。
const MaxTextLen = 4096

// keyCodes 常用 Android 按键码。
var keyCodes = map[string]string{
	"HOME": "3", "BACK": "4", "CALL": "5", "ENDCALL": "6",
	"POWER": "26", "MENU": "82", "VOLUME_UP": "24", "VOLUME_DOWN": "25",
	"APP_SWITCH": "187", "ENTER": "66", "DEL": "67", "TAB": "61",
	"SPACE": "62", "WAKEUP": "224", "SEARCH": "84", "CAMERA": "27",
	"ESC": "111", "DPAD_UP": "19", "DPAD_DOWN": "20", "DPAD_LEFT": "21",
	"DPAD_RIGHT": "22", "MUTE": "91", "MEDIA_PLAY_PAUSE": "85",
	"MEDIA_NEXT": "87", "MEDIA_PREV": "88", "SETTINGS": "176",
	"BRIGHTNESS_UP": "220", "BRIGHTNESS_DOWN": "221",
}

// Tap 模拟点击。
func (a *Adb) Tap(serial string, x, y int) error {
	_, err := a.Shell(serial, "input", "tap", strconv.Itoa(x), strconv.Itoa(y))
	return err
}

// Swipe 模拟滑动/拖拽，durMS 为毫秒。
func (a *Adb) Swipe(serial string, x1, y1, x2, y2, durMS int) error {
	_, err := a.Shell(serial, "input", "swipe",
		strconv.Itoa(x1), strconv.Itoa(y1), strconv.Itoa(x2), strconv.Itoa(y2), strconv.Itoa(durMS))
	return err
}

// Key 发送命名按键。
func (a *Adb) Key(serial, name string) error {
	code, ok := keyCodes[strings.ToUpper(name)]
	if !ok {
		return fmt.Errorf("未知按键: %s", name)
	}
	_, err := a.Shell(serial, "input", "keyevent", code)
	return err
}

// RawKey 发送原始 keycode（仅接受数字，避免把任意字符串拼进设备 shell）。
func (a *Adb) RawKey(serial, code string) error {
	code = strings.TrimSpace(code)
	if code == "" {
		return fmt.Errorf("keycode 为空")
	}
	for _, r := range code {
		if r < '0' || r > '9' {
			return fmt.Errorf("keycode 只能是数字: %q", code)
		}
	}
	_, err := a.Shell(serial, "input", "keyevent", code)
	return err
}

// Text 输入文本。空格用 %s 表示（input text 的约定），整段用单引号保护设备 shell 元字符。
//
// 说明：adb 是把各参数按空格原样拼接后交给设备 shell 的，不会二次转义
// （官方文档：`adb shell setprop foo 'a b'` 会失败，需要写两层引号）。
// 因此这里必须自己完成一次 shell 引号包裹，把 ' 写成 '\” 才是正确的。
//
// 局限：`input text` 走 KeyCharacterMap，只支持 ASCII 可见字符，
// 中文等非 ASCII 字符会被系统静默丢弃；需要输入中文时请用手机输入法或 Agent。
func (a *Adb) Text(serial, text string) error {
	s := escapeInputText(text)
	if s == "" {
		return fmt.Errorf("文本为空")
	}
	_, err := a.Shell(serial, "input", "text", s)
	return err
}

// escapeInputText 把任意文本转换为可安全放进设备 shell 的形式。
func escapeInputText(text string) string {
	if text == "" {
		return ""
	}
	// 统一换行/制表符为空格：input text 不支持换行，保留会让命令变成多行
	repl := strings.NewReplacer("\r\n", " ", "\r", " ", "\n", " ", "\t", " ", "\x00", "")
	text = repl.Replace(text)

	if utf8.RuneCountInString(text) > MaxTextLen {
		runes := []rune(text)
		text = string(runes[:MaxTextLen])
	}
	text = strings.TrimSpace(text)
	if text == "" {
		return ""
	}
	// 空格 → %s（input text 约定）
	s := strings.ReplaceAll(text, " ", "%s")
	// 单引号包裹，内部 ' 用 '\'' 断开-转义-续接
	s = strings.ReplaceAll(s, "'", `'\''`)
	return "'" + s + "'"
}

// HasNonASCII 判断文本是否含非 ASCII 字符（这类字符 input text 无法输入）。
func HasNonASCII(s string) bool {
	for _, r := range s {
		if r > 0x7F {
			return true
		}
	}
	return false
}

// WakeUp 点亮屏幕。
func (a *Adb) WakeUp(serial string) error {
	_, err := a.Shell(serial, "input", "keyevent", "KEYCODE_WAKEUP")
	return err
}
