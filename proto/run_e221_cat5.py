"""E-221 对抗题专项分析：空 gold 题里模型的行为

LoCoMo 对抗题 = abstention 题（无答案，模型应答 I don't know）。
空 gold 是正常的。关键看：空 gold 的题，模型是答 unknown（对）还是编造（错）。
"""
import json
import os

TASK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "work", "e221")

judge = {}
for i in range(3):
    p = os.path.join(TASK, f"judge_part{i}.json")
    if os.path.exists(p):
        for r in json.load(open(p, encoding="utf-8")):
            judge[r["cid"]] = r

gold = json.load(open(os.path.join(TASK, "gold.json"), encoding="utf-8"))

# 对抗题 + 空 gold 的题，看模型行为
cat5 = [g for g in gold if g["cat"] == "5"]
print(f"对抗题 {len(cat5)} 题\n")

from collections import Counter
behaviors = Counter()
for g in cat5:
    cid = g["cid"]
    gold_ans = str(g.get("answer", "")).strip()
    verdict = judge.get(cid, {}).get("verdict", "unknown")
    pred = ""
    # 从 answer 文件拿 pred
    # 简化：用 verdict 区分
    if not gold_ans:
        # 空 gold = abstention 题，模型应该答 unknown
        if verdict == "unknown":
            behaviors["空gold + 模型答unknown（对）"] += 1
        else:
            behaviors["空gold + 模型编造（错）"] += 1
    else:
        # 有 gold
        if verdict == "correct":
            behaviors["有gold + 答对"] += 1
        elif verdict == "incorrect":
            behaviors["有gold + 答错"] += 1
        else:
            behaviors["有gold + 答unknown（漏答）"] += 1

print("对抗题行为分布:")
for k, v in behaviors.most_common():
    print(f"  {k}: {v}")

# 关键：对抗题的正确口径
n_abstention_correct = behaviors.get("空gold + 模型答unknown（对）", 0)
n_abstention_wrong = behaviors.get("空gold + 模型编造（错）", 0)
n_answered_correct = behaviors.get("有gold + 答对", 0)
n_answered_wrong = behaviors.get("有gold + 答错", 0)

print(f"\n对抗题正确口径：")
print(f"  abstention 题（空gold）: 模型正确说 unknown {n_abstention_correct}，编造 {n_abstention_wrong}")
print(f"  有答案题: 答对 {n_answered_correct}，答错 {n_answered_wrong}")
