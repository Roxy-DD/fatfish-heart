"""E-317 精确测：强制注入到底占不占 top-5 名额？

E-316 显示 11 条 flag=2 出现在结果里。
但代码显示 picked = order[:top] 先截断，然后才 append forced。

所以理论上 forced 不占名额。需要实测确认：
  · search(top=5) 返回几条？
  · 前 5 条里有几个 flag=2？
"""
import sys
import os
import warnings

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, MEM)

from store_v2 import MemoryStore

DB = os.path.join(MEM, "dsh_mem.db")
st = MemoryStore(DB)

print("=" * 76)
print("E-317 强制注入是否占用 top-k 名额")
print("=" * 76)

QUERIES = [
    "跨版本迁移能力",
    "生命周期迁移",
    "记忆系统",
    "AGI 观点",
    "去重机制",
]

for q in QUERIES:
    res = st.search(q, top=5)
    n_total = len(res)
    n_flag2 = sum(1 for ep, sc, fl in res if fl == 2)
    n_normal = n_total - n_flag2

    # 普通结果（flag != 2）的相对排名
    normal_items = [(ep, sc, fl) for ep, sc, fl in res if fl != 2]

    print(f"\n查询: '{q}'")
    print(f"  返回总数: {n_total}")
    print(f"  flag=2（强制）: {n_flag2}")
    print(f"  普通结果: {n_normal}")
    print(f"  普通结果的分数（降序）: "
          f"{[round(sc, 2) for ep, sc, fl in normal_items[:5]]}")

    # 检查：普通结果是否排在前 n_normal 位
    first_flags = [fl for ep, sc, fl in res[:n_normal]]
    if all(f != 2 for f in first_flags):
        print(f"  ✅ 普通结果排在前面，强制注入在末尾")
    else:
        print(f"  ❌ 强制注入混在前部: {first_flags}")

    # 显示前 5 条
    for i, (ep, sc, fl) in enumerate(res[:5], 1):
        tag = "强制" if fl == 2 else ("固定" if fl == 1 else "普通")
        print(f"     {i}. [{tag:>2}] cos={sc:>5.2f} {ep.raw_text[:45]}")

# ============ 关键对比 ============
print("\n" + "=" * 76)
print("关键对比：top=5 vs 不限 top")
print("=" * 76)

q = "跨版本迁移能力"
print(f"\n查询: '{q}'")

for top in [5, 10, 20]:
    res = st.search(q, top=top)
    normal = [(ep, sc, fl) for ep, sc, fl in res if fl != 2]
    print(f"\n  top={top}:")
    print(f"    总数 {len(res)}, 普通 {len(normal)}")
    # 目标记忆在不在这批里
    target = [ep for ep, sc, fl in res if ep.ep_id.startswith("02c11d5c")]
    if target:
        rank = next(i for i, (ep, sc, fl) in enumerate(res, 1)
                    if ep.ep_id.startswith("02c11d5c"))
        print(f"    ✅ 目标在第 {rank} 位")
    else:
        print(f"    ❌ 目标不在这批里")

st.con.close()
