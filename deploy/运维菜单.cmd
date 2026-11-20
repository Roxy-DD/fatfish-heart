@echo off
chcp 65001 >nul
setlocal

REM ============================================================
REM  itsaplan 日常运维菜单
REM  用法：双击运行（无需管理员）
REM ============================================================

set "ROOT=%~dp0"
set "CADDY=%ROOT%deploy\caddy"
set "PLAN=%ROOT%deploy\itsaplan"
set "PF=-f docker-compose.yml -f docker-compose.override.yml"

:MENU
cls
echo ============================================
echo   itsaplan 运维菜单
echo ============================================
echo.
echo   [1] 查看状态
echo   [2] 启动全部
echo   [3] 停止全部（数据保留）
echo   [4] 重启全部
echo   [5] 查看日志（选服务）
echo   [6] 备份数据库
echo   [7] 检查 IP 与访问地址
echo   [8] 更新到最新版本
echo   [0] 退出
echo.
set /p CHOICE="请选择: "

if "%CHOICE%"=="1" goto STATUS
if "%CHOICE%"=="2" goto START
if "%CHOICE%"=="3" goto STOP
if "%CHOICE%"=="4" goto RESTART
if "%CHOICE%"=="5" goto LOGS
if "%CHOICE%"=="6" goto BACKUP
if "%CHOICE%"=="7" goto IPCHECK
if "%CHOICE%"=="8" goto UPDATE
if "%CHOICE%"=="0" exit /b 0
goto MENU

:STATUS
cls
echo === 容器状态 ===
docker ps -a --format "table {{.Names}}\t{{.Status}}" | findstr /I "caddy itsaplan"
echo.
echo === 访问测试 ===
curl.exe --noproxy "*" --ssl-no-revoke -s -o nul -w "  https://itsaplan.lan/login -> %%{http_code}\n" --max-time 10 https://itsaplan.lan/login
echo.
pause
goto MENU

:START
cls
echo === 启动 Caddy ===
pushd "%CADDY%"
docker compose up -d
popd
echo === 启动 itsaplan ===
pushd "%PLAN%"
docker compose %PF% up -d
popd
echo.
echo 完成。等待 20 秒后可访问 https://itsaplan.lan
pause
goto MENU

:STOP
cls
echo === 停止 itsaplan（数据在 volume 里，不会丢）===
pushd "%PLAN%"
docker compose %PF% stop
popd
echo === 停止 Caddy ===
pushd "%CADDY%"
docker compose stop
popd
echo.
echo 已停止。
pause
goto MENU

:RESTART
cls
docker restart caddy itsaplan-web itsaplan-api itsaplan-worker-1 itsaplan-bot-1 itsaplan-postgres-1 itsaplan-minio-1 >nul 2>&1
echo 已重启全部容器，等待 20 秒...
timeout /t 20 /nobreak >nul
curl.exe --noproxy "*" --ssl-no-revoke -s -o nul -w "  /login -> %%{http_code}\n" --max-time 10 https://itsaplan.lan/login
pause
goto MENU

:LOGS
cls
echo 可选服务：caddy / itsaplan-api / itsaplan-web / itsaplan-worker-1 / itsaplan-postgres-1
set /p SVC="输入服务名（直接回车看 api）: "
if "%SVC%"=="" set SVC=itsaplan-api
cls
echo === %SVC% 最近 50 行日志（Ctrl+C 退出）===
docker logs --tail 50 -f %SVC%
goto MENU

:BACKUP
cls
set "STAMP=%date:~0,4%%date:~5,2%%date:~8,2%_%time:~0,2%%time:~3,2%%time:~6,2%"
set "STAMP=%STAMP: =0%"
set "OUT=%ROOT%deploy\backups\itsaplan_%STAMP%.sql"
if not exist "%ROOT%deploy\backups" mkdir "%ROOT%deploy\backups"
echo 正在导出数据库...
docker exec itsaplan-postgres-1 pg_dump -U itsaplan -d itsaplan > "%OUT%"
if exist "%OUT%" (
    for %%A in ("%OUT%") do echo   [OK] %%~nxA  %%~zA 字节
) else (
    echo   [失败] 导出失败
)
echo.
pause
goto MENU

:IPCHECK
cls
echo === 当前网络 IP ===
powershell -NoProfile -Command "Get-NetIPConfiguration -InterfaceAlias WLAN -ErrorAction SilentlyContinue | ForEach-Object { $_.IPv4Address.IPAddress }"
echo.
echo === hosts 里的 itsaplan.lan ===
findstr /C:"itsaplan.lan" C:\Windows\System32\drivers\etc\hosts
echo.
echo === Caddy 配置里的地址 ===
findstr /C:"itsaplan.lan" "%CADDY%\Caddyfile" | findstr /V "#"
echo.
echo 如果 IP 变了，运行：
echo   python proto\set_lan_ip.py 新IP
echo  然后重启 caddy 并改 hosts
echo.
pause
goto MENU

:UPDATE
cls
echo === 更新 itsaplan 到最新版本 ===
echo 注意：会先拉取新镜像，然后重建容器。数据在 volume 里。
set /p CONFIRM="确认更新? (y/N): "
if /i not "%CONFIRM%"=="y" goto MENU
pushd "%PLAN%"
docker compose %PF% pull
docker compose %PF% up -d
popd
echo.
echo 更新完成。检查日志确认状态。
pause
goto MENU
