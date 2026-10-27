package adb

import (
	"encoding/binary"
	"testing"
)

// buildRaw 构造 screencap 的原始像素数据（RGBA_8888）。
func buildRaw(w, h, header int) []byte {
	size := w * h * 4
	buf := make([]byte, header+size)
	binary.LittleEndian.PutUint32(buf[0:4], uint32(w))
	binary.LittleEndian.PutUint32(buf[4:8], uint32(h))
	binary.LittleEndian.PutUint32(buf[8:12], 1) // RGBA_8888
	for i := 0; i < size; i++ {
		buf[header+i] = byte(i)
	}
	return buf
}

func TestDecodeRawScreencap12(t *testing.T) {
	img, ok := decodeRawScreencap(buildRaw(4, 3, 12))
	if !ok {
		t.Fatal("12 字节头解析失败")
	}
	if img.Bounds().Dx() != 4 || img.Bounds().Dy() != 3 {
		t.Errorf("尺寸错误: %v", img.Bounds())
	}
	if img.Pix[0] != 0 || img.Pix[1] != 1 || img.Pix[2] != 2 || img.Pix[3] != 3 {
		t.Errorf("像素偏移错误: %v", img.Pix[:4])
	}
}

func TestDecodeRawScreencap16(t *testing.T) {
	// Android 9+ 的 16 字节头（多一个 colorspace 字段）
	img, ok := decodeRawScreencap(buildRaw(2, 2, 16))
	if !ok {
		t.Fatal("16 字节头解析失败")
	}
	if img.Bounds().Dx() != 2 || img.Bounds().Dy() != 2 {
		t.Errorf("尺寸错误: %v", img.Bounds())
	}
	if img.Pix[0] != 0 {
		t.Errorf("16 字节头未跳过，首个像素 = %d", img.Pix[0])
	}
}

func TestDecodeRawScreencapRejects(t *testing.T) {
	// 数据被截断：长度既不符合 12 也不符合 16 字节头，必须拒绝而不是返回半张图
	full := buildRaw(10, 10, 12)
	if _, ok := decodeRawScreencap(full[:len(full)-100]); ok {
		t.Error("截断数据应被拒绝")
	}
	// 非 RGBA_8888 格式
	bad := buildRaw(2, 2, 12)
	binary.LittleEndian.PutUint32(bad[8:12], 5)
	if _, ok := decodeRawScreencap(bad); ok {
		t.Error("非 RGBA_8888 格式应被拒绝")
	}
	// 过小尺寸
	if _, ok := decodeRawScreencap([]byte{1, 2, 3}); ok {
		t.Error("过短数据应被拒绝")
	}
	// 超大尺寸（防整数溢出/内存爆炸）
	w := buildRaw(1, 1, 12)
	binary.LittleEndian.PutUint32(w[0:4], 99999)
	if _, ok := decodeRawScreencap(w); ok {
		t.Error("超大尺寸应被拒绝")
	}
}
