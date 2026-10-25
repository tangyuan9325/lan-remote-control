// Command server boots the Legacy V2 Go control server — a from-scratch,
// dependency-light rewrite of the legacy v1.4.1 Python (rc14) Flask app:
// stdlib net/http, on-demand capture workers, constant-time auth, and a
// single static binary (no PyInstaller required).
package main

import (
	"flag"
	"log"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"
	"time"

	"github.com/tangyuan9325/lan-remote-control/legacy-v2/internal/core"
	"github.com/tangyuan9325/lan-remote-control/legacy-v2/internal/server"
)

func main() {
	var (
		port      = flag.Int("port", 5000, "HTTP listen port")
		password  = flag.String("password", "", "access password (empty = no auth; NOT recommended)")
		enableCmd = flag.Bool("enable-cmd", false, "allow remote command execution endpoint (dangerous, opt-in)")
		fps       = flag.Int("fps", 15, "target stream FPS (1-60)")
		quality   = flag.Int("quality", 60, "JPEG quality 10-95")
		resW      = flag.Int("width", 1280, "stream width")
		resH      = flag.Int("height", 720, "stream height")
		upDir     = flag.String("uploads", "", "upload directory (default: ./uploads next to binary)")
	)
	flag.Parse()

	core.Cfg.Port = *port
	core.Cfg.Password = *password
	core.Cfg.EnableCmd = *enableCmd
	core.Cfg.FPS = *fps
	core.Cfg.Quality = *quality
	core.Cfg.ResW = *resW
	core.Cfg.ResH = *resH
	core.Cfg.UploadDir = *upDir
	core.Cfg.ClampSettings()
	core.Cfg.Token = core.GenToken()

	base := "."
	if exe, err := os.Executable(); err == nil {
		base = filepath.Dir(exe)
	}
	if err := core.Cfg.InitUploadDir(base); err != nil {
		log.Fatalf("uploads dir: %v", err)
	}

	srv := server.New(core.Cfg.Port)

	go func() {
		sig := make(chan os.Signal, 1)
		signal.Notify(sig, os.Interrupt, syscall.SIGTERM)
		<-sig
		log.Println("shutting down...")
		srv.Shutdown(5 * time.Second)
	}()

	localIP := core.GetLocalIP()
	log.Printf("Legacy V2 (Go) control server %s listening on http://%s:%d", core.Version, localIP, core.Cfg.Port)
	if core.Cfg.Password == "" {
		log.Println("WARNING: no password set — anyone on the LAN can control this machine. Use -password.")
	}
	if core.Cfg.EnableCmd {
		log.Println("WARNING: remote command execution is ENABLED (-enable-cmd).")
	}

	if err := srv.Start(); err != nil && err != http.ErrServerClosed {
		log.Fatal(err)
	}
}
