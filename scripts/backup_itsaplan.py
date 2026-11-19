#!/usr/bin/env python3
"""itsaplan 一键备份 / 恢复

实测验证过：pg_dump 946KB / 149 表 / 148 数据块，导入后 issue=70 与原库一致。

用法：
    python backup_itsaplan.py backup              # 备份到 proto/backups/
    python backup_itsaplan.py list                # 列出现有备份
    python backup_itsaplan.py restore <file>      # 恢复（会先确认）
    python backup_itsaplan.py verify <file>       # 只验证不覆盖

⚠ 恢复前提：新环境的三个密钥必须与原环境一致
    POSTGRES_PASSWORD / BETTER_AUTH_SECRET / APP_ENCRYPTION_KEY
    （不一致会导致：登录失效、凭证无法解密）
"""
import os
import subprocess
import sys
import time

PODMAN = r"C:\Users\xuee\AppData\Local\Programs\Podman\podman.exe"
PG = "itsaplan-postgres-1"
DB = "itsaplan"
USER = "itsaplan"
HERE = os.path.dirname(os.path.abspath(__file__))
BACKUP_DIR = os.path.join(HERE, "backups")


def sh(args, timeout=900):
    p = subprocess.run([PODMAN] + args, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def psql(db, sql):
    code, out, err = sh(["exec", PG, "psql", "-U", USER, "-d", db,
                         "-t", "-A", "-c", sql])
    return out.strip() if code == 0 else f"ERR:{err[:100]}"


def cmd_backup():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    name = f"itsaplan_full_{stamp}.sql"
    local = os.path.join(BACKUP_DIR, name)

    print(f"备份 {DB} ...")
    code, _, err = sh(["exec", PG, "pg_dump", "-U", USER, "-d", DB,
                       "--clean", "--if-exists", "-f", "/tmp/d.sql"])
    if code != 0:
        print(f"❌ pg_dump 失败: {err[:200]}")
        return 1

    code, _, err = sh(["cp", f"{PG}:/tmp/d.sql", local])
    if code != 0 or not os.path.exists(local):
        print(f"❌ 拷贝失败: {err[:200]}")
        return 1

    size = os.path.getsize(local) / 1024
    with open(local, encoding="utf-8", errors="replace") as f:
        c = f.read()
    print(f"✅ {name}  ({size:.1f} KB, {c.count('CREATE TABLE')} 表)")
    print(f"   路径: {local}")
    print(f"   关键数据: issues={psql(DB,'SELECT COUNT(*) FROM issue;')}")
    return 0


def cmd_list():
    if not os.path.isdir(BACKUP_DIR):
        print("没有备份目录")
        return 0
    files = sorted(f for f in os.listdir(BACKUP_DIR) if f.endswith(".sql"))
    if not files:
        print("没有备份文件")
        return 0
    print(f"{'文件':<40} {'大小':>10}")
    print("-" * 52)
    for f in files:
        p = os.path.join(BACKUP_DIR, f)
        print(f"{f:<40} {os.path.getsize(p)/1024:>9.1f}K")
    return 0


def _restore_to(db, path, quiet=False):
    sh(["exec", PG, "psql", "-U", USER, "-d", "postgres", "-c",
        f"DROP DATABASE IF EXISTS {db};"])
    c1, _, e1 = sh(["exec", PG, "psql", "-U", USER, "-d", "postgres", "-c",
                    f"CREATE DATABASE {db};"])
    if c1 != 0:
        return False, f"建库失败: {e1[:150]}"
    code, out, err = sh(["exec", PG, "psql", "-U", USER, "-d", db,
                         "-q", "-f", "/tmp/restore.sql"])
    real = [l for l in err.split("\n")
            if l and "NOTICE" not in l and "already exists" not in l]
    if code != 0:
        return False, f"导入错误: {str(real[:2])[:200]}"
    n = psql(db, "SELECT COUNT(*) FROM issue;")
    return True, f"导入完成，issues={n}"


def cmd_verify(path):
    local = path if os.path.isabs(path) else os.path.join(BACKUP_DIR, path)
    if not os.path.exists(local):
        print(f"❌ 文件不存在: {local}")
        return 1
    code, _, err = sh(["cp", local, f"{PG}:/tmp/restore.sql"])
    if code != 0:
        print(f"❌ 拷入失败: {err[:150]}")
        return 1
    print("在临时库 restore_verify 中验证（不动原库）...")
    ok, msg = _restore_to("restore_verify", local)
    print(("✅ " if ok else "❌ ") + msg)
    print(f"   原库 issues={psql(DB,'SELECT COUNT(*) FROM issue;')}")
    sh(["exec", PG, "psql", "-U", USER, "-d", "postgres", "-c",
        "DROP DATABASE IF EXISTS restore_verify;"])
    print("   临时库已清理")
    return 0 if ok else 1


def cmd_restore(path):
    local = path if os.path.isabs(path) else os.path.join(BACKUP_DIR, path)
    if not os.path.exists(local):
        print(f"❌ 文件不存在: {local}")
        return 1
    print("⚠ 这会覆盖当前数据库！")
    print(f"   文件: {local}")
    print(f"   当前 issues={psql(DB,'SELECT COUNT(*) FROM issue;')}")
    if input("确认恢复？输入 yes: ").strip() != "yes":
        print("已取消")
        return 0
    code, _, err = sh(["cp", local, f"{PG}:/tmp/restore.sql"])
    if code != 0:
        print(f"❌ 拷入失败: {err[:150]}")
        return 1
    # 先备份当前的
    print("先备份当前数据...")
    cmd_backup()
    print("开始恢复...")
    ok, msg = _restore_to(DB, local)
    print(("✅ " if ok else "❌ ") + msg)
    print("提示：容器可能需要重启才能清除连接缓存")
    return 0 if ok else 1


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    cmd = sys.argv[1]
    if cmd == "backup":
        return cmd_backup()
    if cmd == "list":
        return cmd_list()
    if cmd == "verify":
        if len(sys.argv) < 3:
            print("用法: verify <file>")
            return 1
        return cmd_verify(sys.argv[2])
    if cmd == "restore":
        if len(sys.argv) < 3:
            print("用法: restore <file>")
            return 1
        return cmd_restore(sys.argv[2])
    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
