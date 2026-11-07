"""E-287 验证：注入的记忆，模型真的用上了吗？

做过很多检索质量测试（recall@k），但那测的是
"目标记忆有没有出现在返回列表里"。

真正重要的是：**模型看到这些记忆后，回答有没有变好？**

这是检索质量与实际效果之间的最后一环，从未验证过。

方法：
  ① 构造需要用到记忆的问题
  ② A 组：不注入记忆直接答
  ③ B 组：注入记忆后答
  ④ 对比答案质量

但这里有个困难：需要模型参与，而我无法在测试脚本里调用模型。
所以改为**间接验证**：
  · 检查注入的记忆与问题的相关性（precision）
  · 检查注入是否有噪音（无关记忆挤占预算）
"""
import sys
import os
import json
import warnings

warnings.filterwarnings("ignore")
_HERE = os.path.dirname(os.path.abspath(__file__))
_MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, _MEM)

from store_v2 import MemoryStore
import hook as _hook
import jieba.posseg as pseg

DB = os.path.join(_MEM, "dsh_mem.db")
st = MemoryStore(DB)

print("=" * 78)
print("E-287 注入内容的精度分析")
print("=" * 78)

# ============ ① 模拟真实注入场景 ============
# 真实注入 = L0/L1 常驻 + 检索结果（top-k，受预算限制）

# 取 L0/L1
present = _hook.present_ids(st)
print(f"\n常驻记忆（L0/L1）: {len(present)} 条")

present_texts = []
for eid in present:
    ep = st.get(eid)
    if ep:
        info = next((x for x in _hook.protected_of(st)
                     if x["ep_id"] == eid), {})
        present_texts.append({
            "ep_id": eid,
            "level": info.get("level", "?"),
            "note": info.get("note", ""),
            "text": ep.raw_text,
            "chars": len(ep.raw_text),
        })

total_present_chars = sum(x["chars"] for x in present_texts)
print(f"   总字符: {total_present_chars}")

# 按层级统计
from collections import Counter
lvl = Counter(x["level"] for x in present_texts)
print(f"   按层级: {dict(lvl)}")

# ============ ② 模拟查询，看注入的检索结果精度 ============
print("\n② 检索结果的精度（注入的这部分）")

# 用真实的用户问题模式
queries = [
    ("我应该怎么迭代记忆系统", ["迭代", "实验", "数据", "红线"]),
    ("记忆怎么检索", ["检索", "向量", "FTS", "通道"]),
    ("版本坏了怎么办", ["版本", "回退", "迁移", "生命"]),
    ("笔记怎么组织", ["笔记", "随笔", "手册", "整理"]),
    ("什么该记住", ["重要", "代价", "记住", "判断"]),
]

BUDGET = 1500  # autoInjectMaxChars

print(f"\n   {'查询':<24} {'命中':>4} {'相关':>4} {'精度':>8} {'字符':>6}")
print("   " + "-" * 54)

precisions = []
for q, keywords in queries:
    res = st.search(q, top=5)
    # 排除常驻（flag=2）
    items = [(ep, sc, fl) for ep, sc, fl in res if fl != 2]
    chars = sum(len(ep.raw_text) for ep, sc, fl in items)

    relevant = 0
    for ep, sc, fl in items:
        if any(k in ep.raw_text for k in keywords):
            relevant += 1

    p = relevant / len(items) if items else 0
    precisions.append(p)
    print(f"   {q:<24} {len(items):>4} {relevant:>4} {p:>7.0%} {chars:>6}")

avg_p = sum(precisions) / len(precisions) if precisions else 0
print(f"\n   平均精度: {avg_p:.0%}")

# ============ ③ 检查：注入是否被噪音挤占 ============
print("\n③ 噪音检查")
print("   （检索返回的记忆里，有多少与查询无关？）")

noise_examples = []
for q, keywords in queries:
    res = st.search(q, top=5)
    items = [(ep, sc, fl) for ep, sc, fl in res if fl != 2]
    for ep, sc, fl in items:
        if not any(k in ep.raw_text for k in keywords):
            noise_examples.append({
                "query": q,
                "text": ep.raw_text[:50],
                "score": round(sc, 2),
            })

print(f"   无关记忆条数: {len(noise_examples)}")
for ex in noise_examples[:5]:
    print(f"     查询 '{ex['query']}' 返回: {ex['text']}... (cos={ex['score']})")

# ============ ④ 预算分析 ============
print("\n④ 注入预算分析")

print(f"   总预算: {BUDGET} 字")
print(f"   L0/L1 占用: {total_present_chars} 字 "
      f"({total_present_chars/BUDGET*100:.0f}%)")

if total_present_chars > BUDGET:
    print(f"   ⚠ 常驻记忆已超预算 {total_present_chars - BUDGET} 字")
    print("     → 检索结果无空间，用户记忆被挤出")
elif total_present_chars > BUDGET * 0.6:
    print(f"   🟡 常驻占用 {total_present_chars/BUDGET*100:.0f}%，"
          f"留给检索的空间仅 {BUDGET - total_present_chars} 字")
else:
    print(f"   ✅ 常驻占用合理，留 {BUDGET - total_present_chars} 字给检索")

# 检查首轮 vs 后续的差异
print(f"\n   首轮（L0/L1 全文）: {total_present_chars} 字")
# 后续只给标题，估算
summary_chars = sum(len(x["note"]) for x in present_texts if x["note"])
print(f"   后续（L0/L1 标题）: 约 {summary_chars} 字")

# ============ ⑤ 结论 ============
print("\n" + "=" * 78)
print("结论")
print("=" * 78)

issues = []
if avg_p < 0.5:
    issues.append(f"检索精度偏低 {avg_p:.0%}（一半以上是噪音）")
if total_present_chars > BUDGET * 0.8:
    issues.append(f"常驻记忆占用 {total_present_chars/BUDGET*100:.0f}% 预算")
if len(noise_examples) > 10:
    issues.append(f"{len(noise_examples)} 条无关记忆被注入")

if issues:
    print("\n  发现问题：")
    for i in issues:
        print(f"    · {i}")
else:
    print("\n  ✅ 注入内容精度与预算均合理")

print(f"""
  数据：
    常驻记忆: {len(present_texts)} 条 / {total_present_chars} 字
    检索结果平均精度: {avg_p:.0%}
    无关记忆样本: {len(noise_examples)} 条
""")

st.con.close()

out = {
    "experiment": "E-287",
    "present_count": len(present_texts),
    "present_chars": total_present_chars,
    "budget": BUDGET,
    "avg_precision": round(avg_p, 4),
    "noise_count": len(noise_examples),
    "issues": issues,
}
p = os.path.join(_HERE, "results", "e287_injection_quality.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 results/e287_injection_quality.json")
