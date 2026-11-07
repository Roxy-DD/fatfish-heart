"""
E-57 评分（重写，不用模板改）
"""
from __future__ import annotations

import json
import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
TASK = os.path.join(_HERE, "work", "e57_task")
RESULTS = os.path.join(_HERE, "results")

CONDS = ["HIT_1", "HIT_10", "MISS_0", "MISS_NEAR"]
NDOC = {"HIT_1": 1, "HIT_10": 10, "MISS_0": 10, "MISS_NEAR": 10}
ABSTAIN = ["无法确定", "不确定", "不知道", "无法判断", "不清楚", "未出现", "未提及"]


def norm(s):
    s = str(s).strip()
    s = re.sub(r"\s+", "", s)
    for ch in "，。：:、（）()「」\"'":
        s = s.replace(ch, "")
    return s.lower()


def is_abstain(s):
    s = norm(s)
    if not s:
        return True
    return any(norm(a) in s for a in ABSTAIN)


def main():
    rep = {}
    print("=" * 80)
    print("E-57 目标在候选里 vs 不在候选里")
    print("=" * 80)
    print("计分规则：标准答案='无法确定' 时，回答'无法确定'= 正确；编答案 = 错误")
    print(f"{'条件':<12}{'题数':<7}{'条/题':<7}{'答对':<7}{'该答未答':<10}"
          f"{'编造':<7}{'准确率'}")
    print("-" * 80)

    for c in CONDS:
        af = os.path.join(TASK, f"{c}_answers.json")
        kf = os.path.join(TASK, f"{c}_key.json")
        if not os.path.exists(af):
            print(f"{c:<12}(缺答案)")
            continue
        ans = json.load(open(af, encoding="utf-8"))
        key = json.load(open(kf, encoding="utf-8"))
        amap = {r["qid"]: r.get("answer", "") for r in ans}

        right = wrong = abst = 0
        wrong_samples = []
        for qid, kv in key.items():
            got = amap.get(qid, "")
            if qid not in amap:
                wrong += 1
                continue
            gold = kv["answer"]
            gold_is_abstain = is_abstain(gold)
            got_is_abstain = is_abstain(got)
            # 关键修正：当标准答案本身就是"无法确定"时，答"无法确定"= 正确
            if gold_is_abstain:
                if got_is_abstain:
                    right += 1        # 正确地承认不知道
                else:
                    wrong += 1        # 编了一个答案
                    if len(wrong_samples) < 3:
                        wrong_samples.append((qid, gold, got))
            else:
                if got_is_abstain:
                    abst += 1         # 该答却没答
                elif norm(gold) in norm(got):
                    right += 1
                else:
                    wrong += 1
                    if len(wrong_samples) < 3:
                        wrong_samples.append((qid, gold, got))
        n = len(key)
        rep[c] = {"n": n, "right": right, "wrong": wrong, "abstain": abst,
                  "acc": round(right / n, 4)}
        print(f"{c:<12}{n:<7}{NDOC[c]:<7}{right:<7}{abst:<7}{wrong:<7}"
              f"{right/n:.4f}")
        if wrong_samples:
            print(f"    ⚠ 答错样例: {wrong_samples}")

    print("-" * 80)

    if all(c in rep for c in CONDS):
        h1 = rep["HIT_1"]["acc"]
        h10 = rep["HIT_10"]["acc"]
        m0 = rep["MISS_0"]["acc"]
        mn = rep["MISS_NEAR"]["acc"]

        print("\n结果解读：")
        print(f"  HIT_1     {h1:.4f}  目标在 1 条候选里")
        print(f"  HIT_10    {h10:.4f}  目标在 10 条干扰里")
        print(f"  MISS_0    {m0:.4f}  问了库里根本没有的实体 → 正确答案是'无法确定'")
        print(f"  MISS_NEAR {mn:.4f}  库里有近形名 → 正确答案是那个近形名")

        print("\n两个关键判读：")
        # 1. 不存在时会不会硬编
        if m0 >= 0.9:
            print(f"  ✔ 目标不存在时，模型会诚实说'无法确定'（{m0:.2f}）")
            print(f"    → 不编造的倾向强，这是好消息")
        else:
            print(f"  ✘ 目标不存在时，模型会强行作答（{m0:.2f}）")

        # 2. 近形陷阱：模型输出的是 context 里的名字，说明它做了"抄"而不是"判"
        print(f"\n  MISS_NEAR = {mn:.2f}")
        print(f"    注意：这里的'答对'是指输出与 context 里的近形名一致。")
        print(f"    它证明了模型会严格照抄 context 里的字面，")
        print(f"    但这不等于它能判断'这条记忆是不是我要找的那条'。")

        rep["verdict"] = "MISS_0 诚实弃答" if m0 >= 0.9 else "MISS_0 强行作答"
        rep["summary"] = {
            "hit_stable": h1 >= 0.95 and h10 >= 0.95,
            "abstains_when_absent": m0 >= 0.9,
            "copies_literal": mn >= 0.95}

    with open(os.path.join(RESULTS, "e57_hit_miss.json"), "w", encoding="utf-8") as f:
        json.dump({"experiment": "E-57", "report": rep}, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e57_hit_miss.json')}")


if __name__ == "__main__":
    main()

