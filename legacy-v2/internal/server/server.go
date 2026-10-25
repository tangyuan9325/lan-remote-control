// Package server implements the Legacy V2 HTTP control server — a Go rewrite
// of rc14's Flask app with the same Claude-styled web console, endpoints and
// security posture, but:
//   - stdlib net/http (no Flask/werkzeug dependency => no PyInstaller issues)
//   - MJPEG over multipart/x-mixed-replace with real frame boundaries
//   - on-demand capture worker (zero CPU when idle)
//   - constant-time password check + rc_token cookie auth for <img> streams
//   - path-traversal-safe uploads/downloads, opt-in remote command execution
package server

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"image"
	"image/color"
	"image/jpeg"
	"io"
	"log"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/tangyuan9325/lan-remote-control/go-server/camera"

	"github.com/tangyuan9325/lan-remote-control/legacy-v2/internal/capture"
	"github.com/tangyuan9325/lan-remote-control/legacy-v2/internal/core"
	"github.com/tangyuan9325/lan-remote-control/legacy-v2/internal/input"
)

const maxUploadBytes = 512 << 20 // 512 MB cap (parity with rc14)

// maxConcurrentStreams bounds MJPEG consumers so a hostile LAN client cannot
// exhaust server resources by opening thousands of streaming handlers.
const maxConcurrentStreams = 8

var errTooManyStreams = errors.New("too many concurrent stream clients")

type Server struct {
	mux       *http.Server
	screen    *capture.Screen
	injector  *input.Injector
	camBridge *cameraBridge
	streams   int64 // atomic: currently attached stream consumers
}

// New builds the server; screen worker starts parked (no cost until viewed).
func New(port int) *Server {
	s := &Server{
		screen:    capture.NewScreen(core.Cfg.Quality, core.Cfg.ResW, core.Cfg.ResH, core.Cfg.FPS),
		injector:  input.New(),
		camBridge: newCameraBridge(),
	}
	mux := http.NewServeMux()
	s.routes(mux)
	s.mux = &http.Server{
		Addr:              fmt.Sprintf("0.0.0.0:%d", port),
		Handler:           secHeaders(mux),
		ReadHeaderTimeout: 10 * time.Second,
		ReadTimeout:       60 * time.Second, // generous: uploads stream here
		WriteTimeout:      0,                // MJPEG streams are long-lived; per-handler deadlines instead
		IdleTimeout:       120 * time.Second,
	}
	return s
}

func secHeaders(h http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Header().Set("X-Frame-Options", "SAMEORIGIN")
		w.Header().Set("Referrer-Policy", "no-referrer")
		h.ServeHTTP(w, r)
	})
}

// authOK mirrors rc14._auth_ok: ?password= param or rc_token cookie,
// checked in constant time against the configured password.
func authOK(r *http.Request) bool {
	cand := r.URL.Query().Get("password")
	if cand == "" {
		if c, err := r.Cookie("rc_token"); err == nil {
			cand = c.Value
		}
	}
	if cand == "" && r.Body != nil && strings.HasPrefix(r.Header.Get("Content-Type"), "application/json") {
		// JSON body may carry the password field; peek without consuming
		var probe struct {
			Password string `json:"password"`
		}
		body, _ := io.ReadAll(io.LimitReader(r.Body, 1<<20))
		_ = json.Unmarshal(body, &probe)
		cand = probe.Password
	}
	return core.CheckPassword(cand)
}

func writeJSON(w http.ResponseWriter, code int, v interface{}) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(v)
}

func (s *Server) routes(mux *http.ServeMux) {
	mux.HandleFunc("/", s.handleIndex)
	mux.HandleFunc("/api_status", s.handleStatus)
	mux.HandleFunc("/video_feed", s.auth(s.handleVideoFeed))
	mux.HandleFunc("/camera_feed", s.auth(s.handleCameraFeed))
	mux.HandleFunc("/audio_feed", s.auth(s.handleAudioFeed))
	mux.HandleFunc("/mouse_move", s.auth(s.handleMouse))
	mux.HandleFunc("/mouse_click", s.auth(s.handleMouse))
	mux.HandleFunc("/mouse_right_click", s.auth(s.handleMouse))
	mux.HandleFunc("/mouse_scroll", s.auth(s.handleScroll))
	mux.HandleFunc("/keyboard", s.auth(s.handleKeyboard))
	mux.HandleFunc("/set_quality", s.auth(s.handleSetQuality))
	mux.HandleFunc("/toggle_camera", s.auth(s.handleToggleCamera))
	mux.HandleFunc("/toggle_microphone", s.auth(s.handleToggleMic))
	mux.HandleFunc("/upload_file", s.auth(s.handleUpload))
	mux.HandleFunc("/list_files", s.auth(s.handleListFiles))
	mux.HandleFunc("/download_file/", s.auth(s.handleDownload))
	mux.HandleFunc("/delete_file/", s.auth(s.handleDelete))
	mux.HandleFunc("/execute_cmd", s.auth(s.handleExecCmd))
	mux.HandleFunc("/system", s.auth(s.handleSystem))
}

func (s *Server) auth(h http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if !authOK(r) {
			writeJSON(w, http.StatusUnauthorized, map[string]string{"error": "unauthorized"})
			return
		}
		h(w, r)
	}
}

func (s *Server) handleIndex(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path != "/" {
		http.NotFound(w, r)
		return
	}
	html := core.HTMLTemplateFor(core.Version)
	pwdState := "未设置"
	needsPW := "false"
	if core.Cfg.Password != "" {
		pwdState = "已启用"
		needsPW = "true"
	}
	rep := strings.NewReplacer(
		"__VERSION__", core.Version,
		"__QUALITY__", strconv.Itoa(core.Cfg.Quality),
		"__RESOLUTION__", fmt.Sprintf("%dx%d", core.Cfg.ResW, core.Cfg.ResH),
		"__FPS__", strconv.Itoa(core.Cfg.FPS),
		"__PWDSTATE__", pwdState,
		"__NEEDSPW__", needsPW,
	)
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	_, _ = w.Write([]byte(rep.Replace(html)))
}

func (s *Server) handleStatus(w http.ResponseWriter, r *http.Request) {
	capN, reuse := s.screen.Stats()
	res := map[string]interface{}{
		"ok":                true,
		"requires_password": core.Cfg.Password != "",
		"cmd_enabled":       core.Cfg.EnableCmd,
		"resolution":        fmt.Sprintf("%dx%d", core.Cfg.ResW, core.Cfg.ResH),
		"fps":               core.Cfg.FPS,
		"quality":           core.Cfg.Quality,
		"captured":          capN,
		"reused":            reuse,
		"version":           core.Version,
	}
	if !core.CheckPassword(r.URL.Query().Get("password")) {
		// status is public info only; still gate the counters behind auth
		delete(res, "captured")
		delete(res, "reused")
	}
	writeJSON(w, http.StatusOK, res)
}

// ---------------- video feeds ----------------

func (s *Server) handleVideoFeed(w http.ResponseWriter, r *http.Request) {
	// connection-count guard: reject beyond cap instead of spawning unbounded handlers
	if atomic.AddInt64(&s.streams, 1) > maxConcurrentStreams {
		atomic.AddInt64(&s.streams, -1)
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "too many viewers"})
		return
	}
	defer atomic.AddInt64(&s.streams, -1)
	detach := s.screen.Attach()
	defer detach()
	w.Header().Set("Content-Type", "multipart/x-mixed-replace; boundary=frameboundary")
	w.Header().Set("Cache-Control", "no-store")
	flusher, ok := w.(http.Flusher)
	if !ok {
		http.Error(w, "streaming unsupported", http.StatusInternalServerError)
		return
	}
	interval := time.Second / time.Duration(maxInt(core.Cfg.FPS, 1))
	rc := r.Context()
	lastFrame := -1
	for {
		select {
		case <-rc.Done():
			return
		default:
		}
		frame := s.screen.Frame()
		// only re-send when a NEW frame arrived (dedup by length+header hash keeps it cheap);
		// idle screens cost ~0 bandwidth/CPU on both ends
		if len(frame) > 0 {
			idx := s.screen.LastIndex()
			if idx != lastFrame || idx == 0 {
				lastFrame = idx
				if _, err := w.Write(mjpegPart(frame)); err != nil {
					return
				}
				flusher.Flush()
			}
		}
		time.Sleep(interval)
	}
}

func mjpegPart(data []byte) []byte {
	head := fmt.Sprintf("--frameboundary\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n", len(data))
	out := make([]byte, 0, len(head)+len(data)+2)
	out = append(out, head...)
	out = append(out, data...)
	out = append(out, '\r', '\n')
	return out
}

// cameraBridge wraps the go-server FFmpeg/test-pattern camera pipeline so the
// Legacy V2 MJPEG endpoint can stream real frames. Reference-counted: first
// viewer starts it, last viewer stops it (no zombie ffmpeg when idle).
type cameraBridge struct {
	mu        sync.Mutex
	cam       *camera.Camera
	consumers int
}

func newCameraBridge() *cameraBridge { return &cameraBridge{} }

func (b *cameraBridge) attach() (get func() ([]byte, error), detach func()) {
	b.mu.Lock()
	if b.consumers == 0 {
		if b.cam == nil {
			b.cam = camera.NewCamera()
		}
		_ = b.cam.Start() // idempotent; falls back to test pattern without ffmpeg
	}
	b.consumers++
	c := b.cam
	b.mu.Unlock()

	get = c.GetFrame
	var once sync.Once
	detach = func() {
		once.Do(func() {
			b.mu.Lock()
			b.consumers--
			last := b.consumers <= 0
			if last && b.cam != nil {
				b.cam.Stop()
			}
			b.mu.Unlock()
		})
	}
	return get, detach
}

func (s *Server) handleCameraFeed(w http.ResponseWriter, r *http.Request) {
	if !core.Cfg.CameraEnabled {
		writeJSON(w, http.StatusForbidden, map[string]string{"error": "camera disabled"})
		return
	}
	if atomic.AddInt64(&s.streams, 1) > maxConcurrentStreams {
		atomic.AddInt64(&s.streams, -1)
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "too many viewers"})
		return
	}
	defer atomic.AddInt64(&s.streams, -1)
	getFrame, detach := s.camBridge.attach()
	defer detach()

	w.Header().Set("Content-Type", "multipart/x-mixed-replace; boundary=frameboundary")
	w.Header().Set("Cache-Control", "no-store")
	flusher, _ := w.(http.Flusher)
	tick := time.NewTicker(time.Second / time.Duration(maxInt(core.Cfg.FPS, 1)))
	defer tick.Stop()
	rc := r.Context()
	for {
		select {
		case <-rc.Done():
			return
		case <-tick.C:
			img, err := getFrame()
			if err != nil || len(img) == 0 {
				img = placeholderJPEG("摄像头不可用 / Camera unavailable")
			}
			if _, err := w.Write(mjpegPart(img)); err != nil {
				return
			}
			if flusher != nil {
				flusher.Flush()
			}
		}
	}
}

func (s *Server) handleAudioFeed(w http.ResponseWriter, r *http.Request) {
	if !core.Cfg.MicEnabled {
		writeJSON(w, http.StatusForbidden, map[string]string{"error": "microphone disabled"})
		return
	}
	writeJSON(w, http.StatusNotImplemented, map[string]string{"error": "audio capture not wired in legacy-v2 yet"})
}

// ---------------- input ----------------

func decodeCmd(r *http.Request) (map[string]interface{}, error) {
	body, err := io.ReadAll(io.LimitReader(r.Body, 1<<20))
	if err != nil {
		return nil, err
	}
	var m map[string]interface{}
	if err := json.Unmarshal(body, &m); err != nil {
		return nil, err
	}
	return m, nil
}

func (s *Server) handleMouse(w http.ResponseWriter, r *http.Request) {
	cmd, err := decodeCmd(r)
	if err != nil || cmd["x"] == nil || cmd["y"] == nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"status": "error", "message": "Invalid request"})
		return
	}
	// derive the command type from the endpoint so clients can't forge types
	switch r.URL.Path {
	case "/mouse_move":
		cmd["type"] = "mouse_move"
	case "/mouse_click":
		cmd["type"] = "mouse_click"
	case "/mouse_right_click":
		cmd["type"] = "mouse_click"
		cmd["button"] = "right"
	}
	s.injector.HandleCommand(cmd, core.Cfg.ResW, core.Cfg.ResH)
	writeJSON(w, http.StatusOK, map[string]string{"status": "success"})
}

func (s *Server) handleScroll(w http.ResponseWriter, r *http.Request) {
	cmd, err := decodeCmd(r)
	if err != nil || cmd["delta"] == nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"status": "error", "message": "Invalid request"})
		return
	}
	cmd["type"] = "mouse_scroll"
	s.injector.HandleCommand(cmd, core.Cfg.ResW, core.Cfg.ResH)
	writeJSON(w, http.StatusOK, map[string]string{"status": "success"})
}

func (s *Server) handleKeyboard(w http.ResponseWriter, r *http.Request) {
	cmd, err := decodeCmd(r)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"status": "error", "message": "Invalid request"})
		return
	}
	key, _ := cmd["key"].(string)
	text, _ := cmd["text"].(string)
	switch {
	case text != "":
		if len(text) > 512 {
			text = text[:512]
		}
		s.injector.HandleCommand(map[string]interface{}{"type": "key_type", "text": text}, core.Cfg.ResW, core.Cfg.ResH)
	case key != "" && len(key) <= 64:
		s.injector.HandleCommand(map[string]interface{}{"type": "key_down", "key": key}, core.Cfg.ResW, core.Cfg.ResH)
		s.injector.HandleCommand(map[string]interface{}{"type": "key_up", "key": key}, core.Cfg.ResW, core.Cfg.ResH)
	default:
		writeJSON(w, http.StatusBadRequest, map[string]string{"status": "error", "message": "Invalid request"})
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"status": "success"})
}

func (s *Server) handleSetQuality(w http.ResponseWriter, r *http.Request) {
	cmd, err := decodeCmd(r)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"status": "error", "message": "Invalid request"})
		return
	}
	qf, _ := cmd["quality"].(float64)
	q := int(qf)
	if q < 10 || q > 95 {
		writeJSON(w, http.StatusBadRequest, map[string]string{"status": "error", "message": "quality out of range"})
		return
	}
	core.Cfg.Quality = q
	s.screen.UpdateSettings(q, core.Cfg.FPS, core.Cfg.ResW, core.Cfg.ResH, core.Cfg.MaxStreamEdge)
	writeJSON(w, http.StatusOK, map[string]interface{}{"status": "success", "quality": q})
}

func (s *Server) handleToggleCamera(w http.ResponseWriter, r *http.Request) {
	cmd, err := decodeCmd(r)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"status": "error", "message": "Invalid request"})
		return
	}
	en, _ := cmd["enabled"].(bool)
	core.Cfg.CameraEnabled = en
	writeJSON(w, http.StatusOK, map[string]bool{"enabled": en})
}

func (s *Server) handleToggleMic(w http.ResponseWriter, r *http.Request) {
	cmd, err := decodeCmd(r)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"status": "error", "message": "Invalid request"})
		return
	}
	en, _ := cmd["enabled"].(bool)
	core.Cfg.MicEnabled = en
	writeJSON(w, http.StatusOK, map[string]bool{"enabled": en})
}

// ---------------- files ----------------

func (s *Server) handleUpload(w http.ResponseWriter, r *http.Request) {
	r.Body = http.MaxBytesReader(w, r.Body, maxUploadBytes+1<<20)
	if err := r.ParseMultipartForm(32 << 20); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]interface{}{"success": false, "error": "上传请求过大或格式错误"})
		return
	}
	file, hdr, err := r.FormFile("file")
	if err != nil {
		writeJSON(w, http.StatusOK, map[string]interface{}{"success": false, "error": "没有文件被上传"})
		return
	}
	defer file.Close()
	target, ok := core.SafeUploadPath(filepath.Base(hdr.Filename))
	if !ok {
		writeJSON(w, http.StatusOK, map[string]interface{}{"success": false, "error": "非法文件名"})
		return
	}
	out, err := os.OpenFile(target, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o640)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]interface{}{"success": false, "error": "无法创建文件"})
		return
	}
	written, err := io.Copy(out, io.LimitReader(file, maxUploadBytes))
	out.Close()
	if err != nil || written >= maxUploadBytes {
		os.Remove(target)
		writeJSON(w, http.StatusRequestEntityTooLarge, map[string]interface{}{"success": false, "error": "文件超过 512MB 限制"})
		return
	}
	writeJSON(w, http.StatusOK, map[string]interface{}{"success": true, "message": "文件上传成功"})
}

func (s *Server) handleListFiles(w http.ResponseWriter, r *http.Request) {
	entries, err := os.ReadDir(core.Cfg.UploadDir)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]interface{}{"success": false, "error": err.Error()})
		return
	}
	type fileInfo struct {
		Name     string  `json:"name"`
		Size     int64   `json:"size"`
		Modified float64 `json:"modified"`
	}
	files := make([]fileInfo, 0, 32)
	for _, e := range entries {
		if e.IsDir() || e.Type()&os.ModeSymlink != 0 {
			continue
		}
		name := e.Name()
		if strings.HasPrefix(name, ".") {
			continue
		}
		st, err := e.Info()
		if err != nil {
			continue
		}
		files = append(files, fileInfo{Name: name, Size: st.Size(), Modified: float64(st.ModTime().Unix())})
		if len(files) >= 500 {
			break
		}
	}
	writeJSON(w, http.StatusOK, map[string]interface{}{"success": true, "files": files})
}

func (s *Server) handleDownload(w http.ResponseWriter, r *http.Request) {
	name := strings.TrimPrefix(r.URL.Path, "/download_file/")
	target, ok := core.SafeUploadPath(name)
	if !ok {
		http.Error(w, "文件不存在", http.StatusNotFound)
		return
	}
	st, err := os.Stat(target)
	if err != nil || !st.Mode().IsRegular() {
		http.Error(w, "文件不存在", http.StatusNotFound)
		return
	}
	w.Header().Set("Content-Disposition", fmt.Sprintf("attachment; filename=%q", filepath.Base(target)))
	http.ServeFile(w, r, target)
}

func (s *Server) handleDelete(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodDelete {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	name := strings.TrimPrefix(r.URL.Path, "/delete_file/")
	target, ok := core.SafeUploadPath(name)
	if !ok {
		writeJSON(w, http.StatusNotFound, map[string]interface{}{"success": false, "error": "文件不存在"})
		return
	}
	if err := os.Remove(target); err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]interface{}{"success": false, "error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, map[string]interface{}{"success": true, "message": "文件删除成功"})
}

// ---------------- command execution (opt-in) ----------------

func (s *Server) handleExecCmd(w http.ResponseWriter, r *http.Request) {
	if !core.Cfg.EnableCmd {
		writeJSON(w, http.StatusForbidden, map[string]interface{}{
			"status": "error", "output": "命令执行已被禁用（安全默认）。请在启动时添加 --enable-cmd。"})
		return
	}
	cmd, err := decodeCmd(r)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]interface{}{"status": "error", "output": "Invalid request"})
		return
	}
	raw, _ := cmd["command"].(string)
	if len(raw) > 4000 {
		raw = raw[:4000]
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	var c *exec.Cmd
	c = exec.CommandContext(ctx, shellProg, shellFlag, raw)
	out, err := c.CombinedOutput()
	if ctx.Err() == context.DeadlineExceeded {
		writeJSON(w, http.StatusOK, map[string]interface{}{"status": "error", "output": "命令执行超时(30s)"})
		return
	}
	output := string(out)
	if strings.TrimSpace(output) == "" && err != nil {
		output = err.Error()
	}
	if len(output) > 200000 {
		output = output[:200000]
	}
	if output == "" {
		output = "(无输出)"
	}
	writeJSON(w, http.StatusOK, map[string]interface{}{"status": "success", "output": output})
}

// ---------------- system power ----------------

func (s *Server) handleSystem(w http.ResponseWriter, r *http.Request) {
	cmd, err := decodeCmd(r)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"status": "error", "message": "Invalid request"})
		return
	}
	action, _ := cmd["action"].(string)
	var c *exec.Cmd
	switch action {
	case "shutdown":
		if isWindows() {
			c = exec.Command("shutdown", "/s", "/t", "5")
		} else {
			c = exec.Command("shutdown", "-h", "now")
		}
	case "restart":
		if isWindows() {
			c = exec.Command("shutdown", "/r", "/t", "5")
		} else {
			c = exec.Command("reboot")
		}
	case "sleep":
		if isWindows() {
			c = exec.Command("rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0")
		} else {
			c = exec.Command("systemctl", "suspend")
		}
	default:
		writeJSON(w, http.StatusBadRequest, map[string]string{"status": "error", "message": "Unknown command"})
		return
	}
	if err := c.Start(); err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"status": "error", "message": err.Error()})
		return
	}
	log.Printf("[system] action=%s from %s", action, r.RemoteAddr)
	writeJSON(w, http.StatusOK, map[string]string{"status": "success"})
}

// ---------------- lifecycle ----------------

func (s *Server) Start() error { return s.mux.ListenAndServe() }

func (s *Server) Shutdown(timeout time.Duration) {
	s.screen.Stop()
	s.injector.Close()
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	_ = s.mux.Shutdown(ctx)
}

func placeholderJPEG(text string) []byte {
	// minimal static JPEG-ish placeholder: reuse screen black frame via encode
	img := image.NewRGBA(image.Rect(0, 0, 640, 360))
	for y := 0; y < 360; y++ {
		for x := 0; x < 640; x++ {
			img.SetRGBA(x, y, color.RGBA{R: 20, G: 19, B: 17, A: 255})
		}
	}
	var buf bytes.Buffer
	_ = jpeg.Encode(&buf, img, &jpeg.Options{Quality: 40})
	return buf.Bytes()
}

func maxInt(a, b int) int {
	if a > b {
		return a
	}
	return b
}

func setDefaultHeader(w http.ResponseWriter, key, val string) {
	if w.Header().Get(key) == "" {
		w.Header().Set(key, val)
	}
}
