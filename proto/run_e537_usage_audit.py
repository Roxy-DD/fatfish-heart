"""记忆使用率实测（FISH-15 生命周期那一半）

════════════════════════════════════════════════════════════
要回答的问题
════════════════════════════════════════════════════════════
注入优化做到头了（E-534/535）。真正的浪费在另一头：
**有多少记忆存着但从没被检索到？**

这些"零召回"记忆：
· 占着向量索引（R3 重建成本）
· 占着备份空间（异地副本体积）
· 从没给模型提供过价值

但注意：零召回 ≠ 无用。L0/L1 是"永远在场"的，不走检索通道；
有些低频记忆可能正是"关键时刻才用一次"的。

所以这测的是"候选优化空间"，不是直接结论。

════════════════════════════════════════════════════════════
数据来源
════════════════════════════════════════════════════════════
meta.channel:<ep_id> = {"recall_by_query": N, ...}
由 store_v1.search 在命中时记账（E-242 修过：之前只睡眠记账，
真实检索命中从未记账）。

排除：
· 测试数据（meta.test_data_ep_ids，用户 11-18 选 B 标记）
· 已失效记忆
· L0/L1（flag=2，走独立通道，本来就不该走检索）
"""
import json
import sqlite3
import sys

DB = "cosmos_mem/dsh_mem.db"
sys.path.insert(0, ".")
from test_data import test_ids

con = sqlite3.connect(DB)
marked = test_ids(con)

# ---------- 拉数据 ----------
episodes = {}
for eid, created, cost, invalid, txt in con.execute(
    "SELECT ep_id, created_at, cost, invalid_at, raw_text FROM episodes"
):
    episodes[eid] = {
        "created": created, "cost": cost,
        "invalid": invalid, "len": len(txt or ""),
    }

channels = {}
for key, val in con.execute("SELECT key, val FROM meta WHERE key LIKE 'channel:%'"):
    eid = key[len("channel:"):]
    try:
        d = json.loads(val)
        channels[eid] = sum(v for v in d.values() if isinstance(v, (int, float)))
    except Exception:
        channels[eid] = 0

# L0/L1（seed 里的，走独立通道）
seed = json.load(open("cosmos_mem/seed_memory.json", encoding="utf-8"))
seed_ids = set()
for lv in ("L0", "L1"):
    for it in seed.get(lv) or []:
        if isinstance(it, dict) and it.get("ep_id"):
            seed_ids.add(it["ep_id"])

con.close()

# ---------- 分类 ----------
real = {eid: v for eid, v in episodes.items()
        if eid not in marked and v["invalid"] is None}
l01_real = real.keys() & seed_ids
normal = {eid: v for eid, v in real.items() if eid not in seed_ids}

zero = [eid for eid in normal if channels.get(eid, 0) == 0]
has = [eid for eid in normal if channels.get(eid, 0) > 0]

print("=" * 70)
print("记忆使用率实测（FISH-15）")
print("=" * 70)

print(f"\n【1】总体")
print(f"  episodes 总数:        {len(episodes)}")
print(f"  减去测试残留({len(marked)}):  {len(episodes) - len(marked)}")
print(f"  再减失效 → 有效:      {len(real)}")
print(f"  其中 L0/L1（独立通道）: {len(l01_real)}")
print(f"  普通记忆:             {len(normal)}")

print(f"\n【2】普通记忆的召回情况")
print(f"  被检索过:   {len(has):3d} 条  ({len(has)/len(normal)*100:.1f}%)")
print(f"  从未检索:   {len(zero):3d} 条  ({len(zero)/len(normal)*100:.1f}%)")

if zero:
    zero_chars = sum(normal[e]["len"] for e in zero)
    zero_cost = sum(normal[e]["cost"] or 0 for e in zero)
    print(f"\n  零召回记忆的字符总量: {zero_chars:,} 字")
    print(f"  零召回记忆的 cost 总和: {zero_cost:.1f}")
    print(f"  平均 cost: {zero_cost/len(zero):.3f}")

    print(f"\n  零召回样本（前 8 条）:")
    con = sqlite3.connect(DB)
    for eid in zero[:8]:
        r = con.execute(
            "SELECT substr(raw_text,1,55), cost, created_at FROM episodes WHERE ep_id=?",
            (eid,)
        ).fetchone()
        if r:
            txt, cost, cr = r
            print(f"    [{eid[:10]}] cost={cost} {txt}")
    con.close()

# ---------- 按 cost 分层 ----------
print(f"\n【3】零召回记忆的 cost 分布")
buckets = {"低(<0.5)": 0, "中(0.5-0.8)": 0, "高(>=0.8)": 0}
for eid in zero:
    c = normal[eid]["cost"] or 0
    if c < 0.5:
        buckets["低(<0.5)"] += 1
    elif c < 0.8:
        buckets["中(0.5-0.8)"] += 1
    else:
        buckets["高(>=0.8)"] += 1
for k, v in buckets.items():
    print(f"  {k:14s}: {v:3d} 条")

print("\n" + "=" * 70)
print("怎么读这个结果")
print("=" * 70)
print("""
零召回 ≠ 无用：
· 有些记忆是"低频但关键"（比如红线、约定），平时不检索，
  但需要时缺了就出问题 —— 这类不该清。
· cost 高的零召回记忆尤其不能随便动：系统判定它重要，
  只是当前这批问题没问到它。

真正的优化机会：
· cost 低 + 零召回 + 内容冗长 → 候选下沉（不删，降低注入优先级）
· 但必须先证明"下沉后关键时刻仍能找回"

下一步应该是：给零召回记忆做一次"如果下沉，还能否召回"的模拟。
""")
