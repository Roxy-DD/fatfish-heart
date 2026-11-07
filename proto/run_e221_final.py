"""E-221 最终正确评分：区分 abstention 题的正确口径

LoCoMo 的对抗题（cat=5）是 abstention 题：
  空 gold = 标准答案是"没有答案"，模型应该答 I don't know
  所以空 gold 题里，模型答 unknown = 正确，编造 = 错误

正确口径：
  · abstention 题（空 gold）：correct = 模型答 unknown
  · 正常题（有 gold）：correct = 判等为 correct
  · 严格 = correct / 全部
  · 宽松 = correct / 有效题（排除那些"模型答unknown但实际有答案"的漏答）
"""
import json
import os

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
total_correct = 0
total_n = 0

for g in gold:
    cid = g["cid"]
    cat = g["cat"]
    gold_ans = str(g.get("answer", "")).strip()
    verdict = judge.get(cid, {}).get("verdict", "unknown")

    d = by_cat.setdefault(cat, {"n": 0, "correct": 0})
    d["n"] += 1
    total_n += 1

    if not gold_ans:
        # abstention 题：模型答 unknown 才算对
        if verdict == "unknown":
            d["correct"] += 1
            total_correct += 1
    else:
        # 正常题：判等 correct 才算对
        if verdict == "correct":
            d["correct"] += 1
            total_correct += 1

strict = total_correct / total_n if total_n else 0

print("=" * 94)
print("E-221 最终正确评分（区分 abstention）")
print("=" * 94)

print(f"\n  总题数 {total_n}，正确 {total_correct}")
print(f"  严格准确率: {strict:.4f}")

print(f"\n  分题型:")
print(f"  {'类别':<10}{'n':<6}{'正确':<6}{'准确率'}")
print("-" * 40)
for cat in sorted(by_cat):
    d = by_cat[cat]
    r = d["correct"] / d["n"] if d["n"] else 0
    print("%-10s%-6d%-6d%.4f" % (CAT_NAMES.get(cat, cat), d["n"], d["correct"], r))

print(f"""
  ── 业界自报（LLM-as-judge，口径不完全一致）──
  Mem0:       91.6 F1
  MemMachine: 91.7 F1
  Zep:        94.8 DMR
  Letta:      74.0

  ── 我们（210 题抽样，完整链路，含 abstention 正确处理）──
  严格: {strict:.4f}
""")

out = {"experiment": "E-221-final", "n": total_n,
       "correct": total_correct, "strict": round(strict, 4),
       "by_cat": by_cat}
with open(os.path.join(RESULTS, "e221_fullchain.json"), "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n写入 {os.path.join(RESULTS, 'e221_fullchain.json')}")
