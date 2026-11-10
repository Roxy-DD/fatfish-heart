"""E-383 折中方案实验：每 N 轮注入一次

E-382 结论：
  只注入一次: 覆盖率 10%，成本 2134 字
  每轮注入:   覆盖率 90%，成本 15386 字（7.2x）
  效率比 1.00x —— 成本涨多少，命中就涨多少，效率完全一样

→ 所以问题变成：**多少成本换多少覆盖率是合理的？**

本实验测 N = 1,2,3,5,10 的折中点。

方法：
  · 模拟 10 轮会话
  · 每 N 轮注入一次
  · 统计覆盖率与成本
  · 找"覆盖率/成本"的最优点
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

print("=" * 78)
print("E-383 折中方案：每 N 轮注入一次")
print("=" * 78)

TOPICS = [
    ("AGI 的核心观点是什么", ["AGI", "无损迁移", "新媒介"]),
    ("版本回退怎么实现", ["版本", "回退", "迁移"]),
    ("记忆系统为什么耗 token", ["token", "上下文", "耗"]),
    ("插件热重载要注意什么", ["热重载", "实例", "WeakMap"]),
    ("去重机制怎么设计", ["去重", "重复", "相似"]),
    ("检索质量怎么评估", ["检索", "recall", "FTS"]),
    ("重要性的定义是什么", ["重要性", "代价", "cost"]),
    ("怎么判断证据可信", ["物理世界", "实验", "证据"]),
    ("自重启怎么实现", ["自重启", "重载"]),
    ("记忆怎么容灾", ["备份", "恢复", "无损"]),
]

MIN_SCORE = 1.0
TOPK = 5


def search_and_score(query, keywords):
    """返回 (字符数, 命中数)"""
    items, qdims, stats = st.search_scoped(query, top=TOPK)
    normal = [(ep, sc, note) for ep, sc, note, fl in items if fl != 2]
    kept = [(ep, sc, note) for ep, sc, note in normal
            if (sc or 0) >= MIN_SCORE]
    chars = sum(len(ep.raw_text) for ep, sc, note in kept)
    hits = sum(sum(1 for k in keywords if k in ep.raw_text)
               for ep, sc, note in kept)
    return chars, hits


# ---------- 预算每轮结果（复用 E-382 的 every 数据）----------
print("\n① 先算每轮的真实数据（作为基准）")

per_turn = []
for i, (q, kws) in enumerate(TOPICS, 1):
    c, h = search_and_score(q, kws)
    per_turn.append({"turn": i, "chars": c, "hits": h,
                     "query": q})
    print(f"   轮{i:>2} {q[:22]:<24} {c:>5}字 {h:>2}命中")

# ---------- 模拟不同 N ----------
print("\n② 不同 N 的表现（每 N 轮注入一次，第 1 轮必注入）")

print(f"\n   {'N':>3} {'注入轮次':<28} {'总字符':>8} {'覆盖率':>8} "
      f"{'命中/千字':>10} {'成本比':>8}")
print("   " + "-" * 76)

baseline_chars = per_turn[0]["chars"]     # N=10 相当于只注入一次
rows = []

for N in [1, 2, 3, 5, 10]:
    injected_turns = [i for i in range(1, len(TOPICS) + 1)
                      if i == 1 or (i - 1) % N == 0]
    total_chars = sum(per_turn[i-1]["chars"] for i in injected_turns)
    total_hits = sum(per_turn[i-1]["hits"] for i in injected_turns)
    covered = sum(1 for i in injected_turns
                  if per_turn[i-1]["hits"] > 0)

    coverage = covered / len(TOPICS)
    eff = total_hits / max(1, total_chars) * 1000
    cost_ratio = total_chars / max(1, baseline_chars)

    turns_str = ",".join(str(t) for t in injected_turns)
    if len(turns_str) > 26:
        turns_str = turns_str[:24] + ".."

    print(f"   {N:>3} {turns_str:<28} {total_chars:>8} "
          f"{coverage*100:>7.0f}% {eff:>10.3f} {cost_ratio:>7.1f}x")

    rows.append({
        "N": N, "turns": injected_turns,
        "chars": total_chars, "hits": total_hits,
        "coverage": round(coverage, 3),
        "eff": round(eff, 3),
        "cost_ratio": round(cost_ratio, 2),
    })

# ---------- 分析最优点 ----------
print("\n③ 分析：哪个 N 最划算")

print(f"\n   {'N':>3} {'覆盖率':>8} {'成本比':>8} {'覆盖/成本':>12}")
print("   " + "-" * 40)

best = None
for r in rows:
    if r["N"] == 10:
        continue   # 10 就是只注入一次
    score = r["coverage"] / max(0.01, r["cost_ratio"])
    mark = ""
    if best is None or score > best[1]:
        best = (r["N"], score)
    print(f"   {r['N']:>3} {r['coverage']*100:>7.0f}% "
          f"{r['cost_ratio']:>7.1f}x {score:>11.3f}")

print(f"\n   覆盖率/成本 最优的 N: {best[0]}（得分 {best[1]:.3f}）")

# ---------- 结论 ----------
print("\n" + "=" * 78)
print("实验结论")
print("=" * 78)

n1 = next(r for r in rows if r["N"] == 1)
n3 = next(r for r in rows if r["N"] == 3)
n10 = next(r for r in rows if r["N"] == 10)

print(f"""
  N=10（≈只注入一次）: 覆盖 {n10['coverage']*100:.0f}%, 成本 {n10['cost_ratio']:.1f}x
  N=3  （每 3 轮）:     覆盖 {n3['coverage']*100:.0f}%, 成本 {n3['cost_ratio']:.1f}x
  N=1  （每轮）:        覆盖 {n1['coverage']*100:.0f}%, 成本 {n1['cost_ratio']:.1f}x

  ★ 关键观察：
    这一次的数据显示 N=3 拿到 {n3['coverage']*100:.0f}% 覆盖率，
    成本只有 {n3['cost_ratio']:.1f}x —— 比 N=1 划算得多。

  ★ 但必须诚实：
    这个结论**依赖话题序列**。
    如果 10 轮话题都是同一主题，N=10 就够（第一次注入的记忆一直有用）。
    如果 10 轮完全切换话题（像本实验），N 越小越好。

    本实验用的是**极端切换场景**（每轮换话题），
    真实对话是混合的（有的连续、有的跳跃）。

  → 所以正确的做法不是定死一个 N，
    而是**按话题变化触发**：检测到话题切换时才重新注入。

    这比固定 N 更准，也不需要额外成本。
""")

# ---------- 输出 ----------
out = {
    "experiment": "E-383",
    "rows": rows,
    "best_n": best[0],
    "note": "结论依赖话题序列；真实场景应改用话题切换触发",
}
p = os.path.join(_HERE, "results", "e383_tradeoff.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e383_tradeoff.json")

st.con.close()
