package adb

import (
	"strings"
	"testing"
)

func TestEscapeInputText(t *testing.T) {
	cases := []struct {
		name string
		in   string
		want string
	}{
		{"普通文本", "hello", "'hello'"},
		{"空格转 %s", "hello world", "'hello%sworld'"},
		{"单引号转义", "it's", `'it'\''s'`},
		{"空格+单引号", "a b'c", `'a%sb'\''c'`},
		// 关键：这些 shell 元字符必须被包在单引号内，不能让设备 shell 解释
		{"命令分隔符", "a;reboot", "'a;reboot'"},
		{"命令替换", "$(reboot)", "'$(reboot)'"},
		{"反引号", "`reboot`", "'`reboot`'"},
		{"管道", "a|b", "'a|b'"},
		{"重定向", "a>b", "'a>b'"},
		{"与号", "a&&b", "'a&&b'"},
		{"换行归一为空格", "a\nb", "'a%sb'"},
		{"制表符归一为空格", "a\tb", "'a%sb'"},
		{"回车换行", "a\r\nb", "'a%sb'"},
		{"空字符串", "", ""},
		{"纯空白", "   ", ""},
	}
	for _, c := range cases {
		if got := escapeInputText(c.in); got != c.want {
			t.Errorf("%s: escapeInputText(%q) = %q，期望 %q", c.name, c.in, got, c.want)
		}
	}
}

// 注入防护的核心断言：转义结果中，任何未成对的单引号都不允许出现，
// 否则攻击者可以提前闭合引号并追加自己的命令。
func TestEscapeInputTextNoInjection(t *testing.T) {
	payloads := []string{
		"'; reboot; '",
		"'; rm -rf /sdcard; '",
		`'; su -c reboot; '`,
		"$(reboot)",
		"`reboot`",
	}
	for _, p := range payloads {
		got := escapeInputText(p)
		if !strings.HasPrefix(got, "'") || !strings.HasSuffix(got, "'") {
			t.Errorf("结果未被单引号包裹: %q → %q", p, got)
		}
		// 去掉内部的 '\'' 标准化序列后，剩余部分不能再有单引号
		normalized := strings.ReplaceAll(got, `'\''`, "")
		if strings.Count(normalized, "'") != 2 {
			t.Errorf("存在未转义的单引号: %q → %q", p, got)
		}
	}
}

func TestEscapeInputTextLengthCap(t *testing.T) {
	long := strings.Repeat("a", MaxTextLen+500)
	got := escapeInputText(long)
	// 去掉首尾引号后应等于 MaxTextLen
	if len(got) != MaxTextLen+2 {
		t.Errorf("超长文本应被截断到 %d，实际 %d", MaxTextLen, len(got)-2)
	}
}

func TestHasNonASCII(t *testing.T) {
	if HasNonASCII("hello") {
		t.Error("纯 ASCII 误判")
	}
	if !HasNonASCII("你好") {
		t.Error("中文应判定为非 ASCII")
	}
	if !HasNonASCII("a1!中") {
		t.Error("混合内容应判定为非 ASCII")
	}
}

func TestRawKeyValidation(t *testing.T) {
	a := &Adb{Path: "adb-not-exist-for-test"}
	// 非数字必须被拒绝，且不能执行到启动进程
	if err := a.RawKey("serial", "3;reboot"); err == nil {
		t.Error("非数字 keycode 应被拒绝")
	}
	if err := a.RawKey("serial", ""); err == nil {
		t.Error("空 keycode 应被拒绝")
	}
}
