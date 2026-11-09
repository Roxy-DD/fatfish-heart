"""E-316 查：为什么合并记忆的自检索排名只有第 4？

E-315 确认：
  · 向量是最新的（cos=1.0000）
  · 用段落查询能找回（✅）
  • 但排名只有 4，不是第 1

理想情况：用记忆自己的原句查询，应该排第 1。

排名低说明有更强的竞争者。看看是谁，为什么。
"""
import sys
import os
import json
import warnings

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, MEM)

from store_v2 import MemoryStore

DB = os.path.join(MEM, "dsh_mem.db")
st = MemoryStore(DB)

print("=" * 76)
print("E-316 查自检索排名偏低的原因")
print("=" * 76)

TARGET_PREFIX = "02c11d5c"
row = st.con.execute(
    "SELECT ep_id, raw_text FROM episodes WHERE ep_id LIKE ?",
    (TARGET_PREFIX + "%",)).fetchone()
if not row:
    print("目标不存在")
    raise SystemExit(1)

eid, txt = row
parts = [p.strip() for p in txt.split("｜合并:") if p.strip()]
probe = parts[0][:30]

print(f"\n① 目标: [{eid[:8]}] {len(txt)} 字")
print(f"   查询: '{probe}'")

# ============ ② 看完整排名 ============
print("\n② 完整排名（top 8）")

res = st.search(probe, top=8)

print(f"\n   {'排名':>4} {'cos':>7} {'flag':>5} {'长度':>6}  内容")
for i, (ep, sc, fl) in enumerate(res, 1):
    is_target = ep.ep_id == eid
    mark = "★" if is_target else " "
    flag_str = "强制" if fl == 2 else ("固定" if fl == 1 else "")
    print(f"   {mark}{i:>3} {sc:>7.2f} {flag_str:>5} {len(ep.raw_text):>6}  "
          f"{ep.raw_text[:50]}")

# ============ ③ 分析：谁排在了前面 ============
print("\n③ 分析排在前面的记忆")

target_rank = None
for i, (ep, sc, fl) in enumerate(res, 1):
    if ep.ep_id == eid:
        target_rank = i
        target_score = sc
        break

if target_rank and target_rank > 1:
    print(f"\n   目标排名 {target_rank}, 得分 {target_score:.2f}")
    print(f"\n   排在它前面的:")
    for i, (ep, sc, fl) in enumerate(res[:target_rank - 1], 1):
        print(f"\n   [{i}] cos={sc:.2f} [{ep.ep_id[:8]}] "
              f"({len(ep.raw_text)}字)")
        print(f"       {ep.raw_text[:100]}")
        if fl:
            print(f"       ⚠ flag={fl}（{'强制注入的L0/L1' if fl == 2 else '固定席位'}）")

# ============ ④ 用原始单句查询（不带任何修饰）============
print("\n④ 用更短更精确的查询")

probes = [
    (parts[0][:15], "段0前15字"),
    ("跨版本迁移能力", "核心概念"),
    ("生命周期迁移", "另一核心概念"),
    (txt[:20], "整条开头"),
]

for p, desc in probes:
    r = st.search(p, top=5)
    rank = None
    for i, (ep, sc, fl) in enumerate(r, 1):
        if ep.ep_id == eid:
            rank = i
            break
    mark = "✅" if rank == 1 else ("🟡" if rank else "❌")
    print(f"   {mark} '{p}' ({desc}) → 排名 {rank or '未命中'}")

# ============ ⑤ 检查是否有 flag 影响排序 ============
print("\n⑤ flag 影响检查")

# 统计返回结果里有多少 flag=2 的
n_flag2 = sum(1 for ep, sc, fl in res if fl == 2)
n_flag1 = sum(1 for ep, sc, fl in res if fl == 1)
print(f"   flag=2（L0/L1强制）: {n_flag2} 条")
print(f"   flag=1（固定席位）:   {n_flag1} 条")

if n_flag2 > 0:
    print(f"\n   ⚠ 有 {n_flag2} 条强制注入占了名额")
    print(f"      这些会挤占普通记忆的排名位置")

# 用更大的 top 看目标实际是第几
res20 = st.search(probe, top=20)
rank20 = None
for i, (ep, sc, fl) in enumerate(res20, 1):
    if ep.ep_id == eid:
        rank20 = i
        break
print(f"\n   top=20 时目标排名: {rank20}")

# ============ ⑥ 结论 ============
print("\n" + "=" * 76)
print("结论")
print("=" * 76)

print(f"""
  查询: '{probe}'
  目标: [{eid[:8]}]（{len(txt)} 字，{len(parts)} 段合并）

  top=5 排名: {target_rank}
  top=20 排名: {rank20}

  关键发现:
    · 向量是最新的（E-315 确认 cos=1.0000）
    · 能找回，但排名不是第 1
""")

if n_flag2 > 0:
    print(f"""
  ⚠ 主要原因: {n_flag2} 条 L0/L1 强制注入占了前排
     这些记忆**无条件**出现在结果里（flag=2），
     挤占了普通记忆的排名位置。

     这是设计使然（红线/常驻准则必须每次在场），
     但对"自检索排名"这个指标有影响。
""")

st.con.close()

out = {
    "experiment": "E-316",
    "target": eid,
    "query": probe,
    "rank_top5": target_rank,
    "rank_top20": rank20,
    "n_flag2": n_flag2,
    "n_flag1": n_flag1,
}
p = os.path.join(_HERE, "results", "e316_rank.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e316_rank.json")
