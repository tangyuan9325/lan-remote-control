//go:build windows

package server

const (
	shellProg = "cmd"
	shellFlag = "/C" // cmd.exe handles builtins like dir/cd (rc14 parity)
)

func isWindows() bool { return true }
