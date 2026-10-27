package ffmpeg

import (
	"bytes"
	"image/jpeg"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
)

// findFFmpegForTest 在仓库内定位附赠的 ffmpeg，找不到则跳过集成测试。
func findFFmpegForTest(t *testing.T) string {
	t.Helper()
	candidates := []string{
		filepath.Join("..", "..", "third_party", "ffmpeg", "ffmpeg.exe"),
		filepath.Join("..", "..", "..", "client", "third_party", "ffmpeg", "ffmpeg.exe"),
	}
	if p := os.Getenv("FFMPEG_PATH"); p != "" {
		candidates = append([]string{p}, candidates...)
	}
	for _, c := range candidates {
		if _, err := os.Stat(c); err == nil {
			return c
		}
	}
	if p, err := exec.LookPath("ffmpeg"); err == nil {
		return p
	}
	t.Skip("未找到 ffmpeg，跳过解码链路集成测试")
	return ""
}

// TestPipelineH264ToJPEG 端到端验证：
// 真实 H.264 码流 → ffmpeg(MJPEG) → SOI/EOI 切帧 → 每一帧都能被 Go 解码为正确尺寸的 JPEG。
//
// 这条测试正是取代旧实现的依据：旧链路输出 bgr24 并由 Go 逐像素转换（且存在索引错位缺陷），
// 新链路直接把 ffmpeg 编码好的 JPEG 交给浏览器，这里同时验证「帧完整」与「尺寸正确」。
func TestPipelineH264ToJPEG(t *testing.T) {
	ffmpegPath := findFFmpegForTest(t)
	dir := t.TempDir()
	h264Path := filepath.Join(dir, "test.h264")

	// 生成 1 秒 10fps 的 160x120 测试画面
	gen := exec.Command(ffmpegPath,
		"-y", "-hide_banner", "-loglevel", "error",
		"-f", "lavfi", "-i", "testsrc=size=160x120:rate=10",
		"-t", "1", "-pix_fmt", "yuv420p", "-c:v", "libx264",
		"-f", "h264", h264Path,
	)
	if out, err := gen.CombinedOutput(); err != nil {
		t.Skipf("生成测试码流失败（跳过）: %v %s", err, out)
	}

	f, err := os.Open(h264Path)
	if err != nil {
		t.Fatalf("打开码流失败: %v", err)
	}
	defer f.Close()

	dec, err := NewDecoder(ffmpegPath, f)
	if err != nil {
		t.Fatalf("启动解码器失败: %v", err)
	}
	defer dec.Close()

	frames := 0
	totalBytes := 0
	for frames < 30 {
		data, err := dec.ReadJPEG()
		if err != nil {
			break
		}
		if len(data) < 4 || !bytes.HasPrefix(data, []byte{0xFF, 0xD8}) || !bytes.HasSuffix(data, []byte{0xFF, 0xD9}) {
			t.Fatalf("第 %d 帧不是完整的 JPEG（len=%d）", frames, len(data))
		}
		img, err := jpeg.Decode(bytes.NewReader(data))
		if err != nil {
			t.Fatalf("第 %d 帧无法解码: %v", frames, err)
		}
		if img.Bounds().Dx() != 160 || img.Bounds().Dy() != 120 {
			t.Fatalf("第 %d 帧尺寸错误: %v", frames, img.Bounds())
		}
		frames++
		totalBytes += len(data)
	}
	if frames < 8 {
		t.Fatalf("只解出 %d 帧，期望至少 8 帧", frames)
	}
	t.Logf("成功解码 %d 帧，每帧平均 %d 字节（等量 rawvideo 为 %d 字节，约 %.0f 倍）",
		frames, totalBytes/frames, 160*120*3, float64(160*120*3)/float64(totalBytes/frames))
}
