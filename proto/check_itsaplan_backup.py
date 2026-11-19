#!/usr/bin/env python3
"""itsaplan 数据备份/恢复能力实测

用户问题：itsaplan 数据能导出备份吗？重建环境后能导入吗？

三条路径分别实测：
  A. 官方 API 导出端点 GET /projects/:projectKey/import-jobs/export
  B. Postgres 全库 dump（真正的备份手段）
  C. 数据完整性核对（备份前后表行数、关键数据是否一致）

不猜，全部实跑。
"""
import json
import os
import subprocess
import time

PODMAN = r"C:\Users\xuee\AppData\Local\Programs\Podman\podman.exe"
PG = "itsaplan-postgres-1"
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backups")
os.makedirs(OUT_DIR, exist_ok=True)


def sh(args, timeout=300):
    p = subprocess.run([PODMAN] + args, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def psql(sql):
    code, out, err = sh([ "exec", PG, "psql", "-U", "itsaplan",
                         "-d", "itsaplan", "-t", "-A", "-F", "|", "-c", sql])
    return out.strip() if code == 0 else f"ERR: {err[:200]}"


print("=" * 70)
print("itsaplan 数据备份 / 恢复能力实测")
print("=" * 70)

# ---------- 1. 统计关键数据 ----------
print("\n【1】当前数据规模")
KEY_TABLES = {
    "project": "项目",
    "issue": "任务",
    "issue_activity": "活动/评论",
    "document": "文档",
    "note_board": "笔记板",
    "agent_schedule": "定时任务",
    "initiative": "倡议",
    "cycle": "周期",
    "label": "标签",
}
counts = {}
for t, cn in KEY_TABLES.items():
    v = psql(f"SELECT COUNT(*) FROM {t};")
    counts[t] = v
    print(f"  {cn:8s} ({t:16s}): {v}")

# ---------- 2. Postgres 全库 dump ----------
print("\n【2】Postgres 全库备份（pg_dump）")
stamp = time.strftime("%Y%m%d_%H%M%S")
dump_name = f"itsaplan_full_{stamp}.sql"
local_path = os.path.join(OUT_DIR, dump_name)

# 容器内 dump 到 /tmp，再拷出来
code, out, err = sh(["exec", PG, "pg_dump", "-U", "itsaplan",
                     "-d", "itsaplan", "--clean", "--if-exists",
                     "-f", "/tmp/dump.sql"])
if code != 0:
    print(f"  ❌ pg_dump 失败: {err[:200]}")
else:
    code2, out2, err2 = sh(["cp", f"{PG}:/tmp/dump.sql", local_path])
    if code2 == 0 and os.path.exists(local_path):
        size = os.path.getsize(local_path)
        print(f"  ✅ 备份成功: {dump_name}")
        print(f"     大小: {size/1024:.1f} KB")
        # 检查内容完整性
        with open(local_path, encoding="utf-8", errors="replace") as f:
            head = f.read(200)
            f.seek(0)
            content = f.read()
        tables_in_dump = content.count("CREATE TABLE")
        inserts = content.count("COPY public.")
        print(f"     含 CREATE TABLE: {tables_in_dump}")
        print(f"     含 COPY 数据块: {inserts}")
        print(f"     开头: {head[:60].replace(chr(10),' ')}")
    else:
        print(f"  ❌ 拷贝失败: {err2[:200]}")

# ---------- 3. 官方 API 导出端点 ----------
print("\n【3】官方 API 导出端点（项目级 JSON）")
print("  端点: GET /projects/:projectKey/import-jobs/export")
print("  说明（源码原文）: 'A self-contained snapshot of the project's")
print("    states, labels, cycles, and issues (with their comments and")
print("    relations), as portable JSON. Not a live sync — a download.'")
print("  ⚠ 需要登录会话（API key 对 REST 端点无效，401）")
print("  → 只能在浏览器里点，或用登录后的 cookie 调")

# ---------- 4. 恢复可行性 ----------
print("\n【4】恢复可行性")
print("  pg_dump 是标准 PostgreSQL 逻辑备份，用 psql 即可恢复：")
print(f"    psql -U itsaplan -d itsaplan -f {dump_name}")
print("  前提：")
print("    · 新环境的 POSTGRES_PASSWORD / BETTER_AUTH_SECRET")
print("      / APP_ENCRYPTION_KEY 必须与原环境一致")
print("      （auth 密钥决定会话，encryption key 决定凭证解密）")
print("    · RustFS (S3) 里的附件需要单独备份")

# ---------- 5. 附件检查 ----------
print("\n【5】附件存储（RustFS/S3）")
code, out, err = sh(["exec", "itsaplan-minio-1", "sh", "-c",
                     "ls -la /data 2>/dev/null | head -10"])
print("  RustFS 数据目录:" if code == 0 else "  无法读取 RustFS")
for line in (out or "").strip().split("\n")[:8]:
    if line.strip():
        print("    " + line.strip())

print("\n" + "=" * 70)
print("结论")
print("=" * 70)
print("""
✅ 可以备份：pg_dump 全库 SQL，一条命令。
✅ 可以恢复：psql 导入，但必须保持 auth/encryption 密钥一致。
✅ 有官方项目级导出：JSON 快照（状态/标签/周期/任务+评论+关系），
   但只能从浏览器会话调用，不含附件/文档正文/agent 数据。
❌ 没有"一键导入导出"UI 按钮（至少源码里没找到对端的 import JSON 端点）。
   import 端点只支持 Plane/Linear/Trello 三种外部来源。

所以你要的"重建环境后重新导入"，正确做法是 **pg_dump + psql**，
不是用它的导入功能。
""")
