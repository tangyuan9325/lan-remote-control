module github.com/tangyuan9325/lan-remote-control/legacy-v2

go 1.19

require (
	github.com/kbinani/screenshot v0.0.0-20250624051815-089614a94018
	github.com/tangyuan9325/lan-remote-control/go-server v0.0.0
)

require (
	github.com/gen2brain/shm v0.1.0 // indirect
	github.com/godbus/dbus/v5 v5.1.0 // indirect
	github.com/jezek/xgb v1.1.1 // indirect
	github.com/lxn/win v0.0.0-20210218163916-a377121e959e // indirect
	golang.org/x/sys v0.24.0 // indirect
)

replace github.com/tangyuan9325/lan-remote-control/go-server => ../go-server

replace golang.org/x/sys => golang.org/x/sys v0.24.0
