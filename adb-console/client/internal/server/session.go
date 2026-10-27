package server

import (
	"bytes"
	"errors"
	"fmt"
	"image/jpeg"
	"image/png"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"remote-control/client/internal/adb"
	"remote-control/client/internal/agent"
	"remote-control/client/internal/ffmpeg"
)

const (
	// jpegQuality PNG 截图模式下由 Go 编码 JPEG 的质量。
	jpegQuality = 72
	// firstFrameTimeout 首帧超时：超时说明设备没出画面（多为屏幕熄灭），自动降级 PNG。
	firstFrameTimeout = 8 * time.Second
	// pngInterval PNG 截图模式的采集间隔。
	pngInterval = 250 * time.Millisecond
)

// Subscriber 是一个视频流订阅者（每个浏览器标签页一个）。
// 每帧只在队列里保留最新一份，慢客户端不会拖慢其它客户端。
type Subscriber struct {
	Frames chan []byte
	closed chan struct{}
	once   sync.Once
}

// Close 取消订阅。
func (s *Subscriber) Close() {
	s.once.Do(func() { close(s.closed) })
}

// Session 管理一台设备的会话：视频流、输入注入、信息刷新与帧分发。
type Session struct {
	serial     string
	model      string
	logFn      func(format string, args ...interface{})
	ffmpegPath string
	adb        *adb.Adb

	// 视频/输入模式：被 WebSocket 协程写、被视频协程读，必须加锁
	modeMu    sync.RWMutex
	videoMode string
	inputMode string

	// 设备信息：被 infoLoop 写、被 HTTP 处理协程读
	infoMu sync.RWMutex
	info   adb.DeviceInfo

	stop    chan struct{}
	done    chan struct{}
	restart chan struct{}

	curMu  sync.Mutex
	curDec *ffmpeg.Decoder
	curCmd *exec.Cmd

	ctrlMu     sync.Mutex
	ctrl       *agent.Control
	ctrlLocal  int
	videoLocal int

	// 订阅者集合：原实现每个 Session 只保存一个 broadcast 回调，
	// 打开第二个标签页会把第一个的覆盖掉，关掉任一个又把它整体置空。
	subsMu sync.RWMutex
	subs   map[*Subscriber]struct{}
	nextID int

	fpsCount    int32
	fpsSnapshot int32
	startedAt   time.Time
}

// NewSession 创建设备会话。
func NewSession(a *adb.Adb, ffmpegPath string, serial, model string, info adb.DeviceInfo) *Session {
	return &Session{
		serial:     serial,
		model:      model,
		info:       info,
		ffmpegPath: ffmpegPath,
		adb:        a,
		stop:       make(chan struct{}),
		done:       make(chan struct{}),
		restart:    make(chan struct{}, 1),
		subs:       make(map[*Subscriber]struct{}),
		startedAt:  time.Now(),
	}
}

// Start 启动视频与信息循环。
func (s *Session) Start() {
	go s.videoLoop()
	go s.infoLoop()
	go s.fpsLoop()
}

// fpsLoop 每秒把帧计数换算成帧率快照。
// 原实现由每个 WebSocket 各自调用 FPS() 清零计数，多开标签页时互相“偷”帧，
// 导致显示的 fps 忽高忽低甚至为 0。
func (s *Session) fpsLoop() {
	tick := time.NewTicker(time.Second)
	defer tick.Stop()
	for {
		select {
		case <-s.stop:
			return
		case <-tick.C:
			atomic.StoreInt32(&s.fpsSnapshot, atomic.SwapInt32(&s.fpsCount, 0))
		}
	}
}

// Stop 停止会话并释放子进程。
func (s *Session) Stop() {
	select {
	case <-s.stop:
	default:
		close(s.stop)
	}
	s.killCurrent()
	select {
	case <-s.done:
	case <-time.After(3 * time.Second):
	}
	// 关闭所有订阅者，避免 WebSocket 协程永久等待
	s.subsMu.Lock()
	for sub := range s.subs {
		sub.Close()
	}
	s.subs = make(map[*Subscriber]struct{})
	s.subsMu.Unlock()
}

// ---- 订阅 ----

// Subscribe 注册一个视频订阅者。
func (s *Session) Subscribe() *Subscriber {
	sub := &Subscriber{
		Frames: make(chan []byte, 1),
		closed: make(chan struct{}),
	}
	s.subsMu.Lock()
	if s.subs == nil {
		s.subs = make(map[*Subscriber]struct{})
	}
	s.subs[sub] = struct{}{}
	s.nextID++
	s.subsMu.Unlock()
	return sub
}

// Unsubscribe 注销订阅者。
func (s *Session) Unsubscribe(sub *Subscriber) {
	s.subsMu.Lock()
	delete(s.subs, sub)
	s.subsMu.Unlock()
	sub.Close()
}

// SubscriberCount 返回当前订阅者数量。
func (s *Session) SubscriberCount() int {
	s.subsMu.RLock()
	defer s.subsMu.RUnlock()
	return len(s.subs)
}

// pushFrame 把一帧 JPEG 分发给所有订阅者；队列满则丢弃旧帧，保证看到的永远是最新画面。
func (s *Session) pushFrame(jpegBytes []byte) {
	atomic.AddInt32(&s.fpsCount, 1)
	s.subsMu.RLock()
	subs := make([]*Subscriber, 0, len(s.subs))
	for sub := range s.subs {
		subs = append(subs, sub)
	}
	s.subsMu.RUnlock()
	for _, sub := range subs {
		select {
		case sub.Frames <- jpegBytes:
		default:
			// 丢掉积压的旧帧再放新帧：低延迟优先于完整帧序列
			select {
			case <-sub.Frames:
			default:
			}
			select {
			case sub.Frames <- jpegBytes:
			default:
			}
		}
	}
}

// ---- 模式 ----

// SetVideoMode 设置视频模式并触发重启。
func (s *Session) SetVideoMode(mode string) {
	s.modeMu.Lock()
	changed := s.videoMode != mode
	s.videoMode = mode
	s.modeMu.Unlock()
	if changed {
		s.signalRestart()
	}
}

// VideoMode 返回当前视频模式。
func (s *Session) VideoMode() string {
	s.modeMu.RLock()
	defer s.modeMu.RUnlock()
	return s.videoMode
}

// SetInputMode 设置输入模式。
func (s *Session) SetInputMode(mode string) {
	s.modeMu.Lock()
	s.inputMode = mode
	s.modeMu.Unlock()
}

// InputMode 返回当前输入模式。
func (s *Session) InputMode() string {
	s.modeMu.RLock()
	defer s.modeMu.RUnlock()
	return s.inputMode
}

func (s *Session) signalRestart() {
	select {
	case s.restart <- struct{}{}:
	default:
	}
}

func (s *Session) killCurrent() {
	s.curMu.Lock()
	dec, cmd := s.curDec, s.curCmd
	s.curDec, s.curCmd = nil, nil
	s.curMu.Unlock()
	if dec != nil {
		dec.Close()
	}
	if cmd != nil && cmd.Process != nil {
		_ = cmd.Process.Kill()
	}
}

func (s *Session) setCurrent(dec *ffmpeg.Decoder, cmd *exec.Cmd) {
	s.curMu.Lock()
	s.curDec, s.curCmd = dec, cmd
	s.curMu.Unlock()
}

// ---- 视频循环 ----

func (s *Session) videoLoop() {
	defer close(s.done)
	for {
		select {
		case <-s.stop:
			return
		default:
		}
		switch s.VideoMode() {
		case "agent":
			s.streamAgent()
		case "record":
			s.streamRecord()
		default:
			s.streamPNG()
		}
		select {
		case <-s.stop:
			return
		case <-s.restart:
			// 模式切换/方向变化：立即重启，不再固定等待
		case <-time.After(400 * time.Millisecond):
			// 流自然结束（screenrecord 180s 上限、设备休眠等）：短暂退避后重启
		}
	}
}

func (s *Session) streamPNG() {
	if s.adb == nil {
		return
	}
	tick := time.NewTicker(pngInterval)
	defer tick.Stop()
	for {
		select {
		case <-s.stop:
			return
		case <-s.restart:
			return
		case <-tick.C:
		}
		if s.SubscriberCount() == 0 {
			continue // 没人看就不截图，省掉一次 adb exec-out
		}
		img, err := s.adb.Screenshot(s.serial)
		if err != nil {
			s.logf("截图失败: %v", err)
			continue
		}
		var buf bytes.Buffer
		if err := jpeg.Encode(&buf, img, &jpeg.Options{Quality: jpegQuality}); err != nil {
			s.logf("JPEG 编码失败: %v", err)
			continue
		}
		s.pushFrame(buf.Bytes())
	}
}

func (s *Session) streamRecord() {
	cmd, stdout, stderr, err := s.adb.StartScreenRecord(s.serial, 8)
	if err != nil {
		s.logf("启动 screenrecord 失败: %v", err)
		return
	}
	go drainLogFirst(stderr, s.serial)
	dec, err := ffmpeg.NewDecoder(s.ffmpegPath, stdout)
	if err != nil {
		killProc(cmd)
		s.logf("ffmpeg 启动失败: %v", err)
		return
	}
	s.setCurrent(dec, cmd)
	defer s.killCurrent()
	s.logf("录屏流已启动")

	s.pumpFrames(dec, "录屏流")
}

func (s *Session) streamAgent() {
	s.ctrlMu.Lock()
	if s.videoLocal == 0 {
		s.videoLocal = agent.FreePort()
	}
	local := fmt.Sprintf("tcp:%d", s.videoLocal)
	s.ctrlMu.Unlock()

	_ = s.adb.ForwardRemove(s.serial, local)
	if err := s.adb.Forward(s.serial, local, "tcp:8585"); err != nil {
		s.logf("agent 端口转发失败: %v", err)
		return
	}
	vc, err := agent.ConnectVideo(fmt.Sprintf("127.0.0.1:%d", s.videoLocal))
	if err != nil {
		s.logf("Agent 视频服务不可用（需在手机上点“开始串流”）: %v", err)
		return
	}
	defer vc.Close()
	dec, err := ffmpeg.NewDecoder(s.ffmpegPath, vc.NewAnnexBReader())
	if err != nil {
		s.logf("ffmpeg 启动失败: %v", err)
		return
	}
	s.setCurrent(dec, nil)
	defer s.killCurrent()
	s.logf("Agent 串流已启动")

	s.pumpFrames(dec, "Agent 串流")
}

// pumpFrames 循环取帧并分发；首帧超时则判定该通道不可用并降级 PNG。
func (s *Session) pumpFrames(dec *ffmpeg.Decoder, label string) {
	// 首帧单独加超时：屏幕熄灭时 screenrecord 可能一个字节都不吐，
	// 没有超时就会永远卡在阻塞读上，也不会触发降级。
	first, err := s.readFirstFrame(dec)
	if err != nil {
		s.logf("%s未产出画面（屏幕可能已熄灭或设备不支持），自动切换 PNG 截图；可在界面手动切回", label)
		s.SetVideoMode("png")
		return
	}
	s.pushFrame(first)

	for {
		jpegBytes, err := dec.ReadJPEG()
		if err != nil {
			select {
			case <-s.stop:
				return
			case <-s.restart:
				return
			default:
			}
			s.logf("%s结束: %v", label, err)
			return
		}
		select {
		case <-s.stop:
			return
		case <-s.restart:
			return
		default:
		}
		s.pushFrame(jpegBytes)
	}
}

// readFirstFrame 带超时读取首帧。
func (s *Session) readFirstFrame(dec *ffmpeg.Decoder) ([]byte, error) {
	type result struct {
		data []byte
		err  error
	}
	ch := make(chan result, 1)
	go func() {
		data, err := dec.ReadJPEG()
		ch <- result{data, err}
	}()
	select {
	case r := <-ch:
		return r.data, r.err
	case <-time.After(firstFrameTimeout):
		// 杀掉 ffmpeg 以解除阻塞读，读协程拿到错误后会写入带缓冲的 ch，不会泄漏
		s.killCurrent()
		select {
		case r := <-ch:
			if r.err == nil {
				return r.data, nil
			}
		case <-time.After(2 * time.Second):
		}
		return nil, errors.New("首帧超时")
	case <-s.stop:
		s.killCurrent()
		return nil, errors.New("会话已停止")
	}
}

// ---- 设备信息 ----

func (s *Session) infoLoop() {
	if s.adb == nil {
		return
	}
	tick := time.NewTicker(5 * time.Second)
	defer tick.Stop()
	for {
		select {
		case <-s.stop:
			return
		case <-tick.C:
		}
		s.infoMu.Lock()
		prev := s.info.Orientation
		s.adb.Refresher(s.serial, &s.info)
		cur := s.info.Orientation
		s.infoMu.Unlock()

		// 方向变化会让录屏流的分辨率与坐标映射同时失效，必须重建
		if cur != prev {
			s.logf("屏幕方向变化 %d° → %d°，重启视频流", prev, cur)
			s.signalRestart()
		}
	}
}

// Info 返回设备信息副本。
func (s *Session) Info() adb.DeviceInfo {
	s.infoMu.RLock()
	defer s.infoMu.RUnlock()
	return s.info
}

// Size 返回当前屏幕宽高。
func (s *Session) Size() (int, int) {
	s.infoMu.RLock()
	defer s.infoMu.RUnlock()
	return s.info.Width, s.info.Height
}

// ---- 输入注入 ----

func (s *Session) useAgentInput() bool { return s.InputMode() == "agent" }

// agentCtrl 返回可用的 Agent 控制连接（失效会自动重建）。
func (s *Session) agentCtrl() (*agent.Control, error) {
	s.ctrlMu.Lock()
	defer s.ctrlMu.Unlock()
	if s.ctrl != nil {
		if s.ctrl.Healthy() {
			return s.ctrl, nil
		}
		s.ctrl.Close()
		s.ctrl = nil
	}
	if s.ctrlLocal == 0 {
		s.ctrlLocal = agent.FreePort()
	}
	local := fmt.Sprintf("tcp:%d", s.ctrlLocal)
	if err := s.adb.Forward(s.serial, local, "tcp:8586"); err != nil {
		return nil, err
	}
	c, err := agent.ConnectControl(fmt.Sprintf("127.0.0.1:%d", s.ctrlLocal))
	if err != nil {
		return nil, err
	}
	s.ctrl = c
	return c, nil
}

// Tap 点击。坐标按设备屏幕范围钳制，非法值直接丢弃。
func (s *Session) Tap(x, y int) {
	w, h := s.Size()
	if !validPoint(x, y, w, h) {
		return
	}
	if s.useAgentInput() {
		if c, err := s.agentCtrl(); err == nil {
			if err := c.Tap(x, y); err == nil {
				return
			}
		}
	}
	if err := s.adb.Tap(s.serial, x, y); err != nil {
		s.logf("tap(%d,%d) 失败: %v", x, y, err)
	}
}

// Swipe 滑动。
func (s *Session) Swipe(x1, y1, x2, y2, durMS int) {
	w, h := s.Size()
	if !validPoint(x1, y1, w, h) || !validPoint(x2, y2, w, h) {
		return
	}
	durMS = clampInt(durMS, 20, 3000)
	if s.useAgentInput() {
		if c, err := s.agentCtrl(); err == nil {
			if err := c.Swipe(x1, y1, x2, y2, durMS); err == nil {
				return
			}
		}
	}
	if err := s.adb.Swipe(s.serial, x1, y1, x2, y2, durMS); err != nil {
		s.logf("swipe 失败: %v", err)
	}
}

// Key 按键。
func (s *Session) Key(name string) {
	if s.useAgentInput() && (name == "HOME" || name == "BACK" || name == "RECENTS") {
		if c, err := s.agentCtrl(); err == nil {
			if err := c.Key(name); err == nil {
				return
			}
		}
	}
	if err := s.adb.Key(s.serial, name); err != nil {
		s.logf("按键 %s 失败: %v", name, err)
	}
}

// Text 文本输入。
func (s *Session) Text(text string) {
	if strings.TrimSpace(text) == "" {
		return
	}
	if adb.HasNonASCII(text) {
		s.logf("提示: 含非 ASCII 字符，`input text` 只能输入 ASCII，中文请用手机输入法")
	}
	if err := s.adb.Text(s.serial, text); err != nil {
		s.logf("文本输入失败: %v", err)
		return
	}
	s.logf("已输入文本（%d 字符）", len([]rune(text)))
}

// WakeUp 唤醒屏幕。
func (s *Session) WakeUp() {
	if err := s.adb.WakeUp(s.serial); err != nil {
		s.logf("唤醒失败: %v", err)
	}
}

// ScreenshotPNG 截图并返回 PNG 字节。
func (s *Session) ScreenshotPNG() ([]byte, error) {
	img, err := s.adb.Screenshot(s.serial)
	if err != nil {
		return nil, err
	}
	var buf bytes.Buffer
	if err := png.Encode(&buf, img); err != nil {
		return nil, err
	}
	return buf.Bytes(), nil
}

// SaveScreenshot 截图保存到本地目录。
func (s *Session) SaveScreenshot(dir string) (string, error) {
	img, err := s.adb.Screenshot(s.serial)
	if err != nil {
		return "", err
	}
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return "", err
	}
	name := fmt.Sprintf("%s_%s.png", sanitize(s.serial), time.Now().Format("20060102_150405"))
	path := filepath.Join(dir, name)
	f, err := os.Create(path)
	if err != nil {
		return "", err
	}
	defer f.Close()
	if err := png.Encode(f, img); err != nil {
		return "", err
	}
	return path, nil
}

// StatusMap 返回状态字典（供 WebSocket/API 使用）。
func (s *Session) StatusMap() map[string]interface{} {
	info := s.Info()
	m := map[string]interface{}{
		"serial":      s.serial,
		"model":       s.model,
		"videoMode":   s.VideoMode(),
		"inputMode":   s.InputMode(),
		"fps":         atomic.LoadInt32(&s.fpsSnapshot),
		"orientation": info.Orientation,
		"battery":     info.Battery,
		"charging":    info.Charging,
		"androidVer":  info.AndroidVer,
		"density":     info.Density,
		"viewers":     s.SubscriberCount(),
	}
	if info.Width > 0 {
		m["width"] = info.Width
		m["height"] = info.Height
	}
	return m
}

// Serial 返回序列号。
func (s *Session) Serial() string { return s.serial }

// Model 返回型号。
func (s *Session) Model() string { return s.model }

// StartedAt 返回会话创建时间。
func (s *Session) StartedAt() time.Time { return s.startedAt }

func (s *Session) logf(format string, args ...interface{}) {
	if s.logFn != nil {
		s.logFn(format, args...)
		return
	}
	fmt.Printf("%s: %s\n", s.serial, fmt.Sprintf(format, args...))
}

func sanitize(str string) string {
	return strings.Map(func(r rune) rune {
		if r == ':' || r == '\\' || r == '/' || r == '.' {
			return '_'
		}
		return r
	}, str)
}

// validPoint 校验坐标落在屏幕范围内；宽高未知（0）时只做上限保护。
func validPoint(x, y, w, h int) bool {
	if x < 0 || y < 0 || x > 32000 || y > 32000 {
		return false
	}
	if w > 0 && x > w {
		return false
	}
	if h > 0 && y > h {
		return false
	}
	return true
}

func clampInt(v, lo, hi int) int {
	if v < lo {
		return lo
	}
	if v > hi {
		return hi
	}
	return v
}

// drainLogFirst 丢弃管道内容，但记录首行（用于排障）。
func drainLogFirst(r io.Reader, serial string) {
	buf := make([]byte, 4096)
	first := true
	for {
		n, err := r.Read(buf)
		if first && n > 0 {
			first = false
			logFirst(serial, buf[:n])
		}
		if err != nil {
			return
		}
	}
}

func logFirst(serial string, b []byte) {
	if len(b) > 256 {
		b = b[:256]
	}
	fmt.Printf("[%s screenrecord] %q\n", serial, strings.TrimSpace(string(b)))
}

func killProc(cmd *exec.Cmd) {
	if cmd != nil && cmd.Process != nil {
		_ = cmd.Process.Kill()
	}
}
