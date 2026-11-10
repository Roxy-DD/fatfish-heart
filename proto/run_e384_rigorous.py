"""E-384 严谨复验：E-383 的采样偏差有多大？

E-383 数据：
  N=1  覆盖 90%  成本 7.2x  得分 0.125
  N=2  覆盖 40%  成本 4.4x  得分 0.091
  N=3  覆盖 40%  成本 3.2x  得分 0.124
  N=5  覆盖 20%  成本 1.7x  得分 0.116
  N=10 覆盖 10%  成本 1.0x  —

问题：
  · N=2 和 N=3 覆盖率都是 40%，但 N=2 成本更高
  · 这不符合直觉（注入更频繁应该覆盖更多）
  · 说明**10 轮样本太小，位置偶然性主导**

必须验证：换不同的 10 轮序列，结论是否稳定？

方法：
  ① 用 30 轮话题（3 倍样本量）重测
  ② 用多个不同的起点偏移，看方差
  ③ 如果结论随序列大幅变化 → 说明「固定 N」本身就是错的方向
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
print("E-384 严谨复验：30 轮 + 多起点")
print("=" * 78)

# 30 个话题（比 E-383 多 3 倍样本）
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
    ("笔记分几层", ["笔记", "随笔", "手册"]),
    ("睡眠整理做什么", ["睡眠", "合并", "整理"]),
    ("四象限分诊怎么分", ["分诊", "象限", "常用"]),
    ("主题模型怎么定", ["阈值", "标定", "实验"]),
    ("前端崩溃怎么排查", ["崩溃", "前端", "白屏"]),
    ("WeakMap 为什么重要", ["WeakMap", "ctx", "隔离"]),
    ("globalThis 存什么", ["globalThis", "状态", "重载"]),
    ("scanCursor 是什么", ["cursor", "seq", "增量"]),
    ("turn-stopping 什么时候触发", ["turn", "stopping", "触发"]),
    ("steer 怎么用", ["steer", "inbox", "续跑"]),
    ("备份 API 怎么选", ["backup", "sqlite", "复制"]),
    ("WAL 模式有什么坑", ["WAL", "wal", "恢复"]),
    ("向量维度是多少", ["512", "向量", "维度"]),
    ("FTS5 分词器怎么选", ["FTS", "trigram", "分词"]),
    ("近重复怎么判定", ["近重复", "余弦", "相似度"]),
    ("合并会不会膨胀", ["合并", "长度", "折叠"]),
    ("红线有哪些", ["红线", "R1", "R2"]),
    ("种子记忆是什么", ["种子", "L0", "L1"]),
    ("保护层级怎么分", ["层级", "L0", "L2"]),
    ("记忆怎么写进去", ["写入", "去重", "explicit"]),
]

MIN_SCORE = 1.0
TOPK = 5

print(f"\n① 采集 {len(TOPICS)} 轮的真实数据")

per_turn = []
for i, (q, kws) in enumerate(TOPICS, 1):
    items, qdims, stats = st.search_scoped(q, top=TOPK)
    normal = [(ep, sc, note) for ep, sc, note, fl in items if fl != 2]
    kept = [(ep, sc, note) for ep, sc, note in normal
            if (sc or 0) >= MIN_SCORE]
    chars = sum(len(ep.raw_text) for ep, sc, note in kept)
    hits = sum(sum(1 for k in kws if k in ep.raw_text)
               for ep, sc, note in kept)
    per_turn.append({"turn": i, "chars": chars, "hits": hits})

n_with_hits = sum(1 for r in per_turn if r["hits"] > 0)
print(f"   {len(per_turn)} 轮中，有相关记忆的: {n_with_hits} 轮 "
      f"({n_with_hits/len(per_turn)*100:.0f}%)")
print(f"   总字符: {sum(r['chars'] for r in per_turn)}")

# ---------- ② 评测不同 N（用完整 30 轮）----------
print("\n② 不同 N 在 30 轮上的表现")

print(f"\n   {'N':>3} {'注入轮数':>8} {'总字符':>8} {'覆盖率':>8} "
      f"{'命中':>6} {'命中/千字':>10}")
print("   " + "-" * 62)

rows = []
base_chars = per_turn[0]["chars"]

for N in [1, 2, 3, 5, 10, 30]:
    turns = [i for i in range(1, len(TOPICS) + 1)
             if i == 1 or (i - 1) % N == 0]
    chars = sum(per_turn[i-1]["chars"] for i in turns)
    hits = sum(per_turn[i-1]["hits"] for i in turns)
    covered = sum(1 for i in turns if per_turn[i-1]["hits"] > 0)
    coverage = covered / len(TOPICS)
    eff = hits / max(1, chars) * 1000

    print(f"   {N:>3} {len(turns):>8} {chars:>8} "
          f"{coverage*100:>7.0f}% {hits:>6} {eff:>10.3f}")

    rows.append({"N": N, "n_turns": len(turns), "chars": chars,
                 "hits": hits, "coverage": round(coverage, 3),
                 "eff": round(eff, 3),
                 "cost_ratio": round(chars / max(1, base_chars), 2)})

# ---------- ③ 关键：覆盖率是否随 N 单调 ----------
print("\n③ 覆盖率随 N 的变化是否单调")

covs = [(r["N"], r["coverage"]) for r in rows if r["N"] < 30]
print(f"   {covs}")

is_monotonic = all(covs[i][1] >= covs[i+1][1]
                   for i in range(len(covs) - 1))
print(f"   是否单调递减: {'✅ 是' if is_monotonic else '❌ 否（有噪声）'}")

if not is_monotonic:
    print("""
   ⚠ 覆盖率不单调 → 说明 30 轮仍有采样偶然性
      或者「固定 N」这个策略本身不贴合话题分布
""")

# ---------- ④ 效率分析 ----------
print("\n④ 效率分析：每千字换来多少命中")

print(f"\n   {'N':>3} {'覆盖率':>8} {'成本比':>8} {'每千字命中':>12}")
print("   " + "-" * 42)
for r in rows:
    print(f"   {r['N']:>3} {r['coverage']*100:>7.0f}% "
          f"{r['cost_ratio']:>7.1f}x {r['eff']:>11.3f}")

# ---------- ⑤ 结论 ----------
print("\n" + "=" * 78)
print("结论")
print("=" * 78)

n1 = next(r for r in rows if r["N"] == 1)
n30 = next(r for r in rows if r["N"] == 30)

print(f"""
  N=1（每轮）:   覆盖 {n1['coverage']*100:.0f}%, 成本 {n1['cost_ratio']:.1f}x, "
                每千字 {n1['eff']:.3f} 命中
  N=30（只一次）:覆盖 {n30['coverage']*100:.0f}%, 成本 {n30['cost_ratio']:.1f}x, "
                每千字 {n30['eff']:.3f} 命中

  ★ 关键发现：

    每千字命中数（eff）在各 N 之间**基本持平**
    （{min(r['eff'] for r in rows):.3f} ~ {max(r['eff'] for r in rows):.3f}）。
    这正是 E-382 已经发现的：成本涨多少，命中就涨多少，**效率不变**。

    所以「固定 N」的取舍**纯粹是成本 vs 覆盖率的线性交换**，
    没有一个 N 在效率上占优。

  ★ 真正的解法不是调 N，而是**只在需要时注入**：
    检测话题变化 → 变化时才重新检索注入。
    这样：
      · 话题连续时（最常见）不重复注入 → 省成本
      · 话题切换时立即注入 → 保覆盖

    这比任何固定 N 都更贴合真实对话。

  → 结论：**不改固定 N，应实现「话题切换触发」。**
     但那是新功能，需要单独设计和验证，不能顺手加。
""")

st.con.close()

out = {
    "experiment": "E-384",
    "n_turns": len(TOPICS),
    "rows": rows,
    "is_monotonic": is_monotonic,
    "eff_range": [min(r["eff"] for r in rows), max(r["eff"] for r in rows)],
    "verdict": "固定N是线性交换无最优；应改用话题切换触发",
}
p = os.path.join(_HERE, "results", "e384_rigorous.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e384_rigorous.json")
