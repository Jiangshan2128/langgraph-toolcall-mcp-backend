# ============================================================
# AI Note Backend — 打包微信云托管部署包 (cloudbase-deploy.zip)
#
# 用途:
#   按 .dockerignore 的排除规则, 把当前工作区打成 CloudBase
#   可上传的 zip。然后到 微信云托管控制台 → 代码包部署 → 上传。
#
# 用法:
#   powershell -ExecutionPolicy Bypass -File scripts\package-cloudbase.ps1
#   可选参数:
#     -Output <name>   输出文件名 (默认 cloudbase-deploy.zip)
#     -CheckTests      打包前先跑一遍 pytest (慢, 默认跳过)
#
# 校验:
#   打包后自动核对包内是否包含钉钉 OAuth 关键文件, 防止上传到
#   过期旧包 (2026-08 曾踩坑: 旧 zip 缺 app/dingtalk/oauth.py,
#   部署后 import 即 500)。
#
# 注意:
#   zip 里刻意不含 .env —— 环境变量需在云托管控制台手动配置,
#   不要用带密钥的本地 .env 覆盖线上。
# ============================================================

param(
    [string]$Output = "cloudbase-deploy.zip",
    [switch]$CheckTests
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$outputAbs = [System.IO.Path]::GetFullPath((Join-Path $root $Output))

Write-Host "==> 打包根目录: $root" -ForegroundColor Cyan

# ---- 0. 可选: 打包前跑测试 ----
if ($CheckTests) {
    Write-Host "==> 先跑 pytest (CheckTests) ..." -ForegroundColor Cyan
    & (Join-Path $root ".venv\Scripts\python.exe") -m pytest -q
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] 测试未通过, 中止打包" -ForegroundColor Red
        exit 1
    }
}

# ---- 1. 读取 .dockerignore 排除规则 (与 Docker 构建同一套) ----
#   文件无 BOM, 必须显式 -Encoding UTF8, 否则末尾中文文件名会被当 ANSI 读乱。
$ignorePatterns = @()
Get-Content -Path (Join-Path $root ".dockerignore") -Encoding UTF8 | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith("#")) { $ignorePatterns += $line }
}

function Test-DockerIgnoreMatch {
    param([string]$rel, [string]$pat)
    # rel 用 '/' 分隔 (如 app/main.py)。
    # 目录规则 (结尾带 /): 路径任意一层目录名命中即排除。
    # 无斜杠规则: 按文件名/目录名匹配任意一层。
    # 含斜杠规则: 按整个相对路径匹配 (-like, 支持 * 和 [..])。
    $isDir = $pat.EndsWith("/")
    $p = $pat.TrimEnd("/")
    if ($isDir) {
        foreach ($seg in $rel.Split("/")) {
            if ($seg -eq $p) { return $true }
        }
        return $false
    }
    if ($pat -notmatch "/") {
        foreach ($seg in $rel.Split("/")) {
            if ($seg -like $p) { return $true }
        }
        return $false
    }
    return $rel -like $p
}

# ---- 2. 枚举文件 (DFS, 剪掉被目录规则排除的大目录如 .venv/tests/.git) ----
$include = New-Object 'System.Collections.Generic.List[string]'
$dirStack = New-Object 'System.Collections.Generic.Stack[string]'
$dirStack.Push($root)

while ($dirStack.Count -gt 0) {
    $dir = $dirStack.Pop()

    foreach ($sub in Get-ChildItem -Path $dir -Directory -Force) {
        $rel = $sub.FullName.Substring($root.Length + 1).Replace("\", "/")
        $excluded = $false
        foreach ($pat in $ignorePatterns) {
            if ($pat.EndsWith("/") -and (Test-DockerIgnoreMatch $rel $pat)) {
                $excluded = $true
                break
            }
        }
        if (-not $excluded) { $dirStack.Push($sub.FullName) }
    }

    foreach ($f in Get-ChildItem -Path $dir -File -Force) {
        $rel = $f.FullName.Substring($root.Length + 1).Replace("\", "/")
        if ([System.IO.Path]::GetFullPath($f.FullName) -eq $outputAbs) { continue }
        $state = $true
        foreach ($pat in $ignorePatterns) {
            if ($pat.StartsWith("!")) {
                if (Test-DockerIgnoreMatch $rel $pat.Substring(1)) { $state = $true }
            } elseif (Test-DockerIgnoreMatch $rel $pat) {
                $state = $false
            }
        }
        if ($state) { $include.Add($rel) }
    }
}
Write-Host "    共 $($include.Count) 个文件待打包" -ForegroundColor Green

# ---- 3. 写 zip ----
#   ZipArchiveMode 枚举在 System.IO.Compression, ZipFile 在 FileSystem, 两个都要加载。
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
if (Test-Path $outputAbs) { Remove-Item $outputAbs -Force }
Write-Host "==> 生成 $Output ..." -ForegroundColor Cyan
$zip = [System.IO.Compression.ZipFile]::Open($outputAbs, [System.IO.Compression.ZipArchiveMode]::Create)
try {
    foreach ($rel in $include) {
        $srcPath = [System.IO.Path]::Combine($root, $rel.Replace("/", "\"))
        $entry = $zip.CreateEntry($rel, [System.IO.Compression.CompressionLevel]::Optimal)
        $entry.LastWriteTime = (Get-Item -Path $srcPath -Force).LastWriteTime
        $fs = [System.IO.File]::OpenRead($srcPath)
        $es = $entry.Open()
        try { $fs.CopyTo($es) } finally { $fs.Dispose(); $es.Dispose() }
    }
} finally {
    $zip.Dispose()
}

# ---- 4. 内容校验 ----
Write-Host "==> 校验包内容 ..." -ForegroundColor Cyan
$z = [System.IO.Compression.ZipFile]::OpenRead($outputAbs)
try {
    $names = @($z.Entries | ForEach-Object { $_.FullName })
} finally {
    $z.Dispose()
}

# 必须包含 (本轮新增的钉钉 OAuth 文件, 缺一个部署即起不来)
$mustHave = @(
    "app/main.py",
    "app/dingtalk/oauth.py",
    "app/dingtalk/schemas.py",
    "app/dingtalk/router.py",
    "packages/harness/ainote/agents/graph/scoped_tool_node.py",
    "packages/harness/ainote/agents/graph/tool_binder.py",
    "packages/harness/ainote/agents/graph/dingtalk_runtime.py",
    "Dockerfile",
    "config.yaml",
    "mcp_servers.json",
    "uv.lock",
    "pyproject.toml",
    ".env.example"
)
$missing = @($mustHave | Where-Object { $names -notcontains $_ })

# 绝不允许混入: 大目录 / 密钥 / 缓存
$forbiddenRe = '(^|/)(\.venv|tests|__pycache__|\.git|\.pytest_cache|build|dist|wheels|mcp|mcp-server|\.cache|\.claude|\.vscode)(/|$)|\.egg-info'
$forbidden = @($names | Where-Object { $_ -match $forbiddenRe })
$forbiddenEnv = @($names | Where-Object {
    ($_ -match '(^|/)\.env($|/)') -or (($_ -match '(^|/)\.env\.') -and ($_ -ne ".env.example"))
})
# 非 ASCII 文件名: 云端 docker build 可能按系统代码页解包, 中文/特殊字符
# 文件名会触发 "string field contains invalid UTF-8" 构建失败。此类文件必须
# 在 .dockerignore 排除, 不允许进包。
$nonAscii = @($names | Where-Object {
    if ($_ -eq ".env.example") { return $false }
    foreach ($ch in $_.ToCharArray()) {
        if ([int]$ch -gt 127) { return $true }
    }
    return $false
})

$ok = $true
if ($missing.Count -gt 0) {
    $ok = $false
    Write-Host "  [FAIL] 包内缺少关键文件:" -ForegroundColor Red
    $missing | ForEach-Object { Write-Host "        $_" -ForegroundColor Red }
}
if ($forbidden.Count -gt 0) {
    $ok = $false
    Write-Host "  [FAIL] 包内混入了不应存在的条目:" -ForegroundColor Red
    $forbidden | Select-Object -First 10 | ForEach-Object { Write-Host "        $_" -ForegroundColor Red }
}
if ($forbiddenEnv.Count -gt 0) {
    $ok = $false
    Write-Host "  [FAIL] 包内混入了 .env 密钥文件:" -ForegroundColor Red
    $forbiddenEnv | ForEach-Object { Write-Host "        $_" -ForegroundColor Red }
}
if ($nonAscii.Count -gt 0) {
    $ok = $false
    Write-Host "  [FAIL] 包内含非 ASCII 文件名(云端构建会报 invalid UTF-8):" -ForegroundColor Red
    $nonAscii | ForEach-Object { Write-Host "        $_" -ForegroundColor Red }
    Write-Host "        请把这些文件加到 .dockerignore 再重打包" -ForegroundColor Red
}

$sizeMB = [math]::Round((Get-Item -Path $outputAbs).Length / 1MB, 2)
if ($ok) {
    Write-Host "  [OK] 校验通过: $($names.Count) 个条目, $sizeMB MB, 关键文件齐全, 无密钥/缓存/测试文件" -ForegroundColor Green
    Write-Host "  输出: $outputAbs" -ForegroundColor Cyan
} else {
    Write-Host "  [ERROR] 校验失败, 请修复后重试" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "下一步: 微信云托管控制台 → 上传这个 zip。" -ForegroundColor Yellow
Write-Host "  - 环境变量: 把 .env 的值照抄到云托管 (zip 里不含 .env)" -ForegroundColor Yellow
Write-Host "  - 新增必须配: DINGTALK_CLIENT_ID / DINGTALK_CLIENT_SECRET / DINGTALK_REDIRECT_URI" -ForegroundColor Yellow
Write-Host "  - 多副本坑: 控制台把「最小实例数」设为 1 (OAuth state 存在进程内存)" -ForegroundColor Yellow
