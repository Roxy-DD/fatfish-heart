# 让 itsaplan 在局域网内可访问（iPhone 热点 / 同一 WiFi）
#
# 用法：
#   1. 右键本文件 → "使用 PowerShell 运行"（必须在管理员窗口里）
#      或者：以管理员身份打开 PowerShell，cd 到本目录，执行：
#          .\enable-lan-access.ps1
#   2. 看最后的输出，把访问地址告诉另一台电脑
#
# 为什么需要这个脚本：
#   Podman 6.x 在 Windows/WSL 上做端口转发时，
#   只绑定到 127.0.0.1（回环），不监听局域网网卡。
#   实测：从本机用热点 IP 访问自己也会被拒绝。
#   所以需要在 Windows 层加一层 portproxy，把 0.0.0.0:端口 转发到 127.0.0.1:端口。
#
# 安全性说明：
#   · 只转发 3000（api）和 3001（web）两个端口
#   · 只放行这两个端口的入站，不开其他
#   · 不影响 itsaplan 自身配置，随时可撤销（见脚本末尾）

$ErrorActionPreference = 'Stop'

# ---- 只在交互窗口里暂停 ----
# Read-Host 在非交互环境（如 -File 调用、CI、后台运行）会一直卡住，
# 所以包一层：只有真正的交互式控制台才等待按键。
function Pause-IfInteractive {
    if ([Environment]::UserInteractive -and -not $env:FFH_NO_PAUSE) {
        Read-Host "按回车退出"
    }
}

# ---- 权限检查 ----
$isAdmin = ([Security.Principal.WindowsPrincipal]`
  [Security.Principal.WindowsIdentity]::GetCurrent()
).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin) {
    Write-Host ""
    Write-Host "❌ 需要管理员权限" -ForegroundColor Red
    Write-Host ""
    Write-Host "请这样做：" -ForegroundColor Yellow
    Write-Host "  1. 按 Win 键，输入 PowerShell"
    Write-Host "  2. 右键 'Windows PowerShell' → 以管理员身份运行"
    Write-Host "  3. 执行："
    Write-Host "     cd '$PSScriptRoot'"
    Write-Host "     .\enable-lan-access.ps1"
    Write-Host ""
    Pause-IfInteractive
    exit 1
}

Write-Host "=" * 66
Write-Host "让 itsaplan 在局域网内可访问"
Write-Host "=" * 66

# ---- 1. 检测当前局域网 IP ----
Write-Host ""
Write-Host "[1] 检测局域网 IP"

$candidates = Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object {
        $_.IPAddress -notmatch '^127\.' -and
        $_.IPAddress -notmatch '^169\.254\.' -and      # 链路本地，不可用
        $_.IPAddress -notmatch '^192\.168\.(88|144|160)\.'  # VMware/WSL 虚拟网卡
    } |
    Select-Object IPAddress, InterfaceAlias

if (-not $candidates) {
    Write-Host "  ❌ 没找到可用的局域网 IP" -ForegroundColor Red
    Write-Host "     请确认已连接 WiFi 或热点"
    Pause-IfInteractive
    exit 1
}

Write-Host "  找到这些地址：" -ForegroundColor Green
foreach ($c in $candidates) {
    Write-Host ("    " + $c.IPAddress + "  (" + $c.InterfaceAlias + ")")
}

# iPhone 热点通常是 172.20.10.x
$lanIp = ($candidates | Where-Object { $_.IPAddress -like '172.20.10.*' } |
    Select-Object -First 1).IPAddress
if (-not $lanIp) {
    $lanIp = $candidates[0].IPAddress
}
Write-Host ""
Write-Host ("  将使用: " + $lanIp) -ForegroundColor Cyan

# ---- 2. 确认服务在跑 ----
Write-Host ""
Write-Host "[2] 检查 itsaplan 是否在运行"

$ports = @(3000, 3001)
foreach ($p in $ports) {
    $listening = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue
    if ($listening) {
        Write-Host ("  ✅ 端口 " + $p + " 正在监听") -ForegroundColor Green
    } else {
        Write-Host ("  ⚠ 端口 " + $p + " 没有监听（服务可能没启动）") -ForegroundColor Yellow
    }
}

# ---- 3. 添加 portproxy ----
Write-Host ""
Write-Host "[3] 添加端口转发（0.0.0.0 → 127.0.0.1）"

foreach ($p in $ports) {
    # 先删旧的（幂等，重复跑不会报错）
    netsh interface portproxy delete v4tov4 listenport=$p listenaddress=0.0.0.0 2>$null | Out-Null
    $r = netsh interface portproxy add v4tov4 listenport=$p listenaddress=0.0.0.0 connectport=$p connectaddress=127.0.0.1 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host ("  ✅ " + $p + " 已转发") -ForegroundColor Green
    } else {
        Write-Host ("  ❌ " + $p + " 失败: " + $r) -ForegroundColor Red
    }
}

# ---- 4. 防火墙放行 ----
Write-Host ""
Write-Host "[4] 配置防火墙放行"

foreach ($p in $ports) {
    $ruleName = "itsaplan port $p"
    # 先删同名规则（幂等）
    Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue |
        Remove-NetFirewallRule -ErrorAction SilentlyContinue
    try {
        New-NetFirewallRule -DisplayName $ruleName `
            -Direction Inbound -Protocol TCP -LocalPort $p `
            -Action Allow -Profile Any `
            -Description "itsaplan 局域网访问" | Out-Null
        Write-Host ("  ✅ 已放行 " + $p) -ForegroundColor Green
    } catch {
        Write-Host ("  ❌ 放行 " + $p + " 失败: " + $_.Exception.Message) -ForegroundColor Red
    }
}

# ---- 5. 验证 ----
Write-Host ""
Write-Host "[5] 验证（从本机用局域网 IP 访问自己）"

Start-Sleep -Seconds 2
foreach ($p in $ports) {
    $url = "http://" + $lanIp + ":" + $p
    try {
        $resp = Invoke-WebRequest -Uri $url -TimeoutSec 8 -UseBasicParsing
        Write-Host ("  ✅ " + $url + " → HTTP " + $resp.StatusCode) -ForegroundColor Green
    } catch {
        $code = $_.Exception.Response.StatusCode.value__
        if ($code) {
            Write-Host ("  ✅ " + $url + " → HTTP " + $code + "（能连上，有响应）") -ForegroundColor Green
        } else {
            Write-Host ("  ❌ " + $url + " → 连不上") -ForegroundColor Red
        }
    }
}

# ---- 结果 ----
Write-Host ""
Write-Host ("=" * 66)
Write-Host "完成" -ForegroundColor Green
Write-Host ("=" * 66)
Write-Host ""
Write-Host "另一台电脑访问：" -ForegroundColor Cyan
Write-Host ("    网页界面:  http://" + $lanIp + ":3001")
Write-Host ("    API/MCP:   http://" + $lanIp + ":3000")
Write-Host ""
Write-Host "注意：" -ForegroundColor Yellow
Write-Host "  · iPhone 热点的 IP 每次重连可能变，变了要重跑本脚本"
Write-Host "  · 如果另一台电脑还是连不上，可能是 iPhone 热点开了"
Write-Host "    「客户端隔离」，那样同一热点下的设备互相看不到"
Write-Host ""
Write-Host "撤销（恢复成本机访问）：" -ForegroundColor DarkGray
Write-Host "    netsh interface portproxy reset" -ForegroundColor DarkGray
Write-Host "    Get-NetFirewallRule -DisplayName 'itsaplan port *' | Remove-NetFirewallRule" -ForegroundColor DarkGray
Write-Host ""
Pause-IfInteractive
