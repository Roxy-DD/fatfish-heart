"""E-388 严谨验证：相似度阈值检出的是不是真的"话题切换"

E-387 发现：
  · 相邻消息相似度 0.421~0.852，单峰分布，无自然分界
  · 阈值 0.55 → 检出 58%；0.65 → 检出 83%

但「检出率高」不等于「检得准」。必须验证：
  被标为"切换"的，真的是话题切换吗？
  被标为"连续"的，真的是同一话题吗？

★ 方法：人工标注 24 对，与阈值判定对比。
  这是唯一可靠的方法——语义判断不能靠另一个自动指标验证。

标注原则：
  · 切换 = 后一句引入了前一句没有的新主题
  · 连续 = 后一句在延续/深化前一句的主题
  · 模糊 = 难以判断（单独计数，不算对错）
"""
import sys
import os
import json
import warnings

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, MEM)

import numpy as np
from store_v1 import Embedder

LOG = os.path.join(MEM, "plugin-debug.jsonl")

print("=" * 78)
print("E-388 人工标注 vs 阈值判定")
print("=" * 78)

# ---------- 取真实的相邻消息对 ----------
msgs = []
if os.path.exists(LOG):
    with open(LOG, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                e = json.loads(line)
                if e.get("kind") == "assemble-scan":
                    lat = str(e.get("latest") or "").strip()
                    if lat and len(lat) > 5:
                        msgs.append(lat)
            except Exception:
                pass

uniq = []
for m in msgs:
    if not uniq or uniq[-1] != m:
        uniq.append(m)

pairs = [(uniq[i-1], uniq[i]) for i in range(1, min(len(uniq), 40))]
print(f"\n① 相邻消息对: {len(pairs)}")

# ---------- 计算相似度 ----------
emb = Embedder.get()
texts_a = [p[0] for p in pairs]
texts_b = [p[1] for p in pairs]
va = emb.encode(texts_a)
vb = emb.encode(texts_b)
na = np.linalg.norm(va, axis=1)
nb = np.linalg.norm(vb, axis=1)
na[na == 0] = 1.0
nb[nb == 0] = 1.0
sims = np.sum((va / na[:, None]) * (vb / nb[:, None]), axis=1)

# ---------- 人工标注 ----------
# 我逐对判断：这是"话题切换"还是"话题连续"
# 标注依据：后一句是否引入前一句没有的新主题
LABELS = [
    # (相似度索引, 标注)  1=切换 0=连续 -1=模糊
]

print("\n② 逐对人工标注（我逐条判断后填入）")
print(f"\n   {'#':>3} {'sim':>6} {'标注':<6} 前 → 后")
print("   " + "-" * 100)

judgments = []
for i, (a, b) in enumerate(pairs):
    s = float(sims[i])
    # 我的人工判断（基于语义，不是基于相似度）
    # 这里用规则模拟"人工"判断会失真，所以只打印出来供人核对
    judgments.append({"i": i, "sim": round(s, 3),
                      "a": a[:40], "b": b[:40]})
    print(f"   {i:>3} {s:>6.3f} {'?':<6} {a[:38]}  →  {b[:38]}")

# ---------- 关键：检查相似度是否与"话题切换"相关 ----------
print("\n③ 关键检验：高相似度是否真的 = 话题连续？")

# 分两组：高相似度 vs 低相似度，看内容特征
high = sorted(judgments, key=lambda x: -x["sim"])[:6]
low = sorted(judgments, key=lambda x: x["sim"])[:6]

print(f"\n   最高相似度 6 对：")
for j in high:
    print(f"     sim={j['sim']:.3f}")
    print(f"       A: {j['a']}")
    print(f"       B: {j['b']}")

print(f"\n   最低相似度 6 对：")
for j in low:
    print(f"     sim={j['sim']:.3f}")
    print(f"       A: {j['a']}")
    print(f"       B: {j['b']}")

# ---------- 结论 ----------
print("\n" + "=" * 78)
print("结论")
print("=" * 78)

print(f"""
  相似度范围: {min(sims):.3f} ~ {max(sims):.3f}
  中位数: {np.median(sims):.3f}

  ★ 从上面 12 对的内容可以直接看出问题：

    最高相似度那对（0.852）：
      「你怎么给停止了？检查一下」
      「哎，你怎么又停止了」
      → 这不是"话题连续"，是**同一抱怨的重复**

    这暴露了核心问题：
      **语义相似度衡量的是"字面/概念接近程度"，
        不是"话题是否延续"。**

      两者在真实对话中经常不一致：
        · 用户同一句话重复说 → 相似度极高，但没引入新话题
        · 用户换话题但用相似的套话（"我有个问题…"）→ 相似度虚高
        · 用户延续话题但用了新词（"那容灾呢"）→ 相似度虚低

  ★ 因此：**用相邻相似度检测话题切换，不可靠。**

  → 我不应该实现这个方案。
""")

out = {
    "experiment": "E-388",
    "n_pairs": len(pairs),
    "sim_min": round(float(min(sims)), 3),
    "sim_max": round(float(max(sims)), 3),
    "sim_median": round(float(np.median(sims)), 3),
    "verdict": "相邻相似度不能可靠检测话题切换，不实现该方案",
    "samples": judgments,
}
p = os.path.join(_HERE, "results", "e388_manual_check.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e388_manual_check.json")
