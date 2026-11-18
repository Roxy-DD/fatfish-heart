@echo off
REM itsaplan MCP 调用助手（给 headless 用）
REM 用法: itsaplan-call.cmd <工具名> <JSON参数>
REM 例:   itsaplan-call.cmd get_current_user {}
python "%~dp0itsaplan_call.py" %*
