"""E-221 评分：完整链路准确率（LLM 判等）

流程：
  1. 合并 3 份 answers
  2. 生成判等任务包（pred vs gold）
  3. 子代理 LLM 判等
  4. 汇总分题型准确率，对齐业界口径

本脚本做 1-2，判等由子代理做。
"""
import json
import os

TASK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "work", "e221")

# 合并 answers
answers = {}
for i in range(3):
    p = os.path.join(TASK, f"answers_part{i}.json")
    if os.path.exists(p):
        for r in json.load(open(p, encoding="utf-8")):
            answers[r["cid"]] = r

gold = json.load(open(os.path.join(TASK, "gold.json"), encoding="utf-8"))

# 生成判等任务包
pairs = []
for g in gold:
    cid = g["cid"]
    pred = answers.get(cid, {}).get("answer", "")
    pairs.append({
        "cid": cid,
        "cat": g["cat"],
        "question": g["question"],
        "gold": str(g["answer"]),
        "pred": str(pred),
    })

with open(os.path.join(TASK, "judge_pairs.json"), "w", encoding="utf-8") as f:
    json.dump(pairs, f, ensure_ascii=False, indent=1)

print(f"answers: {len(answers)} 条")
print(f"判等任务 {len(pairs)} 题 → {TASK}/judge_pairs.json")
