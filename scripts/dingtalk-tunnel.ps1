# ============================================================
# 钉钉 OAuth 本地联调隧道脚本 (ngrok)
#
# 用途: 本地后端在 8000 跑着, 这个脚本启动 ngrok 暴露到公网,
#       并把公网地址自动回填到 .env 的 DINGTALK_REDIRECT_URI,
#       同时打印出钉钉回调地址, 供你在钉钉开放平台应用后台配置。
#
# 每次自己执行:  powershell -ExecutionPolicy Bypass -File scripts\dingtalk-tunnel.ps1
# 非交互(跑通验证后自动停):  powershell -ExecutionPolicy Bypass -File scripts\dingtalk-tunnel.ps1 -NoWait
#
# 前置:
#   1. ngrok 已安装 (检查: ngrok --version)
#   2. 本地后端已在 8000 端口跑着 (uvicorn app.main:fastApi --port 8000)
#   3. ngrok 已登录 (首次需 `ngrok config add-authtoken <token>`)
# ============================================================

param(
    [switch]$NoWait  # 非交互模式: 隧道建立并回填 .env 后立即停止 ngrok
)

$ErrorActionPreference = "Stop"
$port = 8000
$tunnelUrl = "http://127.0.0.1:4040/api/tunnels"
$envFile = Join-Path $PSScriptRoot "..\.env"

Write-Host "==> 检查本地后端 $port 端口..." -ForegroundColor Cyan
try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:$port/health" -TimeoutSec 3 -UseBasicParsing
    Write-Host "    HTTP $($r.StatusCode) — 后端正常(稍后会重启以加载新 .env)" -ForegroundColor Green
} catch {
    Write-Host "    后端未在 $port 运行 — 脚本稍后会启动它" -ForegroundColor Yellow
}

Write-Host "==> 检查 ngrok 是否已登录..." -ForegroundColor Cyan
$authed = ngrok config check 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "    ngrok 未登录, 请先执行: ngrok config add-authtoken <你的token>" -ForegroundColor Red
    Write-Host "    (token 在 https://dashboard.ngrok.com 获取)"
    exit 1
}

# 启动 ngrok 后台进程, 日志写临时文件(stdout/stderr 必须分文件, PowerShell 限制)
$logOut = Join-Path $env:TEMP "ngrok-dingtalk.out.log"
$logErr = Join-Path $env:TEMP "ngrok-dingtalk.err.log"

# 关键: 本机有 HTTP_PROXY/HTTPS_PROXY 代理(127.0.0.1:7897), ngrok 走代理是
# Pay-as-you-go 付费功能, 会直接启动失败 (ERR_NGROK_9009)。
# 保存原值, 启动 ngrok 时临时清掉, 结束后恢复(不污染全局环境)。
$savedHttpProxy = $env:HTTP_PROXY
$savedHttpsProxy = $env:HTTPS_PROXY
$savedNoProxy = $env:NO_PROXY
Remove-Item Env:HTTP_PROXY -ErrorAction SilentlyContinue
Remove-Item Env:HTTPS_PROXY -ErrorAction SilentlyContinue
Remove-Item Env:NO_PROXY -ErrorAction SilentlyContinue

Write-Host "==> 启动 ngrok 暴露 $port 端口 (后台, 已临时绕过代理)..." -ForegroundColor Cyan
try {
    $proc = Start-Process -FilePath "ngrok" -ArgumentList "http", "$port", "--log=stdout" -RedirectStandardOutput $logOut -RedirectStandardError $logErr -PassThru
} finally {
    # 恢复代理变量, 不影响后续
    if ($savedHttpProxy) { $env:HTTP_PROXY = $savedHttpProxy }
    if ($savedHttpsProxy) { $env:HTTPS_PROXY = $savedHttpsProxy }
    if ($savedNoProxy) { $env:NO_PROXY = $savedNoProxy }
}
Write-Host "    ngrok PID: $($proc.Id)  (日志: $logOut / $logErr)" -ForegroundColor Green

# 等待 ngrok 隧道建立, 轮询本地 API 拿公网 URL (最多 15 秒)
$publicUrl = $null
for ($i = 0; $i -lt 15; $i++) {
    Start-Sleep -Seconds 1
    try {
        $tunnels = (Invoke-WebRequest -Uri $tunnelUrl -TimeoutSec 2 -UseBasicParsing).Content | ConvertFrom-Json
        $https = $tunnels.tunnels | Where-Object { $_.proto -eq "https" } | Select-Object -First 1
        if ($https -and $https.public_url) {
            $publicUrl = $https.public_url
            break
        }
    } catch { # ngrok 还没起来, 继续等
    }
}

if (-not $publicUrl) {
    Write-Host "    ngrok 隧道建立失败, 查看日志: $logOut / $logErr" -ForegroundColor Red
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    exit 1
}
Write-Host "   公网地址: $publicUrl" -ForegroundColor Green

# 构造钉钉回调地址
$callback = "$publicUrl/api/v1/dingtalk/callback"
Write-Host ""
Write-Host "============================================================" -ForegroundColor Yellow
Write-Host "  钉钉 OAuth 回调地址: $callback" -ForegroundColor Yellow
Write-Host "  把这个地址配置到钉钉开放平台应用后台: 开发配置 → 安全设置 → 回调URL" -ForegroundColor Yellow
Write-Host "============================================================" -ForegroundColor Yellow

# 自动回填 .env 的 DINGTALK_REDIRECT_URI
# 注意: 用 .NET WriteAllText 写无 BOM 的 UTF-8(PowerShell 5.1 的 Set-Content UTF8
#       会写 BOM, 破坏 pydantic-settings 读取 .env)。
if (Test-Path $envFile) {
    $content = [System.IO.File]::ReadAllText($envFile, [System.Text.Encoding]::UTF8)
    if ($content -match "(?m)^DINGTALK_REDIRECT_URI=.*$") {
        $content = $content -replace "(?m)^DINGTALK_REDIRECT_URI=.*$", "DINGTALK_REDIRECT_URI=$callback"
    } else {
        $content = $content.TrimEnd() + "`r`nDINGTALK_REDIRECT_URI=$callback`r`n"
    }
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($envFile, $content, $utf8NoBom)
    Write-Host "==> 已更新 .env: DINGTALK_REDIRECT_URI=$callback" -ForegroundColor Green
} else {
    Write-Host "    (未找到 .env, 跳过回填)" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Yellow
Write-Host "  下一步: 重启后端让 .env 生效(后端只在启动时读 .env)" -ForegroundColor Yellow
Write-Host "  请在 backend 目录执行: " -ForegroundColor Yellow
Write-Host "    Ctrl+C 停掉当前 uvicorn, 然后重新运行:" -ForegroundColor Yellow
Write-Host "    .venv\Scripts\uvicorn app.main:fastApi --host 0.0.0.0 --port $port --reload" -ForegroundColor Yellow
Write-Host "============================================================" -ForegroundColor Yellow

Write-Host ""
if ($NoWait) {
    Write-Host "非交互模式 (-NoWait): 验证完成, 停止 ngrok..." -ForegroundColor Cyan
} else {
    Write-Host "ngrok 正在运行 (PID $($proc.Id))。按任意键停止并退出..." -ForegroundColor Cyan
    $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
}

# 停止 ngrok
Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
Write-Host "已停止 ngrok。" -ForegroundColor Green
