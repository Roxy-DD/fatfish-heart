"""合并 3 份 judge 结果，用文件里的 verdict 字段做权威统计"""
import json
import os
from collections import Counter

TASK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "work", "e221")
RESULTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")

# 合并 judge 结果
judge = {}
for i in range(3):
    p = os.path.join(TASK, f"judge_part{i}.json")
    if os.path.exists(p):
        for r in json.load(open(p, encoding="utf-8")):
            judge[r["cid"]] = r

gold = json.load(open(os.path.join(TASK, "gold.json"), encoding="utf-8"))

CAT_NAMES = {"1": "单跳", "2": "时间", "3": "多跳", "4": "常识", "5": "对抗"}

print("=" * 94)
print("E-221 LoCoMo 完整链路准确率（以 verdict 字段为准）")
print("=" * 94)
print(f"\n  合并 judge: {len(judge)} 题，gold: {len(gold)} 题")

# 检查 gold 里 answer 为空的情况（这些是数据问题，需排除）
empty_gold = [g["cid"] for g in gold if not str(g.get("answer", "")).strip()]
print(f"  gold 为空的题: {len(empty_gold)}")

by_cat = {}
total_correct = total_incorrect = total_unknown = 0

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
        total_incorrect += 1
    else:
        d["unknown"] += 1
        total_unknown += 1

n = len(gold)
strict = total_correct / n if n else 0
# 宽松：排除 unknown 和 gold 为空的题
answerable = n - total_unknown - len(empty_gold)
lenient = total_correct / answerable if answerable > 0 else 0

print(f"\n  {'verdict':<12}{'数量':<8}{'占比'}")
print("-" * 40)
print(f"{'correct':<12}{total_correct:<8}{total_correct/n:.1%}")
print(f"{'incorrect':<12}{total_incorrect:<8}{total_incorrect/n:.1%}")
print(f"{'unknown':<12}{total_unknown:<8}{total_unknown/n:.1%}")

print(f"\n  严格准确率（unknown 算错）: {strict:.4f}")
print(f"  宽松准确率（排除 unknown + 空gold）: {lenient:.4f}")

print(f"\n  分题型（严格口径）:")
print(f"  {'类别':<10}{'正确/总':<14}{'准确率':<12}{'unknown'}")
print("-" * 60)
for cat in sorted(by_cat):
    d = by_cat[cat]
    r = d["correct"] / d["n"] if d["n"] else 0
    frac = "%d/%d" % (d["correct"], d["n"])
    print("%-10s%-14s%-12.4f%d" % (
        CAT_NAMES.get(cat, cat), frac, r, d["unknown"]))

print(f"""
  ── 业界自报（LLM-as-judge 口径，注意口径仍不完全一致）──
  Mem0:       91.6 F1
  MemMachine: 91.7 F1
  Zep:        94.8 DMR
  Letta:      74.0（gpt-4o-mini，文件系统塞历史）

  ── 我们（210 题抽样，完整链路）──
  严格: {strict:.4f}
  宽松: {lenient:.4f}
""")

out = {"experiment": "E-221", "n": n,
       "correct": total_correct, "incorrect": total_incorrect,
       "unknown": total_unknown, "empty_gold": len(empty_gold),
       "strict": round(strict, 4), "lenient": round(lenient, 4),
       "by_cat": by_cat}
with open(os.path.join(RESULTS, "e221_fullchain.json"), "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 {os.path.join(RESULTS, 'e221_fullchain.json')}")
