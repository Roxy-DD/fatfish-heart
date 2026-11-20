@echo off
chcp 65001 >nul
REM 修复 WSL mirrored 模式下 Docker 端口无法从 Windows/局域网访问
REM 用法：右键"以管理员身份运行"
echo 放行 WSL mirrored 入站...
Set-NetFirewallHyperVVMSetting -Name '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}' -DefaultInboundAction Allow
echo.
echo 当前状态：
Get-NetFirewallHyperVVMSetting | Select-Object Name, DefaultInboundAction
echo.
echo 完成后测试：curl -k https://172.20.10.5/login
pause