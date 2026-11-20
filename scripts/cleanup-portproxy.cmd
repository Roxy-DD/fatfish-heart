@echo off
chcp 65001 >nul
REM ============================================================
REM 环境残留清理脚本（WSL2 / Podman / itsaplan）
REM 背景：用户要重新配置虚拟环境和网络，清理旧残留
REM 用法：右键"以管理员身份运行"
REM
REM 注意：此脚本只清理【网络和系统层】残留。
REM       文件目录（podman machine、.wslconfig 备份等）
REM       已由 AI 在普通权限下清理并备份。
REM ============================================================

echo ============================================
echo 环境残留清理（管理员）
echo ============================================
echo.

echo [1/3] 清理 portproxy 转发规则...
echo 当前规则:
netsh interface portproxy show all
echo.
netsh interface portproxy delete v4tov4 listenport=3000 listenaddress=0.0.0.0
netsh interface portproxy delete v4tov4 listenport=3001 listenaddress=0.0.0.0
echo 清理后:
netsh interface portproxy show all
echo.

echo [2/3] 删除 itsaplan 防火墙规则...
netsh advfirewall firewall delete rule name="itsaplan port 3000" >nul 2>&1
netsh advfirewall firewall delete rule name="itsaplan port 3001" >nul 2>&1
echo 已删除 itsaplan 端口规则
echo.

echo [3/3] 删除 Podman Desktop 防火墙规则...
netsh advfirewall firewall delete rule name="Podman Desktop" >nul 2>&1
echo 已删除 Podman Desktop 规则
echo.

echo ============================================
echo 清理完成。剩余规则：
netsh interface portproxy show all
echo.
echo 按任意键退出。
pause >nul
