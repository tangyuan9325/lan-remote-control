package server

import (
	"encoding/json"
	"os"
	"path/filepath"
)

// Config 客户端持久化配置。
type Config struct {
	AdbPath    string `json:"adb_path"`
	FFmpegPath string `json:"ffmpeg_path"`
	VideoMode  string `json:"video_mode"` // auto|png|record|agent
	InputMode  string `json:"input_mode"` // auto|shell|agent
	Port       int    `json:"port"`       // 本地 Web UI 端口，默认 8500
}

// DefaultConfig 返回默认配置。
func DefaultConfig() Config {
	return Config{VideoMode: "auto", InputMode: "auto", Port: 8500}
}

// ConfigPath 返回 exe 同目录下的 config.json 路径。
func ConfigPath() string {
	exe, err := os.Executable()
	if err != nil {
		return "config.json"
	}
	return filepath.Join(filepath.Dir(exe), "config.json")
}

// LoadConfig 读取配置文件；不存在时返回默认值。
func LoadConfig() Config {
	cfg := DefaultConfig()
	data, err := os.ReadFile(ConfigPath())
	if err != nil {
		return cfg
	}
	// 解析失败时保留默认值，避免配置文件被写坏后程序直接起不来
	if err := json.Unmarshal(data, &cfg); err != nil {
		return DefaultConfig()
	}
	if cfg.VideoMode == "" {
		cfg.VideoMode = "auto"
	}
	if cfg.InputMode == "" {
		cfg.InputMode = "auto"
	}
	if cfg.Port <= 0 || cfg.Port > 65535 {
		cfg.Port = 8500
	}
	return cfg
}

// Save 原子化保存配置。
// 原实现直接 WriteFile 覆盖，进程在写入中途退出会留下半截 JSON，
// 下次启动解析失败即丢配置；这里改为写临时文件再 rename。
func (c Config) Save() error {
	data, err := json.MarshalIndent(c, "", "  ")
	if err != nil {
		return err
	}
	path := ConfigPath()
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, data, 0o644); err != nil {
		return err
	}
	if err := os.Rename(tmp, path); err != nil {
		_ = os.Remove(tmp)
		return err
	}
	return nil
}
