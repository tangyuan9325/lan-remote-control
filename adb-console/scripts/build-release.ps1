# 构建发布产物到 dist/
#
# 用法：
#   pwsh scripts/build-release.ps1                # 版本取 client/internal/server/server.go 里的默认值
#   pwsh scripts/build-release.ps1 -Version 1.1.1 # 指定版本
#
# 产物：
#   dist/adb-remote-console-windows-amd64.exe
#   dist/rc-agent-debug.apk          （若已构建 APK）
#   dist/SHA256SUMS.txt
#
# 说明：ffmpeg.exe 有 100MB+，不随本脚本打包，请在 Release 说明里给出下载方式。

param(
    [string]$Version = ""
)

$ErrorActionPreference = "Stop"

$root = Split-Path $PSScriptRoot -Parent
$dist = Join-Path $root "dist"
$clientDir = Join-Path $root "client"
$androidDir = Join-Path $root "android-agent"

# 版本号：未指定则从源码读取，保证与 -version 输出一致
if (-not $Version) {
    $src = Get-Content (Join-Path $clientDir "internal/server/server.go") -Raw
    if ($src -match 'var Version = "([^"]+)"') { $Version = $Matches[1] } else { $Version = "0.0.0" }
}
Write-Host "==> 版本: $Version"

if (Test-Path $dist) { Remove-Item $dist -Recurse -Force }
New-Item -ItemType Directory -Path $dist | Out-Null

# ---- 1. Windows 客户端 ----
Write-Host "==> 构建 Windows 客户端 (windows/amd64, 无 cgo)"
Push-Location $clientDir
try {
    $env:CGO_ENABLED = "0"
    $env:GOOS = "windows"
    $env:GOARCH = "amd64"
    $exe = Join-Path $dist "adb-remote-console-windows-amd64.exe"
    go build -trimpath -ldflags "-s -w -X remote-control/client/internal/server.Version=$Version" -o $exe .
    if ($LASTEXITCODE -ne 0) { throw "go build 失败" }
} finally {
    Pop-Location
    Remove-Item Env:CGO_ENABLED, Env:GOOS, Env:GOARCH -ErrorAction SilentlyContinue
}

# 自检：跑一次 -smoke，确认二进制可用
Write-Host "==> 自检 -version / -smoke"
& $exe -version
& $exe -smoke -nobrowser -port 18099
if ($LASTEXITCODE -ne 0) { throw "自检失败（-smoke 退出码 $LASTEXITCODE）" }
# 自检会在 exe 同目录写 config.json，不要打进发布包
Remove-Item (Join-Path $dist "config.json") -ErrorAction SilentlyContinue

# ---- 2. Android Agent ----
$apk = Join-Path $androidDir "app/build/outputs/apk/debug/app-debug.apk"
if (Test-Path $apk) {
    Write-Host "==> 拷贝 APK"
    Copy-Item $apk (Join-Path $dist "rc-agent-debug.apk")
} else {
    Write-Host "==> 未找到 APK，跳过（先执行: cd android-agent; gradle assembleDebug）"
}

# ---- 3. 校验和 ----
Write-Host "==> 生成 SHA256SUMS.txt"
Push-Location $dist
try {
    Get-ChildItem -File | Where-Object { $_.Name -ne "SHA256SUMS.txt" } | ForEach-Object {
        $h = (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLower()
        "$h  $($_.Name)"
    } | Set-Content -Path "SHA256SUMS.txt" -Encoding ASCII
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "==> 完成，产物："
Get-ChildItem $dist | Format-Table Name, @{N = "Size(MB)"; E = { "{0:N2}" -f ($_.Length / 1MB) } }
