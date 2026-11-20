"""修复 itsaplan 迁移记录不一致（恢复备份后 migrate 失败）

问题：
  备份的迁移记录停在 idx=181（created_at=1794631138557），
  但当前镜像有 187 条迁移（idx 0-186）。
  migrate 于是尝试执行 182-186，其中 182 要建
  issue_activity_reaction 表 —— 该表在备份里已存在 → 42P07 失败。

修复：
  182 只补记录（表已存在）
  183-186 真正执行（都是 ALTER，安全）

用法：
  python fix_itsaplan_migrations.py           # 检查
  python fix_itsaplan_migrations.py --apply   # 执行修复
"""
import argparse
import json
import subprocess
import sys
import tempfile
import os

PG = "itsaplan-postgres-1"
IMG = "ghcr.io/croffasia/itsaplan-api:latest"

# 备份停在哪条
BACKUP_LAST_WHEN = 1794631138557   # idx=181

# 需要处理的迁移
MIGRATIONS = [
    # (idx, when, tag, 是否补记录跳过)
    (182, 1794842060664, "0182_comment_reactions", True),        # 表已存在
    (183, 1794844437414, "0183_reaction_notification", False),
    (184, 1794854169132, "0184_color_themes", False),
    (185, 1794957343836, "0185_agent_run_api_trigger", False),
    (186, 1794959471732, "0186_agent_run_requested_by", False),
]


def sh(args, inp=None, timeout=180):
    p = subprocess.run(args, input=inp, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def psql(sql):
    """在 postgres 容器里执行 SQL"""
    with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False,
                                     encoding="utf-8") as f:
        f.write(sql)
        path = f.name
    try:
        sh(["docker", "cp", path, f"{PG}:/tmp/_fix.sql"])
        c, o, e = sh(["docker", "exec", PG, "psql", "-U", "itsaplan",
                      "-d", "itsaplan", "-t", "-A", "-v", "ON_ERROR_STOP=1",
                      "-f", "/tmp/_fix.sql"])
        return c, o, e
    finally:
        os.unlink(path)


def get_applied_max():
    c, o, e = psql("SELECT COALESCE(MAX(created_at),0) FROM drizzle.__drizzle_migrations;")
    try:
        return int(o.strip().split("\n")[0])
    except Exception:
        return None


def get_applied_count():
    c, o, e = psql("SELECT COUNT(*) FROM drizzle.__drizzle_migrations;")
    try:
        return int(o.strip().split("\n")[0])
    except Exception:
        return None


def read_migration_sql(tag):
    """从镜像里读迁移 SQL"""
    c, o, e = sh(["docker", "run", "--rm", "--entrypoint", "cat", IMG,
                  f"/app/packages/db/drizzle/{tag}.sql"])
    if c != 0:
        raise RuntimeError(f"读取 {tag}.sql 失败: {e[:200]}")
    return o


def split_statements(sql):
    """按 --> statement-breakpoint 切分"""
    parts = sql.split("--> statement-breakpoint")
    return [p.strip() for p in parts if p.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="执行修复")
    args = ap.parse_args()

    print("=" * 66)
    print("itsaplan 迁移记录修复")
    print("=" * 66)

    cur_max = get_applied_max()
    cur_cnt = get_applied_count()
    print(f"\n  当前迁移记录数: {cur_cnt}")
    print(f"  当前最新 created_at: {cur_max}")
    print(f"  备份时的值: {BACKUP_LAST_WHEN}")

    if cur_max is None:
        print("  ❌ 读不到迁移表，检查容器名/数据库名")
        return 1

    if cur_max >= MIGRATIONS[-1][1]:
        print("\n  ✅ 已经是最新，无需修复")
        return 0

    print(f"\n  需要处理 {len(MIGRATIONS)} 条迁移：")
    for idx, when, tag, skip in MIGRATIONS:
        mark = "补记录(表已存在)" if skip else "执行SQL"
        already = "✅已应用" if cur_max >= when else "待处理"
        print(f"    idx={idx} {tag:38s} {already}  [{mark}]")

    if not args.apply:
        print("\n  这是检查模式。加 --apply 执行修复。")
        return 0

    print("\n  开始修复...")
    for idx, when, tag, skip in MIGRATIONS:
        if cur_max >= when:
            print(f"    {tag}: 已应用，跳过")
            continue

        if skip:
            # 只补记录
            sql = (f"INSERT INTO drizzle.__drizzle_migrations (hash, created_at) "
                   f"VALUES ('{tag}-manual-skip', {when});")
            c, o, e = psql(sql)
            if c != 0:
                print(f"    ❌ {tag} 补记录失败: {e[:200]}")
                return 1
            print(f"    ✅ {tag}: 补记录（表已存在，跳过建表）")
        else:
            # 真执行 SQL
            try:
                sql = read_migration_sql(tag)
            except Exception as ex:
                print(f"    ❌ {ex}")
                return 1
            stmts = split_statements(sql)
            ok = 0
            for s in stmts:
                c, o, e = psql(s)
                if c != 0:
                    # 已存在/已删的容错
                    if "already exists" in e or "does not exist" in e:
                        print(f"      跳过（幂等）: {e.strip().splitlines()[-1][:80]}")
                        ok += 1
                        continue
                    print(f"    ❌ {tag} 执行失败: {e[:300]}")
                    return 1
                ok += 1
            # 补记录
            rec = (f"INSERT INTO drizzle.__drizzle_migrations (hash, created_at) "
                   f"VALUES ('{tag}-manual', {when});")
            c, o, e = psql(rec)
            print(f"    ✅ {tag}: 执行 {ok} 条语句 + 补记录")

    new_max = get_applied_max()
    new_cnt = get_applied_count()
    print(f"\n  修复后：{new_cnt} 条记录，最新 created_at={new_max}")
    print("\n  ✅ 完成。现在可以正常 start 服务了。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
