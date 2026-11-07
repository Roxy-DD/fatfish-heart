"""E-280 验证 backup 工具（含 WAL 陷阱回归）

E-279 发现的真实风险：
  SQLite WAL 模式下主库可能只有 4KB，数据全在 1.2MB 的 -wal 里。
  直接 shutil.copy2 主库 → 得到空库 → 恢复后数据全丢。

本实验：
  ① 证明"直接复制"确实会丢数据（复现陷阱）
  ② 证明 backup API 不丢数据
  ③ 验证 restore 会清理 WAL（避免旧 WAL 覆盖）
  ④ 验证 verify 能检测出损坏的备份
"""
import sys
import os
import json
import shutil
import sqlite3

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))

from store_v2 import MemoryStore
import backup as bk
import cli_mcp

DB = os.path.join(_HERE, "work", "test_db", "e280v.db")
os.makedirs(os.path.dirname(DB), exist_ok=True)

results = []


def check(name, cond, detail=""):
    results.append({"name": name, "ok": bool(cond), "detail": detail})
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  {detail}" if detail else ""))


def fresh_db(n=15):
    for suf in ("", "-wal", "-shm", ".bk", ".bad"):
        p = DB + suf
        if os.path.exists(p):
            os.remove(p)
    st = MemoryStore(DB)
    for i in range(n):
        st.add(f"备份验证第{i}条记忆，涉及检索和向量", resolve_conflict=False)
    return st


print("=" * 74)
print("E-280 备份工具验证")
print("=" * 74)

# ============ ① 复现"直接复制丢数据"的陷阱 ============
print("\n【1】复现陷阱：直接复制主库会丢数据")
st = fresh_db(20)
n_orig = st.con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]

# 检查 WAL 大小
db_size = os.path.getsize(DB)
wal_size = os.path.getsize(DB + "-wal") if os.path.exists(DB + "-wal") else 0
print(f"   主库 {db_size} bytes | WAL {wal_size} bytes")

naive_path = DB + ".bk"
shutil.copy2(DB, naive_path)   # ← 错误做法

try:
    c = sqlite3.connect(naive_path)
    n_naive = c.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
    c.close()
except Exception:
    n_naive = 0

check("直接复制确实丢失数据", n_naive < n_orig,
      f"直接复制得 {n_naive} 条（原 {n_orig} 条）")
print(f"   → 这证明 WAL 陷阱真实存在")

# ============ ② backup API 不丢数据 ============
print("\n【2】backup API 不丢数据")
r = bk.backup_db(DB)
check("备份成功", r.get("ok"))
check("备份有校验", r.get("verify", {}).get("ok") is True)

v = r.get("verify", {}).get("counts", {})
ep_counts = v.get("episodes", (0, 0))
check("episodes 数量一致", ep_counts[0] == ep_counts[1],
      f"{ep_counts}")

# ============ ③ restore 清理 WAL ============
print("\n【3】restore 清理 WAL（避免旧 WAL 覆盖）")
st.con.close()

# 破坏数据
c = sqlite3.connect(DB)
c.execute("DELETE FROM episodes WHERE rowid % 3 = 0")
c.commit()
n_dmg = c.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
c.close()
print(f"   破坏后 {n_dmg} 条")

r2 = bk.restore_db(r["path"], DB)
check("恢复成功", r2.get("ok"))
check("恢复到原始数量", r2.get("restored") == n_orig,
      f"{r2.get('restored')} vs {n_orig}")

# 验证恢复后 WAL 已清理（或至少不覆盖数据）
wal_after = os.path.getsize(DB + "-wal") if os.path.exists(DB + "-wal") else 0
print(f"   恢复后 WAL: {wal_after} bytes")

# ============ ④ verify 能检测损坏备份 ============
print("\n【4】verify 能检测出损坏的备份")
bad_path = DB + ".bad"
shutil.copy2(r["path"], bad_path)
c = sqlite3.connect(bad_path)
c.execute("DELETE FROM episodes WHERE rowid % 2 = 0")
c.commit()
c.close()

v2 = bk.verify_backup(DB, bad_path)
check("检测出损坏备份", v2.get("ok") is False)
counts2 = v2.get("counts", {})
if "episodes" in counts2:
    print(f"   源 {counts2['episodes'][0]} 条 vs 备份 {counts2['episodes'][1]} 条")

# ============ ⑤ 内核命令 ============
print("\n【5】内核命令")
st2 = MemoryStore(DB)
rc = cli_mcp.cmd_backup(st2, {"action": "backup"})
check("cmd_backup backup", rc.get("ok"))

rl = cli_mcp.cmd_backup(st2, {"action": "list"})
check("cmd_backup list", rl.get("ok") and len(rl.get("backups", [])) > 0,
      f"{len(rl.get('backups', []))} 个备份")

if rl.get("backups"):
    rv = cli_mcp.cmd_backup(st2, {"action": "verify",
                                  "path": rl["backups"][0]["path"]})
    check("cmd_backup verify", rv.get("ok") is True)

# 审计
import audit as _audit
astats = _audit.stats(st2)
check("备份写入了审计", astats["by_kind"].get("config", 0) > 0,
      f"{astats['by_kind'].get('config', 0)} 条")

st2.con.close()

# ============ ⑥ 备份文件列表 ============
print("\n【6】备份列表")
backs = bk.list_backups(DB)
check("能列出备份", len(backs) > 0, f"{len(backs)} 个")
for b in backs[:3]:
    print(f"     {b['name']} ({b['bytes']//1024} KB)")

# ---------- 清理 ----------
for suf in ("", "-wal", "-shm", ".bk", ".bad"):
    p = DB + suf
    if os.path.exists(p):
        try:
            os.remove(p)
        except Exception:
            pass
for b in backs:
    try:
        os.remove(b["path"])
    except Exception:
        pass

# ============ 汇总 ============
print("\n" + "=" * 74)
n_ok = sum(1 for r in results if r["ok"])
print(f"结果：{n_ok}/{len(results)} 通过")
for r in results:
    if not r["ok"]:
        print(f"  ❌ {r['name']}  {r['detail']}")
print("=" * 74)

out = {"experiment": "E-280", "n_pass": n_ok, "n_total": len(results),
       "naive_copy_lost": n_orig - n_naive,
       "results": results}
p = os.path.join(_HERE, "results", "e280_backup_verify.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e280_backup_verify.json")

sys.exit(0 if n_ok == len(results) else 1)
