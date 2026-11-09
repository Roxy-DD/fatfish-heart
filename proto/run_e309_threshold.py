"""E-309 实测：近重复检测的阈值应该定多少？

E-308 发现：「本记忆系统的检索依靠向量相似度」
            与「记忆系统使用向量检索」的相似度 < 0.95，没被去重。

问题：0.95 是拍脑袋定的，没有数据依据。
     按用户要求「不预设门槛，靠实验确定边界」。

做法：
  ① 构造「确定是同一件事」的句子对，测相似度分布
  ② 构造「确定是不同的事」的句子对，测相似度分布
  ③ 找两者的分界线
"""
import sys
import os
import json
import warnings
import itertools

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))

import numpy as np
from store_v1 import Embedder

emb = Embedder.get()

print("=" * 76)
print("E-309 近重复阈值的实测标定")
print("=" * 76)

# ============ 构造样本对 ============
# A 组：确定是同一件事（措辞不同）
SAME = [
    ("记忆系统使用向量检索", "本系统依靠向量来检索记忆"),
    ("用户希望 AI 能自重启", "用户想要 AI 具备自我重启的能力"),
    ("不必无损迁移", "无损迁移不是必须的"),
    ("插件热重载需要按实例隔离状态", "热重载时状态必须绑定到实例"),
    ("检索延迟是模型加载成本", "慢的原因是加载模型"),
    ("数据只增不删", "永不物理删除数据"),
    ("前端崩溃因回调参数遮蔽", "回调参数覆盖了 h 导致白屏"),
    ("用 WeakMap 按 ctx 隔离", "通过 WeakMap 按上下文做隔离"),
    ("FTS5 默认分词器对中文失效", "FTS5 的 unicode61 切不了中文"),
    ("睡眠整理会合并碎片", "睡眠时合并相似的记忆"),
]

# B 组：确定是不同的事（同领域但不同主题）
DIFF = [
    ("记忆系统使用向量检索", "版本回退支持任意跨度"),
    ("用户希望 AI 能自重启", "记忆去重需要语义检测"),
    ("不必无损迁移", "插件热重载按实例隔离"),
    ("插件热重载需要按实例隔离状态", "检索延迟是模型加载成本"),
    ("检索延迟是模型加载成本", "数据只增不删"),
    ("数据只增不删", "前端崩溃因回调参数遮蔽"),
    ("前端崩溃因回调参数遮蔽", "用 WeakMap 按 ctx 隔离"),
    ("用 WeakMap 按 ctx 隔离", "FTS5 默认分词器对中文失效"),
    ("FTS5 默认分词器对中文失效", "睡眠整理会合并碎片"),
    ("睡眠整理会合并碎片", "记忆系统使用向量检索"),
    ("向量检索的历史数据", "笔记分为随笔笔记手册三层"),
    ("重要性的定义是代价", "情绪强度决定记得牢"),
]

# C 组：边界情况（相关但可能不该合并）
BORDER = [
    ("记忆系统使用向量检索", "记忆系统还使用关键词检索"),
    ("用户希望 AI 能自重启", "用户希望 AI 能自修复"),
    ("检索延迟是模型加载成本", "检索延迟已经优化到 10ms"),
    ("数据只增不删", "失效的记忆只打标记"),
]


def sim(a, b):
    va = emb.encode(a)[0]
    vb = emb.encode(b)[0]
    na = float(np.linalg.norm(va)) or 1.0
    nb = float(np.linalg.norm(vb)) or 1.0
    return float(va @ vb / (na * nb))


# ============ ① 同一件事的相似度 ============
print("\n① 同一件事（措辞不同）的相似度")
same_sims = []
for a, b in SAME:
    s = sim(a, b)
    same_sims.append(s)
    print(f"   {s:.3f}  {a[:22]}  ↔  {b[:22]}")

print(f"\n   范围: {min(same_sims):.3f} ~ {max(same_sims):.3f}")
print(f"   均值: {sum(same_sims)/len(same_sims):.3f}")

# ============ ② 不同事的相似度 ============
print("\n② 不同的事的相似度")
diff_sims = []
for a, b in DIFF:
    s = sim(a, b)
    diff_sims.append(s)
    print(f"   {s:.3f}  {a[:22]}  ↔  {b[:22]}")

print(f"\n   范围: {min(diff_sims):.3f} ~ {max(diff_sims):.3f}")
print(f"   均值: {sum(diff_sims)/len(diff_sims):.3f}")

# ============ ③ 边界情况 ============
print("\n③ 边界情况（相关但可能不该合并）")
border_sims = []
for a, b in BORDER:
    s = sim(a, b)
    border_sims.append(s)
    print(f"   {s:.3f}  {a[:22]}  ↔  {b[:22]}")

# ============ ④ 找分界线 ============
print("\n④ 分界线分析")

same_min = min(same_sims)
diff_max = max(diff_sims)

print(f"\n   同一件事的最低相似度: {same_min:.3f}")
print(f"   不同事的最高相似度:   {diff_max:.3f}")

if same_min > diff_max:
    gap = same_min - diff_max
    threshold = (same_min + diff_max) / 2
    print(f"\n   ✅ 有清晰分界，间隔 {gap:.3f}")
    print(f"   建议阈值: {threshold:.3f}")
    verdict = "clear"
else:
    print(f"\n   ⚠ 有重叠！无法用单一阈值完美分开")
    print(f"      同一件事最低 {same_min:.3f} < 不同事最高 {diff_max:.3f}")
    # 权衡：优先不误杀（取同一件事的下分位）
    same_sorted = sorted(same_sims)
    threshold = same_sorted[max(0, len(same_sorted) // 10)]  # 10% 分位
    print(f"   权衡后建议阈值: {threshold:.3f}")
    print(f"      （宁可漏判，也不误杀不同信息）")
    verdict = "overlap"

# ============ ⑤ 在当前阈值下的表现 ============
print("\n⑤ 各候选阈值的表现")

print(f"\n   {'阈值':>6} {'同件事识别率':>12} {'不同事误判率':>12}")
for t in [0.85, 0.88, 0.90, 0.92, 0.94, 0.96, 0.98]:
    tp = sum(1 for s in same_sims if s >= t) / len(same_sims)
    fp = sum(1 for s in diff_sims if s >= t) / len(diff_sims)
    mark = ""
    if fp == 0 and tp > 0.5:
        mark = " ← 零误判"
    print(f"   {t:>6.2f} {tp:>11.0%} {fp:>11.0%}{mark}")

# ============ 结论 ============
print("\n" + "=" * 76)
print("结论")
print("=" * 76)

# 选一个「零误判 + 识别率最高」的阈值
best_t = None
best_tp = -1
for t in [x / 100 for x in range(80, 100)]:
    fp = sum(1 for s in diff_sims if s >= t) / len(diff_sims)
    tp = sum(1 for s in same_sims if s >= t) / len(same_sims)
    if fp == 0 and tp > best_tp:
        best_tp = tp
        best_t = t

print(f"""
  样本量: 同件事 {len(SAME)} 对 / 不同事 {len(DIFF)} 对 / 边界 {len(BORDER)} 对

  同一件事相似度: {min(same_sims):.3f} ~ {max(same_sims):.3f}（均值 {sum(same_sims)/len(same_sims):.3f}）
  不同事相似度:   {min(diff_sims):.3f} ~ {max(diff_sims):.3f}（均值 {sum(diff_sims)/len(diff_sims):.3f}）

  零误判前提下识别率最高的阈值: {best_t:.2f}
  在该阈值下:
    · 同一件事识别率: {sum(1 for s in same_sims if s >= best_t)/len(same_sims):.0%}
    · 不同事误判率:   {sum(1 for s in diff_sims if s >= best_t)/len(diff_sims):.0%}

  → 当前代码用的 0.95 {'偏保守' if best_t < 0.95 else '合适或偏激进'}
""")

out = {
    "experiment": "E-309",
    "same_sims": [round(s, 4) for s in same_sims],
    "diff_sims": [round(s, 4) for s in diff_sims],
    "border_sims": [round(s, 4) for s in border_sims],
    "same_min": round(min(same_sims), 4),
    "diff_max": round(max(diff_sims), 4),
    "recommended": best_t,
    "current": 0.95,
    "verdict": verdict,
}
p = os.path.join(_HERE, "results", "e309_threshold.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e309_threshold.json")
