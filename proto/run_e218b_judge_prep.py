"""E-218b 用 LLM 判等多跳题端到端准确率

字符串判等太严（多跳答案开放），导致大量语义正确的被判错。
生成 LLM 判等任务包，让子代理判断 pred 与 gold 是否语义等价。
"""
import json
import os

TASK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "work", "e218")

tasks = json.load(open(os.path.join(TASK, "qa_gold.json"), encoding="utf-8"))
answers = {r["cid"]: r for r in json.load(open(os.path.join(TASK, "qa_answers.json"), encoding="utf-8"))}

pairs = []
for t in tasks:
    cid = t["cid"]
    pred = answers.get(cid, {}).get("answer", "")
    gold = str(t["answer"])
    pairs.append({
        "cid": cid,
        "question": t["question"],
        "gold": gold,
        "pred": pred,
    })

with open(os.path.join(TASK, "judge_pairs.json"), "w", encoding="utf-8") as f:
    json.dump(pairs, f, ensure_ascii=False, indent=1)

print(f"判等任务 {len(pairs)} 题 → {TASK}/judge_pairs.json")
