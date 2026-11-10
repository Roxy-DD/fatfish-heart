"""E-369 核查：容灾与无损迁移到底实现了什么

用户问：
  · 是不是实现了记忆容灾？
  · 是不是实现了 100% 无损记忆迁移？
  · 原理是什么（一句话）？

必须用真实代码和实测数据回答，不凭印象。
查：
  ① 无损迁移的实测证据在哪（R1）
  ② 容灾的实现方式（备份/恢复/校验）
  ③ 密钥设计点：为什么能无损
"""
import sys
import os
import re
import warnings

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(_HERE, "cosmos_mem")

print("=" * 78)
print("E-369 容灾与无损迁移的实证核查")
print("=" * 78)

# ---------- ① 无损迁移：找 R1 的实测脚本 ----------
print("\n① R1 无损迁移的证据")
cands = []
for f in os.listdir(_HERE):
    if not f.endswith(".py"):
        continue
    try:
        s = open(os.path.join(_HERE, f), encoding="utf-8",
                 errors="replace").read()
    except Exception:
        continue
    if "无损" in s or "roundtrip" in s.lower() or "R1" in s:
        has_assert = ("8000" in s or "逐字段" in s or "逐向量" in s
                      or "cos" in s.lower())
        cands.append((f, has_assert, len(s)))

print(f"   相关脚本 {len(cands)} 个：")
for f, a, n in sorted(cands, key=lambda x: -x[2])[:12]:
    print(f"     {f:<40} {n:>6}字  {'关键' if a else ''}")

# ---------- ② 迁移实现：lifecycle.py ----------
print("\n② 迁移实现（lifecycle.py）")
lp = os.path.join(_HERE, "lifecycle.py")
if os.path.exists(lp):
    s = open(lp, encoding="utf-8").read()
    fns = re.findall(r"^def (\w+)", s, re.M)
    print(f"   函数: {', '.join(fns)}")
    # 找关键实现
    for key in ["def migrate_to", "def snapshot_version", "def health_check"]:
        i = s.find(key)
        if i > 0:
            seg = s[i:i + 400].split("\n\n")[0]
            print(f"\n   --- {key} ---")
            for line in seg.split("\n")[:10]:
                print(f"   {line[:88]}")

# ---------- ③ 容灾：备份实现 ----------
print("\n③ 容灾实现（backup.py）")
bp = os.path.join(MEM, "backup.py")
if os.path.exists(bp):
    s = open(bp, encoding="utf-8").read()
    fns = re.findall(r"^def (\w+)", s, re.M)
    print(f"   函数: {', '.join(fns)}")
    # 关键：是否用 sqlite3 backup API
    print(f"   用 sqlite3 backup API: "
          f"{'✅' if 'backup(' in s and '.backup(' in s else '❌'}")
    print(f"   处理 -wal: {'✅' if 'wal' in s.lower() else '❌'}")
    print(f"   恢复前删 -wal/-shm: "
          f"{'✅' if '-shm' in s else '❌'}")

# ---------- ④ 真值层设计：为什么能无损 ----------
print("\n④ 真值层结构（决定能否无损）")
sv = os.path.join(MEM, "store_v1.py")
if os.path.exists(sv):
    s = open(sv, encoding="utf-8").read()
    m = re.search(r"SCHEMA_VERSION\s*=\s*['\"]?([\w.]+)", s)
    print(f"   SCHEMA_VERSION: {m.group(1) if m else '?'}")
    print(f"   COLS 定义: {'✅' if 'COLS' in s else '❌'}")
    # 幂等重建
    print(f"   有 rebuild/重建函数: "
          f"{'✅' if 'rebuild' in s.lower() else '❌'}")
    for kw in ["_load_vectors", "rebuild_index", "def _ensure_fts"]:
        if kw in s:
            print(f"   找到 {kw}")

# ---------- ⑤ 实际验证一次无损 ----------
print("\n⑤ 实测：导出一条记忆的所有字段，看是否可完整重建")
sys.path.insert(0, MEM)
try:
    import json
    from store_v2 import MemoryStore
    import backup as bk

    DB = os.path.join(MEM, "dsh_mem.db")
    st = MemoryStore(DB)

    row = st.con.execute(
        "SELECT * FROM episodes WHERE invalid_at IS NULL "
        "ORDER BY cost DESC LIMIT 1").fetchone()
    cols = [c[1] for c in st.con.execute(
        "PRAGMA table_info(episodes)").fetchall()]

    print(f"   样本记忆字段数: {len(cols)}")
    for c, v in zip(cols, row):
        vs = str(v)
        if len(vs) > 40:
            vs = vs[:37] + "..."
        print(f"     {c:<16} = {vs}")

    # 向量
    vec = st.con.execute(
        "SELECT dim, length(vec) FROM vectors WHERE ep_id=?",
        (row[0],)).fetchone()
    print(f"\n   向量: dim={vec[0]}, blob={vec[1]} 字节")

    # 备份
    b = bk.backup_db(DB)
    print(f"\n   备份: ok={b.get('ok')} "
          f"{os.path.basename(str(b.get('path')))}")
    if b.get("ok"):
        v = bk.verify_backup(b["path"])
        print(f"   校验: {v}")

    st.con.close()
except Exception as e:
    print(f"   实测失败: {type(e).__name__}: {e}")

# ---------- 结论 ----------
print("\n" + "=" * 78)
print("核查结论")
print("=" * 78)
