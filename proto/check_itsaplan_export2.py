"""深入看 itsaplan export.ts 的实现：导出什么、什么格式、能否导入"""
import subprocess

PODMAN = r"C:\Users\xuee\AppData\Local\Programs\Podman\podman.exe"
CT = "itsaplan-api-1"


def sh(cmd):
    p = subprocess.run([PODMAN, "exec", CT, "sh", "-c", cmd],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=120)
    return p.stdout, p.stderr


print("=" * 70)
print("itsaplan export.ts 实现")
print("=" * 70)
out, _ = sh("cat /app/apps/api/src/modules/import-export/export.ts")
print(out[:4000])

print("\n" + "=" * 70)
print("路由完整定义（index.ts 155 行附近）")
print("=" * 70)
out, _ = sh("sed -n '150,190p' /app/apps/api/src/modules/import-export/index.ts")
print(out)
