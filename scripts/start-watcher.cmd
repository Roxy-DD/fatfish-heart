@echo off
REM ────────────────────────────────────────────────────────────
REM itsaplan 监听服务启动器
REM
REM 作用：用户走了之后，我在 DSH 里不在场。
REM       这个服务轮询 itsaplan，发现新任务就拉起 headless 处理。
REM
REM 用法：双击本文件
REM       或命令行：scripts\start-watcher.cmd
REM
REM 停止：关掉那个窗口，或 Ctrl+C
REM ────────────────────────────────────────────────────────────

title itsaplan watcher
cd /d "%~dp0..\proto"

echo ============================================================
echo itsaplan 监听服务
echo ============================================================
echo.
echo 工作目录: %CD%
echo 日志:     watch.log
echo 状态:     watch_state.json
echo.
echo 按 Ctrl+C 停止
echo ============================================================
echo.

python watch_itsaplan.py

echo.
echo 服务已停止
pause
