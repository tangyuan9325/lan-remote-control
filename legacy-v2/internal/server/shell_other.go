//go:build !windows

package server

const (
	shellProg = "/bin/sh"
	shellFlag = "-c"
)

func isWindows() bool { return false }
