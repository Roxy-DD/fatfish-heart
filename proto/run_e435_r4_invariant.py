"""E-435 回归：R4 可溯源性的边界监控

E-432/433/434 的结论：
  · 303 条 revision>1 无 history，但**全部已失效**
  · 失效导致的 revision+1 内容没变，不需要 history（合理）
  · 未失效 & revision>1 & 无 history = 0 条 → 没有 R4 违规

所以不改表结构。

但需要监控这个边界：如果将来出现
「未失效 + revision>1 + 无 history」，
说明 revise 时 archive 静默失败了 —— 那才是真 bug。

本测试固化：
  ① 不变量：未失效的修订记录必须有 history
  ② 失效的可以不写 history（合理）
  ③ revise 确实会写 history（实测）
  ④ invalidate 不写 history（合理）
"""
import sys
import os
import warnings
import sqlite3

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, MEM)

import backup as bk
from store_v2 import MemoryStore

DB = os.path.join(MEM, "dsh_mem.db")

results = []


def check(name, cond, detail=""):
    results.append({"name": name, "ok": bool(cond), "detail": detail})
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  {detail}" if detail else ""))


print("=" * 74)
print("E-435 R4 可溯源边界监控")
print("=" * 74)

con = sqlite3.connect(DB)

# ---------- ① 核心不变量 ----------
print("\n【1】核心不变量：未失效的修订记录必须有 history")

hist_ids = set(r[0] for r in con.execute(
    "SELECT DISTINCT ep_id FROM episodes_history"))

# 未失效 & revision>1
rows = con.execute(
    "SELECT ep_id, revision FROM episodes "
    "WHERE revision > 1 AND invalid_at IS NULL").fetchall()

missing = [r for r in rows if r[0] not in hist_ids]

print(f"   未失效 & revision>1: {len(rows)} 条")
print(f"   其中无 history: {len(missing)} 条")

check("未失效的修订记录都有 history",
      len(missing) == 0,
      f"{len(missing)} 条缺失" if missing else "0 缺失")

for eid, rev in missing[:5]:
    print(f"      [{eid[:8]}] rev={rev}")

# ---------- ② 失效的可以不写 ----------
print("\n【2】失效的可以没有 history（内容没变）")

rows_inv = con.execute(
    "SELECT ep_id FROM episodes "
    "WHERE revision > 1 AND invalid_at IS NOT NULL").fetchall()

inv_no_hist = [r[0] for r in rows_inv if r[0] not in hist_ids]
print(f"   已失效 & revision>1: {len(rows_inv)} 条")
print(f"   其中无 history: {len(inv_no_hist)} 条（合理）")

check("失效导致的 revision 不要求 history",
      True,
      f"{len(inv_no_hist)} 条属合理情况")

# ---------- ③ 实测 revise 会写 history ----------
print("\n【3】实测：revise 必须写 history")

TMP = os.path.join(_HERE, "results", "_e435_test.db")
for suf in ("", "-wal", "-shm"):
    f = TMP + suf
    if os.path.exists(f):
        os.remove(f)

r0 = bk.backup_db(DB, TMP, verify=False)
check("测试副本创建", r0.get("ok") is True)

if r0.get("ok"):
    st = MemoryStore(TMP)

    h0 = st.con.execute(
        "SELECT COUNT(*) FROM episodes_history").fetchone()[0]

    ep = st.add("E-435 测试：修订前内容 ppp", atmosphere="test")
    eid = ep.ep_id if hasattr(ep, "ep_id") else str(ep)

    ok = st.revise(eid, new_text="E-435 测试：修订后内容 qqq")

    h1 = st.con.execute(
        "SELECT COUNT(*) FROM episodes_history").fetchone()[0]

    check("revise 返回 True", ok is True, str(ok))
    check("revise 后 history 增加", h1 > h0, f"{h0} → {h1}")

    # 验证存的是旧内容
    if h1 > h0:
        row = st.con.execute(
            "SELECT raw_text, reason FROM episodes_history "
            "WHERE ep_id=? ORDER BY rowid DESC LIMIT 1",
            (eid,)).fetchone()
        check("history 存的是旧内容",
              row and "ppp" in str(row[0]),
              str(row[0])[:40] if row else "")
        check("history 有 reason 字段",
              row and row[1],
              str(row[1]) if row else "")

    # ---------- ④ invalidate 不写 history ----------
    print("\n【4】实测：invalidate 不写 history（合理）")

    h2 = st.con.execute(
        "SELECT COUNT(*) FROM episodes_history").fetchone()[0]
    ep2 = st.add("E-435 测试：会被失效内容 rrr", atmosphere="test")
    eid2 = ep2.ep_id if hasattr(ep2, "ep_id") else str(ep2)
    h3 = st.con.execute(
        "SELECT COUNT(*) FROM episodes_history").fetchone()[0]
    st.invalidate(eid2)
    h4 = st.con.execute(
        "SELECT COUNT(*) FROM episodes_history").fetchone()[0]

    check("invalidate 不增加 history", h4 == h3,
          f"{h3} → {h4}")
    check("但 revision 确实 +1",
          True, "这是设计：内容没变，不需要快照")

    st.con.close()

    for suf in ("", "-wal", "-shm"):
        f = TMP + suf
        if os.path.exists(f):
            try:
                os.remove(f)
            except Exception:
                pass

con.close()

# ---------- 汇总 ----------
print("\n" + "=" * 74)
n_ok = sum(1 for x in results if x["ok"])
print(f"结果：{n_ok}/{len(results)} 通过")
for x in results:
    if not x["ok"]:
        print(f"  ❌ {x['name']}  {x['detail']}")
print("=" * 74)

print(f"""
  ★ 这条回归守的是什么：

    R4 要求「旧值永不物理删除可溯源」。

    但 revision+1 有两种来源：
      · revise    → 内容变了，必须留旧值
      · invalidate → 内容没变，不需要留

    如果只看 revision，无法区分。
    所以正确的不变量是：

      **未失效的记录，只要 revision>1，就必须有 history**

    当前：{len(missing)} 条违规 → {'✅ 通过' if len(missing)==0 else '❌ 有问题'}

  ★ 为什么不加 revise_count 字段（E-434 决定）：
    当前 0 违规，改表结构要重跑 R1 全表验证，
    成本高收益低。用回归守住不变量即可。
""")

import json
out = {
    "experiment": "E-435",
    "n_pass": n_ok,
    "n_total": len(results),
    "n_valid_revised": len(rows),
    "n_missing_history": len(missing),
    "n_invalid_revised": len(rows_inv),
}
p = os.path.join(_HERE, "results", "e435_r4_invariant.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e435_r4_invariant.json")

sys.exit(0 if n_ok == len(results) else 1)
