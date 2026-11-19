"""查 itsaplan 的导出/导入能力（在容器内检查源码与 API 路由）

避免 PowerShell 引号地狱：全部走 Python subprocess。
"""
import re
import subprocess

PODMAN = r"C:\Users\xuee\AppData\Local\Programs\Podman\podman.exe"
CT = "itsaplan-api-1"


def sh(cmd):
    """在容器内执行 sh 命令"""
    p = subprocess.run([PODMAN, "exec", CT, "sh", "-c", cmd],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=120)
    return p.stdout, p.stderr


print("=" * 70)
print("itsaplan 导出/导入能力检查")
print("=" * 70)

# 1. 模块文件
out, _ = sh("ls /app/apps/api/src/modules/import-export/")
print("\n【1】import-export 模块文件")
for line in out.strip().split("\n"):
    if line.strip():
        print("  " + line.strip())

# 2. 找路由字符串（含 /export 或 /import）
out, _ = sh(
    "grep -rhoE '/[a-zA-Z0-9/_:.-]*(export|import)[a-zA-Z0-9/_:.-]*' "
    "/app/apps/api/src/modules/import-export/ 2>/dev/null | sort -u | head -40"
)
print("\n【2】路由路径字面量")
for line in out.strip().split("\n"):
    if line.strip():
        print("  " + line.strip())

# 3. 找 HTTP 方法和 handler 名
out, _ = sh(
    "grep -rnE '\\.(get|post|put|delete)\\(' "
    "/app/apps/api/src/modules/import-export/index.ts 2>/dev/null | head -30"
)
print("\n【3】路由方法定义")
for line in out.strip().split("\n"):
    if line.strip():
        print("  " + line.strip()[:140])

# 4. 找 MCP 工具（可能导出/导入是 MCP 工具而非 HTTP）
out, _ = sh(
    "grep -rloE 'import_project|export_project|export_data|import_data' "
    "/app/apps/api/src 2>/dev/null | head -15"
)
print("\n【4】含 export/import 关键字的源码文件")
for line in out.strip().split("\n"):
    if line.strip():
        print("  " + line.strip())
