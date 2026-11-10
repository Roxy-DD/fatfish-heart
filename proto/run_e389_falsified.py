"""E-389 固化否证结论：相邻相似度检测话题切换 —— 不可行

E-387/388 实验数据：
  相邻消息相似度 0.421 ~ 0.852，单峰分布
  中位数 0.538

关键反例（直接推翻方案）：
  ┌─────────────────────────────────────────────────────────┐
  │ sim=0.852（最高）                                        │
  │   「你怎么给停止了？检查一下」                            │
  │   「哎，你怎么又停止了」                                  │
  │   → 同一抱怨重复，**不是新话题**，但相似度最高            │
  ├─────────────────────────────────────────────────────────┤
  │ sim=0.464（偏低）                                        │
  │   「你能看到我刚才在笔记本里加入的新笔记吗？关于 AGI 会是…」│
  │   「他说的是不必无损迁移…这是我最新的对 AGI 的个人认知」   │
  │   → **同一话题（AGI）**，但相似度偏低                     │
  └─────────────────────────────────────────────────────────┘

这两个反例方向相反：
  · 高相似度 ≠ 话题连续（重复≠延续）
  · 低相似度 ≠ 话题切换（换词≠换题）

→ 语义相似度与"话题是否延续"相关性极弱，不能作为触发器。

本测试固化这个否证，防止以后重复尝试同一方案。
"""
import sys
import os
import json
import warnings

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))

results = []


def check(name, cond, detail=""):
    results.append({"name": name, "ok": bool(cond), "detail": detail})
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  {detail}" if detail else ""))


print("=" * 74)
print("E-389 否证固化：相邻相似度不能检测话题切换")
print("=" * 74)

d = json.load(open(os.path.join(_HERE, "results", "e388_manual_check.json"),
                   encoding="utf-8"))

print(f"\n① 实验数据回顾")
print(f"   样本: {d['n_pairs']} 对相邻消息")
print(f"   相似度: {d['sim_min']:.3f} ~ {d['sim_max']:.3f}")
print(f"   中位: {d['sim_median']:.3f}")

# ---------- ② 检查反例是否仍在数据里 ----------
print("\n② 两个关键反例")

samples = d.get("samples", [])

# 反例 1：高相似度的"同一抱怨重复"
high = [s for s in samples if s["sim"] >= 0.8]
has_high_case = any("停止" in s["a"] or "停止" in s["b"] for s in high)
print(f"   高相似度样本（>=0.8）: {len(high)} 个")
if high:
    for s in high:
        print(f"     sim={s['sim']:.3f}")
        print(f"       A: {s['a'][:50]}")
        print(f"       B: {s['b'][:50]}")
check("存在「高相似度但非话题延续」的反例", has_high_case,
      "重复抱怨 vs 话题延续")

# 反例 2：低相似度的"同一话题"
low = [s for s in samples if 0.44 <= s["sim"] <= 0.48]
has_low_case = any("AGI" in s["a"] and "AGI" in s["b"] for s in low)
print(f"\n   低相似度样本（0.44~0.48）: {len(low)} 个")
agi_found = [s for s in low if "AGI" in s["a"] and "AGI" in s["b"]]
if agi_found:
    for s in agi_found:
        print(f"     sim={s['sim']:.3f}（两句都含 AGI，是同一话题）")
        print(f"       A: {s['a'][:50]}")
        print(f"       B: {s['b'][:50]}")
check("存在「低相似度但同话题」的反例", has_low_case,
      "换词但同主题")

# ---------- ③ 分布特征 ----------
print("\n③ 分布特征")

sims = [s["sim"] for s in samples]
import statistics
print(f"   均值 {statistics.mean(sims):.3f}")
print(f"   标准差 {statistics.stdev(sims):.3f}" if len(sims) > 1 else "")

# 单峰判断：中位数附近样本最多
mid = sum(1 for s in sims if 0.45 <= s <= 0.70)
edges = sum(1 for s in sims if s < 0.45 or s > 0.70)
print(f"   中间区间(0.45~0.70): {mid} 个")
print(f"   两端(<0.45 或 >0.70): {edges} 个")

check("分布为单峰（中间密集、两端稀疏）",
      mid > edges,
      f"中间 {mid} vs 两端 {edges}")

# ---------- ④ 结论固化 ----------
print("\n④ 固化的否证结论")

verdict_text = d.get("verdict", "")
check("实验结果已明确标注方案不可行",
      "不实现" in verdict_text or "不可靠" in verdict_text,
      verdict_text[:50])

print("""
  ★ 为什么这个方案不可行（写进记忆，避免重复尝试）：

    语义相似度衡量「概念距离」，不衡量「话题是否延续」。

    三类典型错判：
      1. 同一句话重复说 → 相似度极高，但零新信息
         （"你怎么又停了" vs "你怎么又停止了" = 0.852）
      2. 换话题但用相似套话 → 相似度虚高
         （"我有个问题…" 开头的话都像）
      3. 延续话题但换用词 → 相似度虚低
         （"AGI 会是什么样的" vs "不必无损迁移" = 0.464）

    这三类错判方向相反，无法用调阈值解决。

  ★ 如果以后还想做「按需注入」，正确的方向是：
    · 不猜话题，而是让**检索分数自己说话**
      （每轮都检索，看 top 分数是否显著高于首轮已注入的）
    · 或直接问模型「这轮需要回忆什么」——但那是额外模型调用
    · 或放弃智能判断，用固定 N（承认它是线性交换）

    目前证据下，我倾向：**保持现状（只注入一次）+ 保留开关**，
    等有真实任务效果数据后再决定。
""")

# ---------- 汇总 ----------
print("=" * 74)
n_ok = sum(1 for x in results if x["ok"])
print(f"结果：{n_ok}/{len(results)} 通过")
for x in results:
    if not x["ok"]:
        print(f"  ❌ {x['name']}  {x['detail']}")
print("=" * 74)

out = {
    "experiment": "E-389",
    "verdict": "相邻相似度不可用于话题切换检测（有双向反例）",
    "has_high_case": has_high_case,
    "has_low_case": has_low_case,
    "is_unimodal": mid > edges,
    "future_direction": "不猜话题，改看检索分数，或用固定N",
}
p = os.path.join(_HERE, "results", "e389_falsified.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e389_falsified.json")

sys.exit(0 if n_ok == len(results) else 1)
