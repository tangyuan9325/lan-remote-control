//go:build !windows

package inputinject

import "log"

// Injector 在非 Windows 平台上为空实现（输入注入依赖 Win32 API）。
type Injector struct{}

func New() *Injector { return &Injector{} }

func (in *Injector) HandleCommand(cmd map[string]interface{}) {
	log.Printf("[InputInject] unsupported platform, ignoring command %v", cmd["type"])
}

func (in *Injector) Close() {}
