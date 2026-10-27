package adb

import (
	"bytes"
	"encoding/binary"
	"fmt"
	"image"
	"image/png"
	"io"
	"os/exec"
)

// pngMagic 是 PNG 文件头。
var pngMagic = []byte{0x89, 'P', 'N', 'G', 0x0D, 0x0A, 0x1A, 0x0A}

// Screenshot 截取当前屏幕。
//
// 返回 image.Image 而不是 *image.RGBA：原来强制转 RGBA 用逐像素 Set() 双重循环，
// 1080p 一帧就要 200 万次接口调用（实测 100ms 以上）且结果并不需要——
// png/jpeg 编码器本身接受任意 image.Image，因此这里直接返回解码结果，省掉整次转换。
func (a *Adb) Screenshot(serial string) (image.Image, error) {
	data, err := a.ExecOut(serial, "screencap", "-p")
	if err != nil {
		return nil, err
	}
	if len(data) > len(pngMagic) && bytes.Equal(data[:len(pngMagic)], pngMagic) {
		img, err := png.Decode(bytes.NewReader(data))
		if err != nil {
			return nil, fmt.Errorf("PNG 解码失败: %v", err)
		}
		return img, nil
	}
	// 部分老旧设备 -p 仍返回原始像素：w(4LE) h(4LE) format(4LE) [colorspace(4LE)] + RGBA
	if img, ok := decodeRawScreencap(data); ok {
		return img, nil
	}
	return nil, fmt.Errorf("无法识别的截图格式（%d 字节）", len(data))
}

// decodeRawScreencap 解析 screencap 的原始像素格式。
// 12 字节头是旧格式，Android 9+ 在 format 后多一个 colorspace，共 16 字节；
// 用「总长度精确匹配」来区分两者，避免把 16 字节头误判成 12 字节。
func decodeRawScreencap(data []byte) (*image.RGBA, bool) {
	if len(data) < 12 {
		return nil, false
	}
	w := int(binary.LittleEndian.Uint32(data[0:4]))
	h := int(binary.LittleEndian.Uint32(data[4:8]))
	format := binary.LittleEndian.Uint32(data[8:12])
	if w <= 0 || h <= 0 || w > 16384 || h > 16384 || format != 1 { // 1 = RGBA_8888
		return nil, false
	}
	size := w * h * 4
	for _, hdr := range []int{12, 16} {
		if len(data) == hdr+size {
			dst := image.NewRGBA(image.Rect(0, 0, w, h))
			copy(dst.Pix, data[hdr:hdr+size])
			return dst, true
		}
	}
	// 长度对不上（可能被截断），宁可报错也不要返回半张图
	return nil, false
}

// StartScreenRecord 启动 screenrecord 输出 H.264 到 stdout。
// bitRateMbps 为码率（Mbps）。screenrecord 单次最长 180 秒，需由调用方检测结束并重启。
func (a *Adb) StartScreenRecord(serial string, bitRateMbps int) (*exec.Cmd, io.ReadCloser, io.ReadCloser, error) {
	return a.StartCmd(serial,
		"screenrecord",
		"--bit-rate", fmt.Sprintf("%dM", bitRateMbps),
		"--time-limit", "180",
		"--output-format", "h264",
		"-",
	)
}
