"""E-221 修正评分：正确处理 empty_gold 与 unknown 的重叠

问题：
  1. 宽松准确率 >1：empty_gold 和 unknown 有重叠，除数算错
  2. 对抗题 unknown 36/40：需要查清是不是 gold 为空（abstention 题）

正确口径：
  · 有效题 = gold 非空 且 verdict != unknown
  · 严格 = correct / 全部 210
  · 宽松 = correct / 有效题
"""
import json
import os
from collections import Counter

TASK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "work", "e221")
RESULTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")

judge = {}
for i in range(3):
    p = os.path.join(TASK, f"judge_part{i}.json")
    if os.path.exists(p):
        for r in json.load(open(p, encoding="utf-8")):
            judge[r["cid"]] = r

gold = json.load(open(os.path.join(TASK, "gold.json"), encoding="utf-8"))

CAT_NAMES = {"1": "单跳", "2": "时间", "3": "多跳", "4": "常识", "5": "对抗"}

by_cat = {}
total_correct = total_incorrect = total_unknown = total_empty = 0

for g in gold:
    cid = g["cid"]
    cat = g["cat"]
    gold_ans = str(g.get("answer", "")).strip()
    verdict = judge.get(cid, {}).get("verdict", "unknown")

    d = by_cat.setdefault(cat, {
        "n": 0, "correct": 0, "incorrect": 0,
        "unknown": 0, "empty_gold": 0,
    })
    d["n"] += 1
    if not gold_ans:
        d["empty_gold"] += 1
        total_empty += 1
    if verdict == "correct":
        d["correct"] += 1
        total_correct += 1
    elif verdict == "incorrect":
        d["incorrect"] += 1
        total_incorrect += 1
    else:
        d["unknown"] += 1
        total_unknown += 1

n = len(gold)
strict = total_correct / n

# 有效题：gold 非空 且 verdict 不是 unknown（即模型实际作答了，且有标准答案可判）
valid = 0
for g in gold:
    cid = g["cid"]
    verdict = judge.get(cid, {}).get("verdict", "unknown")
    gold_ans = str(g.get("answer", "")).strip()
    if gold_ans and verdict != "unknown":
        valid += 1
lenient = total_correct / valid if valid > 0 else 0

print("=" * 94)
print("E-221 修正评分")
print("=" * 94)
print(f"\n  总题数 {n}，其中 gold 为空 {total_empty} 题，unknown {total_unknown} 题")
print(f"  有效题（gold非空 且 模型作答）: {valid}")

print(f"\n  verdict 分布:")
print(f"    correct:   {total_correct}")
print(f"    incorrect: {total_incorrect}")
print(f"    unknown:   {total_unknown}")
print(f"    empty_gold:{total_empty}")

print(f"\n  严格准确率（correct / 全部 {n}）: {strict:.4f}")
print(f"  宽松准确率（correct / 有效 {valid}）: {lenient:.4f}")

print(f"\n  分题型:")
print(f"  {'类别':<10}{'n':<6}{'正确':<6}{'错误':<6}{'unknown':<10}{'空gold':<8}{'严格率'}")
print("-" * 66)
for cat in sorted(by_cat):
    d = by_cat[cat]
    r = d["correct"] / d["n"] if d["n"] else 0
    print("%-10s%-6d%-6d%-6d%-10d%-8d%.4f" % (
        CAT_NAMES.get(cat, cat), d["n"], d["correct"], d["incorrect"],
        d["unknown"], d["empty_gold"], r))

print(f"""
  ── 业界自报（LLM-as-judge，口径不完全一致，仅供参考）──
  Mem0:       91.6 F1
  MemMachine: 91.7 F1
  Zep:        94.8 DMR
  Letta:      74.0（gpt-4o-mini 文件系统）

  ── 我们（210 题抽样，完整链路检索+作答+判等）──
  严格: {strict:.4f}   宽松: {lenient:.4f}
""")

out = {"experiment": "E-221-fixed", "n": n, "valid": valid,
       "correct": total_correct, "incorrect": total_incorrect,
       "unknown": total_unknown, "empty_gold": total_empty,
       "strict": round(strict, 4), "lenient": round(lenient, 4),
       "by_cat": by_cat}
with open(os.path.join(RESULTS, "e221_fullchain.json"), "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 {os.path.join(RESULTS, 'e221_fullchain.json')}")
