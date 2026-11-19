# 修复 itsaplan 的 failed to fetch（IP 变化后必跑）
#
# 根因：itsaplan web 容器在启动时把 API_URL 写死注入到前端页面。
# iPhone 热点 IP 一变（172.20.10.2 → .5），页面还在请求旧 IP:
#   window.__ITSAPLAN_ENV__ = {"apiUrl":"http://172.20.10.2:3000"}
# → 浏览器 fetch 到失效地址 → failed to fetch
#
# 本脚本：检测当前 IP → 用新 IP 重建 web/api 容器 → 验证
# 何时跑：每次热点重连、IP 变了、页面报 failed to fetch 时

$ErrorActionPreference = 'Stop'
$PODMAN = "$env:LOCALAPPDATA\Programs\Podman\podman.exe"

function Pause-IfInteractive {
    if ([Environment]::UserInteractive -and -not $env:FFH_NO_PAUSE) {
        Read-Host "按回车退出"
    }
}

Write-Host ("=" * 68)
Write-Host "itsaplan IP 自适应修复（failed to fetch）" -ForegroundColor Cyan
Write-Host ("=" * 68)

# ---- 1. 当前 IP ----
$ip = (Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.IPAddress -like '172.20.*' } |
    Select-Object -First 1).IPAddress
if (-not $ip) {
    Write-Host "❌ 没找到 172.20.x 地址（热点可能没连）" -ForegroundColor Red
    Pause-IfInteractive
    exit 1
}
Write-Host "`n[1] 当前热点 IP: $ip" -ForegroundColor Green

# ---- 2. 现有容器的关键 env（保留密钥等）----
Write-Host "[2] 读取 api 容器现有配置..."
$raw = & $PODMAN inspect itsaplan-api-1 2>$null | Out-String
# 注意：不要用 $raw -match "Error" 判失败 ——
# 容器配置里可能含 Error 字样（env/标签/路径），会误判。
# 正确做法：尝试 ConvertFrom-Json，失败才算真读不到。
try {
    $info = $raw | ConvertFrom-Json -ErrorAction Stop
} catch {
    Write-Host "❌ 读不到 api 容器，检查 podman 是否在跑" -ForegroundColor Red
    Pause-IfInteractive
    exit 1
}
$apiEnv = @($info[0].Config.Env)
Write-Host ("    保留 " + $apiEnv.Count + " 个环境变量（密钥/数据库等）")

# ---- 3. 重建 api（改 APP_URL/API_URL）----
Write-Host "[3] 重建 api 容器..."
& $PODMAN rm -f itsaplan-api-1 2>&1 | Out-Null
$argsApi = @("run", "-d", "--name", "itsaplan-api-1",
    "--network", "itsaplan_default",
    "-p", "127.0.0.1:3010:3000", "--restart", "unless-stopped")
foreach ($e in $apiEnv) {
    if ($e -match "^(PATH|HOSTNAME|HOME|container)=") { continue }
    if ($e -match "^APP_URL=") { $e = "APP_URL=http://${ip}:3001" }
    elseif ($e -match "^API_URL=") { $e = "API_URL=http://${ip}:3000" }
    $argsApi += @("-e", $e)
}
# 确保存在
$hasApp = $apiEnv | Where-Object { $_ -match "^APP_URL=" }
$hasApi = $apiEnv | Where-Object { $_ -match "^API_URL=" }
if (-not $hasApp) { $argsApi += @("-e", "APP_URL=http://${ip}:3001") }
if (-not $hasApi) { $argsApi += @("-e", "API_URL=http://${ip}:3000") }
$argsApi += "ghcr.io/croffasia/itsaplan-api:latest"
& $PODMAN @argsApi 2>&1 | Out-Null
Write-Host "    ✅ api 已重建" -ForegroundColor Green

# ---- 4. 重建 web（改 API_URL）----
Write-Host "[4] 重建 web 容器..."
$rawW = & $PODMAN inspect itsaplan-web-1 2>$null | Out-String
$infoW = $rawW | ConvertFrom-Json
$webEnv = @($infoW[0].Config.Env)
& $PODMAN rm -f itsaplan-web-1 2>&1 | Out-Null
$argsWeb = @("run", "-d", "--name", "itsaplan-web-1",
    "--network", "itsaplan_default",
    "-p", "127.0.0.1:3002:3001", "--restart", "unless-stopped")
foreach ($e in $webEnv) {
    if ($e -match "^(PATH|HOSTNAME|HOME|container)=") { continue }
    if ($e -match "^API_URL=") { $e = "API_URL=http://${ip}:3000" }
    elseif ($e -match "^APP_URL=") { $e = "APP_URL=http://${ip}:3001" }
    $argsWeb += @("-e", $e)
}
$hasApiW = $webEnv | Where-Object { $_ -match "^API_URL=" }
$hasAppW = $webEnv | Where-Object { $_ -match "^APP_URL=" }
if (-not $hasApiW) { $argsWeb += @("-e", "API_URL=http://${ip}:3000") }
if (-not $hasAppW) { $argsWeb += @("-e", "APP_URL=http://${ip}:3001") }
$argsWeb += "ghcr.io/croffasia/itsaplan-web:latest"
& $PODMAN @argsWeb 2>&1 | Out-Null
Write-Host "    ✅ web 已重建" -ForegroundColor Green

# ---- 5. 等待并验证 ----
Write-Host "[5] 等待服务起来（最多 90 秒）..."
for ($i = 0; $i -lt 18; $i++) {
    Start-Sleep -Seconds 5
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:3001" -TimeoutSec 4 -UseBasicParsing
        if ($r.StatusCode -eq 200) {
            Write-Host ("    ✅ 页面就绪 (" + (($i + 1) * 5) + "s)") -ForegroundColor Green
            break
        }
    } catch {}
}

Write-Host "`n[6] 验证注入的 apiUrl:"
try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:3001" -TimeoutSec 8 -UseBasicParsing
    $m = [regex]::Match($r.Content, 'apiUrl\\?"?:\\?"([^"\\]+)')
    $injected = $m.Groups[1].Value
    Write-Host ("    页面注入: " + $injected)
    if ($injected -match [regex]::Escape($ip)) {
        Write-Host "    ✅ 指向当前 IP，正确" -ForegroundColor Green
    } else {
        Write-Host "    ⚠ 仍非当前 IP，可能需要再等几秒" -ForegroundColor Yellow
    }
} catch {
    Write-Host "    ⚠ 验证失败" -ForegroundColor Yellow
}

Write-Host "`n" + ("=" * 68)
Write-Host "完成" -ForegroundColor Green
Write-Host "另一台电脑访问： http://${ip}:3001" -ForegroundColor Cyan
Write-Host "`n下次热点 IP 变了、页面 failed to fetch，再跑本脚本即可。"
Pause-IfInteractive
