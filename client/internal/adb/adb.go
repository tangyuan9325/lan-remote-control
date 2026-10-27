// Package adb 封装 ADB 命令行：定位 adb、设备发现、连接/配对、shell 命令等。
package adb

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

// Adb 包装 adb 可执行文件。
type Adb struct {
	Path string
}

// Find 依次从 ADB_PATH 环境变量、PATH、常见安装目录定位 adb.exe。
func Find() (string, error) {
	if p := os.Getenv("ADB_PATH"); p != "" && fileExists(p) {
		return p, nil
	}
	if p, err := exec.LookPath("adb"); err == nil {
		return p, nil
	}
	local := os.Getenv("LOCALAPPDATA")
	user := os.Getenv("USERPROFILE")
	candidates := []string{
		filepath.Join(local, "Android", "Sdk", "platform-tools", "adb.exe"),
		filepath.Join(user, "AppData", "Local", "Android", "Sdk", "platform-tools", "adb.exe"),
		filepath.Join(user, "Android", "Sdk", "platform-tools", "adb.exe"),
		`C:\Android\platform-tools\adb.exe`,
		`C:\Program Files\Android\platform-tools\adb.exe`,
		`C:\platform-tools\adb.exe`,
	}
	for _, c := range candidates {
		if fileExists(c) {
			return c, nil
		}
	}
	return "", errors.New("未找到 adb，请安装 Android platform-tools 或设置 ADB_PATH 环境变量")
}

func fileExists(p string) bool {
	fi, err := os.Stat(p)
	return err == nil && !fi.IsDir()
}

// StartServer 启动 adb 服务端。
func (a *Adb) StartServer() error {
	return a.StartServerWithTimeout(15 * time.Second)
}

// StartServerWithTimeout 带超时启动 adb 服务端，避免僵尸服务导致挂起。
func (a *Adb) StartServerWithTimeout(timeout time.Duration) error {
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, a.Path, "start-server")
	out, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("adb start-server: %s (%v)", strings.TrimSpace(string(out)), err)
	}
	return nil
}

// Version 返回 adb 版本信息。
func (a *Adb) Version() string {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	out, err := exec.CommandContext(ctx, a.Path, "version").CombinedOutput()
	if err != nil {
		return ""
	}
	lines := strings.Split(strings.TrimSpace(string(out)), "\n")
	if len(lines) > 0 {
		return strings.TrimSpace(lines[0])
	}
	return ""
}

// Device 表示一台已发现的设备。
type Device struct {
	Serial    string
	State     string // device / offline / unauthorized / recovery
	Model     string
	Product   string
	Transport string
	IPPort    string // 无线设备的 host:port
}

// IsUsb 判断设备是否为 USB 连接。
func (d Device) IsUsb() bool { return d.IPPort == "" }

// DisplayName 返回适合展示的设备名。
func (d Device) DisplayName() string {
	if d.Model != "" {
		return d.Model
	}
	if d.Product != "" {
		return d.Product
	}
	return d.Serial
}

// runCtx 带超时执行 adb 命令并返回标准输出。
func (a *Adb) runCtx(timeout time.Duration, args ...string) ([]byte, error) {
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	return exec.CommandContext(ctx, a.Path, args...).Output()
}

// Devices 解析 `adb devices -l` 输出。
func (a *Adb) Devices() ([]Device, error) {
	out, err := a.runCtx(10*time.Second, "devices", "-l")
	if err != nil {
		return nil, err
	}
	return parseDevices(string(out)), nil
}

// parseDevices 解析 `adb devices -l` 的文本输出（抽出以便单测）。
func parseDevices(out string) []Device {
	var devs []Device
	sc := bufio.NewScanner(strings.NewReader(out))
	sc.Buffer(make([]byte, 8*1024), 1024*1024)
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		// 已 TrimSpace，原先 "strings.HasPrefix(line, \" \")" 分支恒为 false（死代码），
		// 真正需要跳过的是 adb 的横幅行、守护进程提示行与空行。
		if line == "" || strings.HasPrefix(line, "List of devices") ||
			strings.HasPrefix(line, "*") || strings.HasPrefix(line, "adb ") {
			continue
		}
		fields := strings.Fields(line)
		if len(fields) < 2 {
			continue
		}
		d := Device{Serial: fields[0], State: fields[1]}
		if strings.Contains(d.Serial, ":") {
			d.IPPort = d.Serial
		}
		for _, f := range fields[2:] {
			if i := strings.Index(f, ":"); i > 0 {
				k, v := f[:i], f[i+1:]
				switch k {
				case "model":
					d.Model = v
				case "product":
					d.Product = v
				case "transport_id":
					d.Transport = v
				}
			}
		}
		devs = append(devs, d)
	}
	return devs
}

// Connect 通过 IP:端口 无线连接设备。
func (a *Adb) Connect(hostPort string) (string, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	out, err := exec.CommandContext(ctx, a.Path, "connect", hostPort).CombinedOutput()
	return strings.TrimSpace(string(out)), err
}

// Disconnect 断开无线设备。
func (a *Adb) Disconnect(hostPort string) (string, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	out, err := exec.CommandContext(ctx, a.Path, "disconnect", hostPort).CombinedOutput()
	return strings.TrimSpace(string(out)), err
}

// Pair 使用无线调试配对码配对（Android 11+）。
func (a *Adb) Pair(hostPort, code string) (string, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	out, err := exec.CommandContext(ctx, a.Path, "pair", hostPort, code).CombinedOutput()
	return strings.TrimSpace(string(out)), err
}

// Forward 建立本地到设备的端口转发。local/remote 形如 tcp:8585。
func (a *Adb) Forward(serial, local, remote string) error {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	out, err := exec.CommandContext(ctx, a.Path, "-s", serial, "forward", local, remote).CombinedOutput()
	if err != nil {
		return fmt.Errorf("adb forward: %s (%v)", strings.TrimSpace(string(out)), err)
	}
	return nil
}

// ForwardRemove 移除一条端口转发。
func (a *Adb) ForwardRemove(serial, local string) error {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	return exec.CommandContext(ctx, a.Path, "-s", serial, "forward", "--remove", local).Run()
}

// Shell 在设备上执行 shell 命令，返回去掉首尾空白的标准输出。
func (a *Adb) Shell(serial string, args ...string) (string, error) {
	full := append([]string{"-s", serial, "shell"}, args...)
	out, err := a.runCtx(30*time.Second, full...)
	return strings.TrimSpace(string(out)), err
}

// ShellOutput 返回原始标准输出字节。
func (a *Adb) ShellOutput(serial string, args ...string) ([]byte, error) {
	full := append([]string{"-s", serial, "shell"}, args...)
	return a.runCtx(30*time.Second, full...)
}

// ExecOut 执行 exec-out（二进制安全输出）。
func (a *Adb) ExecOut(serial string, args ...string) ([]byte, error) {
	full := append([]string{"-s", serial, "exec-out"}, args...)
	return a.runCtx(30*time.Second, full...)
}

// ShellScript 在设备上执行一段多行 shell 脚本，整个过程只启动 1 个 adb 进程。
//
// Windows 上每次启动 adb.exe 约 80~250ms，原来 Info/Refresher 分别要串行启动
// 9 个与 4 个进程，单次刷新耗时可达 1~2 秒；合并成一次调用后只剩一次进程开销。
//
// 关于转义：adb 把各参数用空格原样拼接后交给设备 shell（官方文档说明它不像 ssh
// 那样二次转义），所以这里把整段脚本作为单个参数传入即可，脚本内容全部由本程序
// 生成，不含外部输入。
func (a *Adb) ShellScript(serial, script string, timeout time.Duration) (string, error) {
	full := []string{"-s", serial, "shell", script}
	out, err := a.runCtx(timeout, full...)
	return string(out), err
}

// ParseSections 解析 ShellScript 输出里的分段结果：
//
//	@@KEY@@
//	value（可多行）
//	@@NEXT@@
//	...
//
// 用标记而不是固定行号，可避免某个命令无输出时后续字段整体错位。
func ParseSections(raw string) map[string]string {
	out := make(map[string]string, 12)
	key := ""
	var buf []string

	flush := func() {
		if key == "" {
			return
		}
		out[key] = strings.TrimSpace(strings.Join(buf, "\n"))
		buf = buf[:0]
	}
	for _, line := range strings.Split(raw, "\n") {
		t := strings.TrimSpace(line)
		if len(t) > 4 && strings.HasPrefix(t, "@@") && strings.HasSuffix(t, "@@") {
			flush()
			key = t[2 : len(t)-2]
			continue
		}
		if key != "" {
			buf = append(buf, t)
		}
	}
	flush()
	return out
}

// StartCmd 启动一条 exec-out 流式命令，返回 cmd 与 stdout/stderr 管道。
func (a *Adb) StartCmd(serial string, args ...string) (*exec.Cmd, io.ReadCloser, io.ReadCloser, error) {
	full := append([]string{"-s", serial, "exec-out"}, args...)
	cmd := exec.Command(a.Path, full...)
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return nil, nil, nil, err
	}
	stderr, err := cmd.StderrPipe()
	if err != nil {
		return nil, nil, nil, err
	}
	if err := cmd.Start(); err != nil {
		return nil, nil, nil, err
	}
	return cmd, stdout, stderr, nil
}
