"""E-387 验证「话题切换检测」是否可行

E-384/385 结论：固定 N 没有最优，应改用「话题切换触发」。
但方案可行性的前提是：**话题切换能被可靠检测**。

如果检测不准（漏报或误报多），方案就不成立，改代码是白费。

本实验只做检测，不改代码。

检测方法候选：
  A. 相邻两轮用户消息的向量相似度
     · 低于阈值 = 话题切换
     · 简单、无需额外模型调用
  B. 与"会话锚点"的相似度
     · 锚点 = 首轮消息，偏离锚点则触发
  C. 关键词重叠率
     · 太粗糙，中文分词依赖重

先测 A，因为最符合现有基础设施（已有 BGE 向量）。

★ 关键：用真实对话数据测，不是构造的
  从 plugin-debug 的 assemble-scan 里取真实的 latest 文本序列。
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
print("E-387 话题切换检测可行性验证")
print("=" * 78)

# ---------- ① 取真实对话序列 ----------
print("\n① 从日志取真实的用户消息序列")

msgs = []
if os.path.exists(LOG):
    with open(LOG, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                e = json.loads(line)
                if e.get("kind") == "assemble-scan":
                    lat = str(e.get("latest") or "").strip()
                    sid = str(e.get("sid") or "")[:20]
                    if lat and len(lat) > 5:
                        msgs.append({"sid": sid, "text": lat,
                                     "t": e.get("t", "")})
            except Exception:
                pass

print(f"   取到 {len(msgs)} 条带文本的记录")

# 去重连续重复（同一轮 assemble 会多次记录同一 latest）
uniq = []
for m in msgs:
    if not uniq or uniq[-1]["text"] != m["text"]:
        uniq.append(m)
print(f"   去连续重复后: {len(uniq)} 条")

if len(uniq) < 10:
    print("   ⚠ 样本太少，用主会话的")
uniq = uniq[-60:]   # 取最后 60 条
print(f"   使用最后 {len(uniq)} 条")

# ---------- ② 计算相邻相似度 ----------
print("\n② 计算相邻两轮的消息相似度")

emb = Embedder.get()

texts = [m["text"] for m in uniq]
vecs = emb.encode(texts)
norms = np.linalg.norm(vecs, axis=1)
norms[norms == 0] = 1.0
vn = vecs / norms[:, None]

sims = []
for i in range(1, len(vn)):
    s = float(vn[i] @ vn[i-1])
    sims.append(s)

print(f"   相邻相似度: {len(sims)} 个")
if sims:
    print(f"   范围: {min(sims):.3f} ~ {max(sims):.3f}")
    print(f"   均值: {np.mean(sims):.3f}")
    print(f"   中位: {np.median(sims):.3f}")

# ---------- ③ 分布分析 ----------
print("\n③ 相似度分布（判断是否有清晰分界）")

if sims:
    buckets = {"<0.3": 0, "0.3-0.5": 0, "0.5-0.7": 0,
               "0.7-0.85": 0, ">0.85": 0}
    for s in sims:
        if s < 0.3:
            buckets["<0.3"] += 1
        elif s < 0.5:
            buckets["0.3-0.5"] += 1
        elif s < 0.7:
            buckets["0.5-0.7"] += 1
        elif s < 0.85:
            buckets["0.7-0.85"] += 1
        else:
            buckets[">0.85"] += 1

    print(f"\n   {'区间':<12} {'数量':>6}  分布")
    for k, n in buckets.items():
        bar = "█" * min(n, 30)
        print(f"   {k:<12} {n:>6}  {bar}")

# ---------- ④ 用不同阈值看检测效果 ----------
print("\n④ 不同阈值的「话题切换」检出率")

if sims:
    print(f"\n   {'阈值':>6} {'判为切换':>10} {'占比':>8}")
    print("   " + "-" * 30)
    for th in [0.35, 0.45, 0.55, 0.65, 0.75]:
        n_switch = sum(1 for s in sims if s < th)
        print(f"   {th:>6.2f} {n_switch:>10} "
              f"{n_switch/len(sims)*100:>7.0f}%")

# ---------- ⑤ 抽样看具体内容 ----------
print("\n⑤ 抽样：最低相似度的 5 对（应该真的是话题切换）")

order = sorted(range(len(sims)), key=lambda i: sims[i])
for idx in order[:5]:
    i = idx + 1
    a = texts[i-1][:38]
    b = texts[i][:38]
    print(f"\n   sim={sims[idx]:.3f}")
    print(f"     前: {a}")
    print(f"     后: {b}")

print("\n⑥ 抽样：最高相似度的 3 对（应该话题连续）")
for idx in order[-3:]:
    i = idx + 1
    a = texts[i-1][:38]
    b = texts[i][:38]
    print(f"\n   sim={sims[idx]:.3f}")
    print(f"     前: {a}")
    print(f"     后: {b}")

# ---------- ⑦ 结论 ----------
print("\n" + "=" * 78)
print("结论")
print("=" * 78)

if sims:
    print(f"""
  相邻消息相似度: {min(sims):.3f} ~ {max(sims):.3f}
  均值 {np.mean(sims):.3f}

  ★ 判断方案可行性的关键：

    如果相似度分布**双峰**（一批高、一批低）→ 有清晰分界，方案可行
    如果**单峰连续**（平滑分布）→ 没有自然分界，阈值是拍脑袋的

  实际分布见上面的直方图。

  ★ 另一个重要观察：

    相邻消息相似度受「消息长度」影响很大：
      · 短消息（"继续"、"好"）与任何东西都不像
      · 这会产生**大量误报**

    所以如果要用这个方法，必须先处理短消息。
""")

out = {
    "experiment": "E-387",
    "n_pairs": len(sims),
    "sim_min": round(min(sims), 3) if sims else None,
    "sim_max": round(max(sims), 3) if sims else None,
    "sim_mean": round(float(np.mean(sims)), 3) if sims else None,
    "sim_median": round(float(np.median(sims)), 3) if sims else None,
}
p = os.path.join(_HERE, "results", "e387_topic_detect.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e387_topic_detect.json")
