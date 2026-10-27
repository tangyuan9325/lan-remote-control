// Package ffmpeg 定位并驱动 ffmpeg，把 H.264 流解码为**JPEG 帧**直接交给浏览器。
//
// 设计变更（v1.1）：
// 旧实现让 ffmpeg 输出 rawvideo/bgr24，再由 Go 逐字节把 BGR 转成 RGBA（这里原本存在
// 索引错位缺陷），最后在 Go 里做一次 JPEG 编码。1080p 下每帧要在管道里搬运 6.2MB，
// 一次往返就是三遍全帧遍历（3 次内存带宽 + 1 次 JPEG 编码）。
//
// 现在改为 ffmpeg 直接输出 MJPEG（-f mjpeg），Go 端只做 SOI/EOI 切帧，把 JPEG 原样
// 转发给浏览器：
//   - 管道带宽：6.2 MB/帧 → ~0.1 MB/帧（约 60 倍下降）
//   - 去掉 Go 侧 BGR→RGBA 转换与 jpeg.Encode 两次全帧遍历（ffmpeg 侧为 SIMD 多线程）
//   - 不再依赖预先探测的固定宽高，旋转/分辨率变化不会导致帧长度错位
//
// 帧边界识别依据 JPEG 规范：SOI = 0xFFD8，EOI = 0xFFD9。熵编码数据里的 0xFF 会被
// 字节填充成 0xFF00，重启标记为 0xFFD0~0xFFD7，因此扫描 0xFFD9 不会误判。
package ffmpeg

import (
	"bufio"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"sync"
)

const (
	soi0 = 0xFF
	soi1 = 0xD8
	eoi1 = 0xD9

	// maxFrame 单帧上限，防止输出异常时缓冲区无限增长。
	maxFrame = 32 << 20
	// defaultQuality mjpeg 质量（2~31，越小越好），兼顾清晰度与带宽。
	defaultQuality = 5
)

// Find 定位 ffmpeg.exe：FFMPEG_PATH 环境变量 > exe 同目录/third_party > PATH。
func Find() (string, bool) {
	if p := os.Getenv("FFMPEG_PATH"); p != "" && fileExists(p) {
		return p, true
	}
	if exe, err := os.Executable(); err == nil {
		dir := filepath.Dir(exe)
		for _, c := range []string{
			filepath.Join(dir, "ffmpeg.exe"),
			filepath.Join(dir, "third_party", "ffmpeg", "ffmpeg.exe"),
			filepath.Join(dir, "..", "third_party", "ffmpeg", "ffmpeg.exe"),
		} {
			if fileExists(c) {
				return c, true
			}
		}
	}
	if p, err := exec.LookPath("ffmpeg"); err == nil {
		return p, true
	}
	return "", false
}

func fileExists(p string) bool {
	fi, err := os.Stat(p)
	return err == nil && !fi.IsDir()
}

// Decoder 把 H.264 Annex-B 输入流解码成一帧帧 JPEG。
type Decoder struct {
	cmd     *exec.Cmd
	br      *bufio.Reader
	chunk   []byte
	pending []byte

	errMu   sync.Mutex
	lastErr string

	closeOnce sync.Once
}

// NewDecoder 启动 ffmpeg 子进程，输入为 H.264 Annex-B 字节流。
func NewDecoder(path string, input io.Reader) (*Decoder, error) {
	cmd := exec.Command(path,
		"-hide_banner", "-loglevel", "error",
		// 低延迟：尽量少缓冲、尽快吐帧
		// 低延迟：小 probe、立即吐帧。
		// 注意不要加 -fflags nobuffer：在 ffmpeg 8/9 上会让解码器一帧都不输出
		// （"Output file is empty, nothing was encoded"），实测必现。
		"-probesize", "32k", "-analyzeduration", "0",
		"-flags", "low_delay",
		"-f", "h264", "-i", "pipe:0",
		"-an", "-sn", "-dn",
		// 关键：mjpeg 编码器要求 JPEG 的 full-range YUV，而 H.264 录屏是 limited range。
		// 不指定 yuvj420p 时 ffmpeg 会直接报
		// "Non full-range YUV is non-standard" 并放弃输出（实测 ffmpeg 9.0 必现），
		// 表现为一直黑屏。加这一项由 ffmpeg 完成 TV→PC 色域转换。
		"-pix_fmt", "yuvj420p",
		"-f", "mjpeg", "-q:v", fmt.Sprint(defaultQuality),
		"-flush_packets", "1",
		"pipe:1",
	)
	// Windows 下把另一个子进程的 StdoutPipe 直接当本进程 stdin 句柄不可靠，
	// 用 StdinPipe + 拷贝协程桥接。
	ffIn, err := cmd.StdinPipe()
	if err != nil {
		return nil, err
	}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return nil, err
	}
	stderr, err := cmd.StderrPipe()
	if err != nil {
		return nil, err
	}
	d := &Decoder{
		cmd:     cmd,
		br:      bufio.NewReaderSize(stdout, 256*1024),
		chunk:   make([]byte, 128*1024),
		pending: make([]byte, 0, 512*1024),
	}
	if err := cmd.Start(); err != nil {
		return nil, err
	}
	go func() {
		_, _ = io.Copy(ffIn, input)
		_ = ffIn.Close()
	}()
	go d.watchStderr(stderr)
	return d, nil
}

func (d *Decoder) watchStderr(r io.Reader) {
	sc := bufio.NewScanner(r)
	sc.Buffer(make([]byte, 64*1024), 1024*1024)
	for sc.Scan() {
		line := sc.Text()
		if line == "" {
			continue
		}
		d.errMu.Lock()
		d.lastErr = line
		d.errMu.Unlock()
		fmt.Fprintf(os.Stderr, "[ffmpeg] %s\n", line)
	}
}

// LastError 返回最近一次 ffmpeg 报错，便于排查“没有画面”的原因。
func (d *Decoder) LastError() string {
	d.errMu.Lock()
	defer d.errMu.Unlock()
	return d.lastErr
}

// ReadJPEG 读取下一帧 JPEG（含 SOI 与 EOI）。
func (d *Decoder) ReadJPEG() ([]byte, error) {
	for {
		// 对齐到 SOI：丢掉 SOI 之前的所有字节
		if i := indexSOI(d.pending); i > 0 {
			d.pending = d.pending[i:]
		} else if i < 0 {
			d.pending = d.pending[:0]
		}
		if len(d.pending) >= 2 && d.pending[0] == soi0 && d.pending[1] == soi1 {
			if j := indexEOI(d.pending); j >= 0 {
				end := j + 2
				frame := make([]byte, end)
				copy(frame, d.pending[:end])
				d.pending = append(d.pending[:0], d.pending[end:]...)
				return frame, nil
			}
			if len(d.pending) > maxFrame {
				d.pending = d.pending[:0]
				continue
			}
		}
		n, err := d.br.Read(d.chunk)
		if n > 0 {
			d.pending = append(d.pending, d.chunk[:n]...)
			continue
		}
		if err != nil {
			if msg := d.LastError(); msg != "" {
				return nil, fmt.Errorf("%v（ffmpeg: %s）", err, msg)
			}
			return nil, err
		}
	}
}

// indexSOI 返回 SOI（0xFFD8）在 b 中的位置，找不到返回 -1。
func indexSOI(b []byte) int {
	for i := 0; i+1 < len(b); i++ {
		if b[i] == soi0 && b[i+1] == soi1 {
			return i
		}
	}
	return -1
}

// indexEOI 返回从下标 2 起第一个 EOI（0xFFD9）的位置，找不到返回 -1。
func indexEOI(b []byte) int {
	for i := 2; i+1 < len(b); i++ {
		if b[i] == soi0 && b[i+1] == eoi1 {
			return i
		}
	}
	return -1
}

// Close 终止 ffmpeg 进程（可重复调用）。
func (d *Decoder) Close() {
	d.closeOnce.Do(func() {
		if d.cmd != nil && d.cmd.Process != nil {
			_ = d.cmd.Process.Kill()
		}
		if d.cmd != nil {
			_ = d.cmd.Wait()
		}
	})
}
