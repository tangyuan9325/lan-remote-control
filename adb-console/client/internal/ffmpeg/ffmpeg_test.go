package ffmpeg

import (
	"bufio"
	"bytes"
	"io"
	"testing"
)

// fakeJPEG 造一个最小的合法边界结构：SOI + payload + EOI
func fakeJPEG(payload []byte) []byte {
	out := []byte{0xFF, 0xD8}
	out = append(out, payload...)
	return append(out, 0xFF, 0xD9)
}

func newTestDecoder(r io.Reader) *Decoder {
	return &Decoder{
		br:      bufio.NewReaderSize(r, 64),
		chunk:   make([]byte, 8),
		pending: make([]byte, 0, 64),
	}
}

func TestIndexMarkers(t *testing.T) {
	soi := []byte{0x00, 0xFF, 0xD8, 0x11}
	if got := indexSOI(soi); got != 1 {
		t.Errorf("indexSOI = %d，期望 1", got)
	}
	if got := indexSOI([]byte{0x11, 0x22}); got != -1 {
		t.Errorf("无 SOI 时应返回 -1，实际 %d", got)
	}

	eoi := []byte{0xFF, 0xD8, 0x11, 0xFF, 0xD9, 0x22}
	if got := indexEOI(eoi); got != 3 {
		t.Errorf("indexEOI = %d，期望 3", got)
	}
	// 熵编码里的字节填充 FF 00 不应被当成 EOI
	stuffed := []byte{0xFF, 0xD8, 0xFF, 0x00, 0x13, 0xFF, 0xD9}
	if got := indexEOI(stuffed); got != 5 {
		t.Errorf("带字节填充时 indexEOI = %d，期望 5", got)
	}
	// 重启标记 FF D0~D7 也不是 EOI
	restart := []byte{0xFF, 0xD8, 0xFF, 0xD0, 0xFF, 0xD1, 0xFF, 0xD9}
	if got := indexEOI(restart); got != 6 {
		t.Errorf("带重启标记时 indexEOI = %d，期望 6", got)
	}
}

func TestReadJPEGTwoFrames(t *testing.T) {
	f1 := fakeJPEG([]byte("frame-1"))
	f2 := fakeJPEG([]byte("frame-2"))
	// 前导垃圾字节模拟流从中间开始
	stream := append([]byte("garbage-bytes"), f1...)
	stream = append(stream, f2...)

	d := newTestDecoder(bytes.NewReader(stream))

	got1, err := d.ReadJPEG()
	if err != nil {
		t.Fatalf("第一帧读取失败: %v", err)
	}
	if !bytes.Equal(got1, f1) {
		t.Errorf("第一帧内容不符: %x", got1)
	}
	got2, err := d.ReadJPEG()
	if err != nil {
		t.Fatalf("第二帧读取失败: %v", err)
	}
	if !bytes.Equal(got2, f2) {
		t.Errorf("第二帧内容不符: %x", got2)
	}
	if _, err := d.ReadJPEG(); err == nil {
		t.Error("流结束后应返回错误")
	}
}

// 帧被拆到多次 Read 之间也必须正确重组（管道读取的常态）
func TestReadJPEGAcrossChunks(t *testing.T) {
	frame := fakeJPEG(bytes.Repeat([]byte{0xAB}, 100))
	// chunk 只有 8 字节，一帧会被拆成十几次 Read
	d := newTestDecoder(bytes.NewReader(frame))
	got, err := d.ReadJPEG()
	if err != nil {
		t.Fatalf("跨块读取失败: %v", err)
	}
	if !bytes.Equal(got, frame) {
		t.Errorf("跨块重组结果不符，长度 got=%d want=%d", len(got), len(frame))
	}
}

func TestReadJPEGSkipsIncompleteTail(t *testing.T) {
	// 只有 SOI 没有 EOI：应读不到帧并返回 EOF 错误，而不是返回半帧
	d := newTestDecoder(bytes.NewReader([]byte{0xFF, 0xD8, 0x01, 0x02, 0x03}))
	if _, err := d.ReadJPEG(); err == nil {
		t.Error("不完整帧应返回错误")
	}
}

func TestCloseWithoutProcess(t *testing.T) {
	// Close 必须可重复调用且不 panic（Stop 与 streamX 的 defer 会各调一次）
	d := &Decoder{}
	d.Close()
	d.Close()
}
