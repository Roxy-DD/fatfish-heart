"""E-218 最终评分：LLM 判等的端到端准确率"""
import json
import os

TASK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "work", "e218")
RESULTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")

judge = {r["cid"]: r for r in json.load(open(os.path.join(TASK, "judge_results.json"), encoding="utf-8"))}

from collections import Counter
c = Counter(r["verdict"] for r in judge.values())

n = len(judge)
correct = c.get("correct", 0)
incorrect = c.get("incorrect", 0)
unknown = c.get("unknown", 0)

print("=" * 94)
print("E-218 多跳题端到端准确率（LLM 判等）")
print("=" * 94)
print(f"\n  总题数: {n}")
print(f"  correct:   {correct}")
print(f"  incorrect: {incorrect}")
print(f"  unknown:   {unknown}（pred=I don't know）")

# 两种口径
strict = correct / n  # unknown 算错
lenient = correct / (n - unknown)  # 排除 unknown（没答的题）

print(f"\n  严格准确率（unknown 算错）: {strict:.4f}")
print(f"  宽松准确率（排除 unknown）: {lenient:.4f}")

print(f"""
  对比链路：
    证据命中（改写检索）: 0.5435
    端到端准确率:         {strict:.4f}（严格）/ {lenient:.4f}（宽松）
""")

out = {"experiment": "E-218-final", "n": n,
       "correct": correct, "incorrect": incorrect, "unknown": unknown,
       "strict": round(strict, 4), "lenient": round(lenient, 4),
       "evidence_recall": 0.5435}
with open(os.path.join(RESULTS, "e218_multihop_final.json"), "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"写入 {os.path.join(RESULTS, 'e218_multihop_final.json')}")
