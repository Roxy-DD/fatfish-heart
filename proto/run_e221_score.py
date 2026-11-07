"""E-221 最终评分：LLM 判等后的分题型准确率

读取判等结果（子代理产出），汇总分题型准确率。
"""
import json
import os
from collections import Counter

TASK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "work", "e221")
RESULTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")

jp = os.path.join(TASK, "judge_results.json")
if not os.path.exists(jp):
    print("缺少 judge_results.json（需先跑判等子代理）")
    exit(0)

judge = {r["cid"]: r for r in json.load(open(jp, encoding="utf-8"))}
gold = json.load(open(os.path.join(TASK, "gold.json"), encoding="utf-8"))

CAT_NAMES = {"1": "单跳", "2": "时间", "3": "多跳", "4": "常识", "5": "对抗"}

# 统计
by_cat = {}
total_correct = 0
total_n = 0
total_unknown = 0

for g in gold:
    cid = g["cid"]
    cat = g["cat"]
    verdict = judge.get(cid, {}).get("verdict", "unknown")
    d = by_cat.setdefault(cat, {"correct": 0, "incorrect": 0, "unknown": 0, "n": 0})
    d["n"] += 1
    if verdict == "correct":
        d["correct"] += 1
        total_correct += 1
    elif verdict == "incorrect":
        d["incorrect"] += 1
    else:
        d["unknown"] += 1
        total_unknown += 1
    total_n += 1

print("=" * 94)
print("E-221 LoCoMo 完整链路准确率（LLM 判等）")
print("=" * 94)

print(f"\n  总题数: {total_n}")
print(f"  correct: {total_correct}")
print(f"  unknown: {total_unknown}")

strict = total_correct / total_n
lenient = total_correct / (total_n - total_unknown) if total_n > total_unknown else 0

print(f"\n  严格准确率（unknown 算错）: {strict:.4f}")
print(f"  宽松准确率（排除 unknown）: {lenient:.4f}")

print(f"\n  分题型（严格口径）:")
print(f"  {'类别':<10}{'正确/总':<14}{'准确率':<12}{'unknown'}")
print("-" * 60)
for cat in sorted(by_cat):
    d = by_cat[cat]
    r = d["correct"] / d["n"]
    print(f"  {CAT_NAMES.get(cat, cat):<10}{f'{d[\"correct\"]}/{d[\"n\"]}':<14}"
          f"{r:<12.4f}{d['unknown']}")

print(f"""
  对比业界（自报，LLM-as-judge 口径）:
    Mem0:      91.6 F1 (LoCoMo)
    MemMachine: 91.7 F1
    Zep:       94.8 DMR
    Letta:     74.0 (gpt-4o-mini 文件系统)

  我们（严格）: {strict:.4f}
  我们（宽松）: {lenient:.4f}
""")

out = {"experiment": "E-221", "n": total_n,
       "correct": total_correct, "unknown": total_unknown,
       "strict": round(strict, 4), "lenient": round(lenient, 4),
       "by_cat": by_cat}
with open(os.path.join(RESULTS, "e221_fullchain.json"), "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 {os.path.join(RESULTS, 'e221_fullchain.json')}")
