// ADB 远程控制台 - Windows 客户端
//
// 本地 Web 控制台服务：通过 ADB 发现并远程控制多台 Android 设备。
// 启动后自动在浏览器打开控制台界面。
package main

import (
	"flag"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"remote-control/client/internal/server"
)

func main() {
	showVer := flag.Bool("version", false, "打印版本号并退出")
	smoke := flag.Bool("smoke", false, "自检：启动服务并请求 /api/status，成功后退出")
	nobrowser := flag.Bool("nobrowser", false, "不自动打开浏览器")
	adbPath := flag.String("adb", "", "指定 adb.exe 路径（覆盖配置）")
	ffmpegPath := flag.String("ffmpeg", "", "指定 ffmpeg.exe 路径（覆盖配置）")
	port := flag.Int("port", 0, "本地控制台端口（默认 8500）")
	flag.Parse()

	if *showVer {
		fmt.Println("ADB 远程控制台 v" + server.Version)
		return
	}

	cfg := server.LoadConfig()
	if *adbPath != "" {
		cfg.AdbPath = *adbPath
	}
	if *ffmpegPath != "" {
		cfg.FFmpegPath = *ffmpegPath
	}
	if *port != 0 {
		if *port < 1 || *port > 65535 {
			fmt.Fprintln(os.Stderr, "端口不合法：应在 1~65535 之间")
			os.Exit(2)
		}
		cfg.Port = *port
	}

	srv, err := server.New(cfg, *smoke, *nobrowser)
	if err != nil {
		fmt.Fprintln(os.Stderr, "初始化失败:", err)
		os.Exit(1)
	}

	errCh := make(chan error, 1)
	go func() { errCh <- srv.Run() }()

	if *smoke {
		os.Exit(runSmoke(srv, errCh))
	}

	// 优雅退出：关闭会话、回收 ffmpeg / screenrecord 子进程，
	// 避免关闭控制台后手机上仍在录屏、电脑上残留 ffmpeg.exe。
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, os.Interrupt, syscall.SIGTERM)

	select {
	case err := <-errCh:
		if err != nil {
			fmt.Fprintln(os.Stderr, "服务启动失败:", err)
			srv.Shutdown()
			os.Exit(1)
		}
	case s := <-sigCh:
		fmt.Printf("\n收到 %v，正在退出并回收子进程…\n", s)
	}
	srv.Shutdown()
}

// runSmoke 自检：等 HTTP 起来后请求 /api/status，确认服务可用。
func runSmoke(srv *server.Server, errCh chan error) int {
	deadline := time.Now().Add(6 * time.Second)
	for time.Now().Before(deadline) {
		select {
		case err := <-errCh:
			if err != nil {
				fmt.Fprintln(os.Stderr, "自检失败（服务启动）:", err)
				return 1
			}
			fmt.Fprintln(os.Stderr, "自检失败：服务提前退出")
			return 1
		default:
		}
		resp, err := http.Get(srv.URL() + "/api/status")
		if err == nil {
			body, _ := io.ReadAll(resp.Body)
			resp.Body.Close()
			if resp.StatusCode == http.StatusOK {
				fmt.Printf("自检通过: %s\n%s\n", srv.URL(), string(body))
				srv.Shutdown()
				return 0
			}
		}
		time.Sleep(300 * time.Millisecond)
	}
	fmt.Fprintln(os.Stderr, "自检失败：6 秒内未能访问 /api/status")
	srv.Shutdown()
	return 1
}
