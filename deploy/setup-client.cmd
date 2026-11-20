@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

REM ============================================================
REM  itsaplan 客户端一键接入
REM  作用：自动配 hosts + 导入 Caddy 根证书，之后浏览器即可访问
REM  用法：右键"以管理员身份运行"
REM ============================================================

set "HOSTS=C:\Windows\System32\drivers\etc\hosts"
set "CERT=%~dp0caddy-root.crt"
set "DOMAIN=itsaplan.lan"

echo ============================================
echo  itsaplan 客户端接入
echo ============================================
echo.

REM ---- 检查管理员 ----
net session >nul 2>&1
if errorlevel 1 (
    echo [错误] 需要管理员权限。
    echo        请右键本文件，选择"以管理员身份运行"。
    echo.
    pause
    exit /b 1
)

REM ---- 检查证书文件 ----
if not exist "%CERT%" (
    echo [错误] 找不到 caddy-root.crt
    echo        请把本脚本和 caddy-root.crt 放在同一个文件夹里。
    echo        当前目录：%~dp0
    echo.
    pause
    exit /b 1
)

REM ---- 自动检测主机 IP（如果用户没传参）----
set "SERVER_IP=%~1"
if "%SERVER_IP%"=="" (
    echo [1/3] 请输入主机（跑 Docker 的那台）的局域网 IP
    echo       在主机上运行这条命令可以查到：
    echo       Get-NetIPConfiguration -InterfaceAlias WLAN
    echo.
    set /p SERVER_IP="IP 地址（如 172.20.10.5）: "
)

if "%SERVER_IP%"=="" (
    echo [错误] IP 不能为空
    pause
    exit /b 1
)

echo.
echo 主机 IP: %SERVER_IP%
echo 域名:    %DOMAIN%
echo.

REM ---- 第 1 步：配置 hosts ----
echo [1/3] 配置 hosts...
findstr /C:"%DOMAIN%" "%HOSTS%" >nul 2>&1
if not errorlevel 1 (
    echo       已存在 %DOMAIN% 记录，先删除旧行...
    powershell -NoProfile -Command "$h='%HOSTS%'; $c=Get-Content $h | Where-Object { $_ -notmatch '%DOMAIN%' }; Set-Content -Path $h -Value $c -Encoding ASCII"
)
echo %SERVER_IP%  %DOMAIN%>> "%HOSTS%"
if errorlevel 1 (
    echo       [失败] 无法写入 hosts
    pause
    exit /b 1
)
echo       [OK] hosts 已更新：%SERVER_IP%  %DOMAIN%

REM ---- 第 2 步：导入根证书 ----
echo.
echo [2/3] 导入根证书...
certutil -addstore -f "ROOT" "%CERT%" >nul 2>&1
if errorlevel 1 (
    echo       [警告] certutil 导入失败，尝试 PowerShell 方式...
    powershell -NoProfile -Command "Import-Certificate -FilePath '%CERT%' -CertStoreLocation 'Cert:\LocalMachine\Root'" >nul 2>&1
    if errorlevel 1 (
        echo       [失败] 证书导入失败
        echo        手动方法：双击 caddy-root.crt - 安装证书 -
        echo        本地计算机 - 受信任的根证书颁发机构
    ) else (
        echo       [OK] 证书已导入（PowerShell）
    )
) else (
    echo       [OK] 证书已导入到"受信任的根证书颁发机构"
)

REM ---- 第 3 步：验证 ----
echo.
echo [3/3] 验证连接...
ping -n 1 -w 2000 %SERVER_IP% >nul 2>&1
if errorlevel 1 (
    echo       [警告] ping 不通 %SERVER_IP%
    echo        可能原因：不在同一局域网 / 主机没开机 / IP 变了
) else (
    echo       [OK] 主机可达
)

echo.
echo ============================================
echo  配置完成！
echo ============================================
echo.
echo   浏览器打开：  https://%DOMAIN%
echo.
echo   如果提示证书错误，请：
echo     1. 完全关闭浏览器再打开
echo     2. 确认证书导入到了"受信任的根证书颁发机构"
echo.
echo   主机 IP 变了怎么办：
echo     重新运行本脚本，输入新 IP 即可。
echo.

REM ---- 可选：直接打开浏览器 ----
set /p OPEN="现在打开浏览器? (y/N): "
if /i "%OPEN%"=="y" start https://%DOMAIN%

echo.
pause
