"""E-493 自动备份机制回归

背景（真实数据，不是假设）：
  2026-11-16 检查：生产库 529 条记忆，0 个备份。
  → 容灾只停在「丢了能救回来」，但没人会触发备份。

方案（不预设门槛，找天然事务边界）：
  在 mem_sleep 的 apply（所有写盘完成）之后自动备份。
  · 由模型判断「一段工作结束」触发，而不是拍脑袋定「每 N 分钟」
  · 符合「判断交模型，机械交系统」
  · 备份失败不能让整理失败（增益，不是主流程）

本测试验证：
  ① sleep_apply 后确实生成备份
  ② 备份内容有效（verify 通过）
  ③ 校验不靠行数 —— 逐字段指纹一致（E-420 方法）
  ④ prune_backups 只留最近 N 份，且不碰记忆本体
  ⑤ 备份失败不阻断整理（payload.auto_backup=False 或异常注入）
  ⑥ 备份不进 git（.gitignore 覆盖）

全部在临时库上跑，不碰生产库。
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import warnings

warnings.filterwarnings("ignore")

HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(HERE, "cosmos_mem")
sys.path.insert(0, MEM)

from store_v2 import MemoryStore          # noqa: E402
import backup as bk                       # noqa: E402
import cli_mcp                            # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append({"name": name, "ok": bool(cond), "detail": str(detail)[:150]})
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  {detail}" if detail else ""))


def fingerprint(db_path, tables=None):
    """逐字段指纹（E-420 方法）：所有行所有列排序后 hash"""
    import hashlib
    con = sqlite3.connect(db_path)
    if tables is None:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    h = hashlib.md5()
    for t in tables:
        try:
            rows = con.execute(f"SELECT * FROM {t}").fetchall()
        except Exception:
            continue
        for r in sorted(rows, key=lambda x: repr(x)):
            h.update(repr(r).encode("utf-8", "replace"))
    con.close()
    return h.hexdigest()


print("=" * 72)
print("E-493 自动备份机制回归")
print("=" * 72)

TMP = tempfile.mkdtemp(prefix="e493_", dir=HERE)
DB = os.path.join(TMP, "test.db")

try:
    # ---------- ① 建一个临时库并写入记忆 ----------
    print("\n【1】准备临时库")
    st = MemoryStore(DB)
    st.add("自动备份测试记忆A", atmosphere="test")
    st.add("自动备份测试记忆B", atmosphere="test")
    n0 = st.con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
    check("临时库有记忆", n0 >= 2, f"episodes={n0}")
    st.con.close()

    # ---------- ② sleep_apply 后自动备份 ----------
    print("\n【2】sleep_apply 自动备份")
    st = MemoryStore(DB)
    r = cli_mcp.cmd_sleep_apply(st, {"actions": [], "advance_cursor": False})
    st.con.close()

    check("sleep_apply 成功", r.get("ok") is True, str(r.get("error", "")))
    ab = r.get("auto_backup") or {}
    check("返回 auto_backup 字段", bool(ab), str(ab)[:100])
    check("备份成功", ab.get("ok") is True, str(ab.get("error", ""))[:100])

    backups = bk.list_backups(DB)
    check("生成了备份文件", len(backups) >= 1, f"{len(backups)} 份")
    if backups:
        bpath = backups[0]["path"]
        check("备份文件非空",
              os.path.getsize(bpath) > 1000,
              f"{os.path.getsize(bpath)} B")

        # ---------- ③ 备份内容有效（逐字段指纹） ----------
        print("\n【3】备份内容校验（不靠行数）")
        v = bk.verify_backup(DB, bpath)
        check("verify_backup 通过", v.get("ok") is True, str(v)[:120])

        f_src = fingerprint(DB)
        f_bak = fingerprint(bpath)
        check("逐字段指纹一致", f_src == f_bak,
              f"{f_src[:12]} vs {f_bak[:12]}")

    # ---------- ③b 备份后写入审计，verify 不能假失败 ----------
    print("\n【3b】volatile 表增长不产生假失败（E-494）")
    st = MemoryStore(DB)
    n_audit_before = st.con.execute(
        "SELECT COUNT(*) FROM audit_log").fetchone()[0]
    st.con.close()
    # 用 cmd_backup 走完整路径，它会记录 "备份数据库" 审计
    cli_mcp.cmd_backup(st := MemoryStore(DB), {"action": "backup"})
    st.con.close()
    bp2 = bk.list_backups(DB)[0]["path"]
    st = MemoryStore(DB)
    v2 = bk.verify_backup(DB, bp2)
    st.con.close()
    check("备份后审计增长，verify 仍通过",
          v2.get("ok") is True,
          f"volatile_diff={v2.get('volatile_diff')} counts={v2.get('counts',{}).get('audit_log')}")

    # ---------- ④ prune 不碰记忆本体 ----------
    print("\n【4】prune 只留最近 N 份，不碰记忆本体")
    for i in range(14):
        with open(os.path.join(TMP, "x"), "w") as f:
            f.write(str(i))
        bk.backup_db(DB, verify=False)
        import time
        time.sleep(0.01)
    before_n = len(bk.list_backups(DB))
    before_fp = fingerprint(DB)

    pr = bk.prune_backups(DB, keep=10)
    after_n = len(bk.list_backups(DB))
    after_fp = fingerprint(DB)

    check("prune 成功", pr.get("ok") is True, str(pr)[:100])
    check("备份数降到 10", after_n == 10, f"{before_n} → {after_n}")
    check("记忆本体指纹未变", before_fp == after_fp,
          f"{before_fp[:12]} vs {after_fp[:12]}")

    # ---------- ⑤ 备份失败不阻断整理 ----------
    print("\n【5】备份失败不阻断整理")
    st = MemoryStore(DB)
    r2 = cli_mcp.cmd_sleep_apply(
        st, {"actions": [], "advance_cursor": False,
             "auto_backup": False})
    st.con.close()
    check("关掉自动备份时整理仍成功", r2.get("ok") is True)
    check("auto_backup 标记为跳过",
          r2.get("auto_backup") is None,
          str(r2.get("auto_backup")))

    # ---------- ⑥ gitignore 覆盖 ----------
    print("\n【6】备份文件不进 git")
    gr = subprocess.run(
        ["git", "check-ignore", "-v",
         "proto/cosmos_mem/dsh_mem.db.backup.20991231_235959"],
        cwd=os.path.dirname(HERE), capture_output=True, text=True,
        encoding="utf-8")
    check("*.db.backup.* 已被忽略", gr.returncode == 0,
          (gr.stdout or "").strip()[:100])

finally:
    shutil.rmtree(TMP, ignore_errors=True)

# ---------- 汇总 ----------
print("\n" + "=" * 72)
ok = sum(1 for x in results if x["ok"])
print(f"结果：{ok}/{len(results)} 通过")
for x in results:
    if not x["ok"]:
        print(f"  ❌ {x['name']}  {x['detail']}")
print("=" * 72)

out = {"experiment": "E-493", "n_pass": ok, "n_total": len(results),
       "results": results}
p = os.path.join(HERE, "results", "e493_auto_backup.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 {p}")

sys.exit(0 if ok == len(results) else 1)
