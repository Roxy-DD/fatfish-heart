@echo off
chcp 65001 >nul
REM ============================================================
REM 清理 Podman/itsaplan 遗留的 portproxy 转发规则
REM 背景：WSL2 + Podman 已删除，这两条规则指向不存在的后端
REM   3000 -> 127.0.0.1:3010  (itsaplan api)
REM   3001 -> 127.0.0.1:3002  (itsaplan web)
REM 用法：右键"以管理员身份运行"
REM ============================================================

echo 当前 portproxy 规则:
netsh interface portproxy show all
echo.

echo 正在删除遗留规则...
netsh interface portproxy delete v4tov4 listenport=3000 listenaddress=0.0.0.0
netsh interface portproxy delete v4tov4 listenport=3001 listenaddress=0.0.0.0

echo.
echo 清理后的规则:
netsh interface portproxy show all
echo.
echo 完成。按任意键退出。
pause >nul
