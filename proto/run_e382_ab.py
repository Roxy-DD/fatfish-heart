"""E-382 A/B 对照实验：只注入一次 vs 每轮注入

★ 关键设计：不依赖真实多轮对话（那不可复现），
  而是用**固定的多轮话题序列**模拟会话，测量：

  指标 1：记忆覆盖率
    一个 10 轮会话，其中第 N 轮的话题需要某条记忆。
    · 只注入一次：只有第 1 轮拿到记忆，后续轮次靠模型主动查（实测很少）
    · 每轮注入：每轮都能拿到当轮相关记忆

  指标 2：成本
    每轮注入的实际字符数累计

  指标 3：相关性
    注入的记忆与当轮话题的匹配度

方法：
  ① 构造 10 个话题，每个话题有明确相关的记忆
  ② 模拟两种策略下每轮"模型能看到什么"
  ③ 对比覆盖率与成本
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
import hook as _hook

DB = os.path.join(MEM, "dsh_mem.db")
st = MemoryStore(DB)

print("=" * 78)
print("E-382 A/B 对照：只注入一次 vs 每轮注入")
print("=" * 78)

# ---------- ① 构造模拟会话（10 轮，话题逐步切换）----------
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

print(f"\n① 模拟会话: {len(TOPICS)} 轮，话题逐步切换")

# ---------- ② 两种策略下每轮的实际注入 ----------
BUDGET = 1500      # autoInjectMaxChars
TOPK = 5           # autoInjectTopK
MIN_SCORE = 1.0    # minInjectScore

def do_search(query, top=TOPK):
    """模拟插件调用：scoped=true"""
    res = st.search_scoped(query, top=top)
    items, qdims, stats = res
    protected = [(ep, sc, note) for ep, sc, note, fl in items if fl == 2]
    normal = [(ep, sc, note) for ep, sc, note, fl in items if fl != 2]
    return normal, protected


def relevance_of(item, keywords):
    """该记忆与话题的相关性（关键词命中数）"""
    txt = item[0].raw_text
    return sum(1 for k in keywords if k in txt)


results = {"once": [], "every": []}

print(f"\n② 逐轮模拟（预算 {BUDGET} 字，topK={TOPK}，minScore={MIN_SCORE}）")
print(f"\n   {'轮':>3} {'话题':<24} {'只注入一次':<28} {'每轮注入':<28}")
print("   " + "-" * 84)

for turn, (query, keywords) in enumerate(TOPICS, 1):
    normal, protected = do_search(query)

    # 过滤低分
    normal_f = [(ep, sc, note) for ep, sc, note in normal
                if (sc or 0) >= MIN_SCORE]

    # 计算"每轮注入"这一轮的注入内容
    every_chars = sum(len(ep.raw_text) for ep, sc, note in normal_f)
    every_hits = sum(relevance_of((ep, sc, note), keywords)
                     for ep, sc, note in normal_f)

    # "只注入一次"：只有第 1 轮有内容
    if turn == 1:
        once_chars = every_chars
        once_hits = every_hits
    else:
        once_chars = 0     # 后续轮次不再检索注入，只有 memoryPolicy（一行）
        once_hits = 0

    results["once"].append({
        "turn": turn, "query": query,
        "chars": once_chars, "hits": once_hits,
        "n_items": len(normal_f) if turn == 1 else 0,
    })
    results["every"].append({
        "turn": turn, "query": query,
        "chars": every_chars, "hits": every_hits,
        "n_items": len(normal_f),
    })

    print(f"   {turn:>3} {query[:22]:<24} "
          f"{once_chars:>5}字/{once_hits}命中      "
          f"{every_chars:>5}字/{every_hits}命中")

# ---------- ③ 汇总 ----------
print("\n③ 汇总对比")

once_total_chars = sum(r["chars"] for r in results["once"])
every_total_chars = sum(r["chars"] for r in results["every"])
once_total_hits = sum(r["hits"] for r in results["once"])
every_total_hits = sum(r["hits"] for r in results["every"])

print(f"\n   {'指标':<20} {'只注入一次':>14} {'每轮注入':>14} {'倍数':>8}")
print("   " + "-" * 60)
print(f"   {'总字符数':<20} {once_total_chars:>14} {every_total_chars:>14} "
      f"{every_total_chars/max(1,once_total_chars):>7.1f}x")
print(f"   {'总命中关键词':<20} {once_total_hits:>14} {every_total_hits:>14} "
      f"{every_total_hits/max(1,once_total_hits):>7.1f}x")
print(f"   {'有效轮次(有命中)':<20} "
      f"{sum(1 for r in results['once'] if r['hits']>0):>14} "
      f"{sum(1 for r in results['every'] if r['hits']>0):>14}")

# ---------- ④ 效率比 ----------
print("\n④ 效率比（每字符换来的命中）")

once_eff = once_total_hits / max(1, once_total_chars)
every_eff = every_total_hits / max(1, every_total_chars)

print(f"   只注入一次: {once_eff*1000:.3f} 命中/千字")
print(f"   每轮注入:   {every_eff*1000:.3f} 命中/千字")
print(f"   效率比:     {every_eff/max(1e-9,once_eff):.2f}x")

# ---------- ⑤ 结论 ----------
print("\n" + "=" * 78)
print("实验结论")
print("=" * 78)

coverage_once = sum(1 for r in results["once"] if r["hits"] > 0) / len(TOPICS)
coverage_every = sum(1 for r in results["every"] if r["hits"] > 0) / len(TOPICS)

print(f"""
  ★ 覆盖率（有相关记忆的轮次占比）：
    只注入一次: {coverage_once*100:.0f}%  （{sum(1 for r in results['once'] if r['hits']>0)}/{len(TOPICS)} 轮）
    每轮注入:   {coverage_every*100:.0f}%  （{sum(1 for r in results['every'] if r['hits']>0)}/{len(TOPICS)} 轮）

  ★ 成本：
    只注入一次: {once_total_chars} 字符
    每轮注入:   {every_total_chars} 字符（{every_total_chars/max(1,once_total_chars):.1f} 倍）

  ★ 结论：
""")

if coverage_every > coverage_once * 1.5:
    print(f"""    每轮注入的覆盖率显著更高（{coverage_every*100:.0f}% vs {coverage_once*100:.0f}%），
    但成本是 {every_total_chars/max(1,once_total_chars):.1f} 倍。

    → 这是一个**真实的权衡**，不是"哪个更好"。
    → 需要用「记忆相关的任务」才能判断值不值。
      如果任务不需要跨轮记忆，多花的成本纯浪费。
      如果任务需要（如长线项目、需要引用早期决定），
      只注入一次等于后 9 轮全靠模型自觉（实测只有 12 次主动检索）。

    → 折中方案值得试：**每 N 轮注入一次**（N=3 或 5），
      成本降到 1/N，覆盖率仍随对话推进。
""")
else:
    print(f"""    覆盖率差异不显著，但成本差 {every_total_chars/max(1,once_total_chars):.1f} 倍。
    → 保持「只注入一次」更划算。
""")

# ---------- ⑥ 输出 ----------
out = {
    "experiment": "E-382",
    "n_turns": len(TOPICS),
    "once_total_chars": once_total_chars,
    "every_total_chars": every_total_chars,
    "once_total_hits": once_total_hits,
    "every_total_hits": every_total_hits,
    "coverage_once": round(coverage_once, 3),
    "coverage_every": round(coverage_every, 3),
    "cost_ratio": round(every_total_chars / max(1, once_total_chars), 2),
    "eff_ratio": round(every_eff / max(1e-9, once_eff), 2),
    "per_turn": results,
}
p = os.path.join(_HERE, "results", "e382_ab.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e382_ab.json")

st.con.close()
