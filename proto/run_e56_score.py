"""
E-56 评分：干扰假说检验
"""
from __future__ import annotations

import json
import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
TASK = os.path.join(_HERE, "work", "e56_task")
RESULTS = os.path.join(_HERE, "results")

CONDS = ["N0", "S10", "D10", "S30"]
ABSTAIN = ["无法确定", "不确定", "不知道", "无法判断", "不清楚", "无相关", "没有相关"]


def norm(s):
    s = str(s).strip()
    s = re.sub(r"\s+", "", s)
    for ch in "，。：:、（）()":
        s = s.replace(ch, "")
    return s.lower()


def is_abstain(s):
    s = norm(s)
    if not s:
        return True
    return any(norm(a) in s for a in ABSTAIN)


def main():
    rep = {}
    print("=" * 88)
    print("E-56 干扰假说检验")
    print("=" * 88)
    print(f"{'条件':<12}{'题数':<8}{'条/题':<8}{'答对':<8}{'答错':<8}"
          f"{'弃答':<8}{'准确率'}")
    print("-" * 88)

    for c in CONDS:
        af = os.path.join(TASK, f"{c}_answers.json")
        kf = os.path.join(TASK, f"{c}_key.json")
        if not os.path.exists(af):
            print(f"{c:<12}（缺答案）")
            continue
        ans = json.load(open(af, encoding="utf-8"))
        key = json.load(open(kf, encoding="utf-8"))
        amap = {r["qid"]: r.get("answer", "") for r in ans}

        right = wrong = abst = 0
        for qid, kv in key.items():
            got = amap.get(qid, "")
            if qid not in amap:
                wrong += 1
            elif is_abstain(got):
                abst += 1
            elif norm(kv["answer"]) in norm(got):
                right += 1
            else:
                wrong += 1
        n = len(key)
        rep[c] = {"n": n, "right": right, "wrong": wrong, "abstain": abst,
                  "acc": round(right / n, 4), "abstain_rate": round(abst / n, 4)}
        ndoc = {"N0":1,"S10":10,"D10":10,"S30":20}[c]
        print(f"{c:<12}{n:<8}{ndoc:<8}{right:<8}{wrong:<8}{abst:<8}{right/n:.4f}")

    print("-" * 88)

    if all(c in rep for c in CONDS):
        a = rep["N0"]["acc"]
        b = rep["S10"]["acc"]
        cc = rep["D10"]["acc"]
        d = rep["S30"]["acc"]
        print("\n关键对比：")
        print(f"  N0     (1条)        : {a:.4f}   ← 天花板")
        print(f"  S10  (5条,同模板) : {b:.4f}   ({b-a:+.4f})")
        print(f"  D10  (5条,异模板) : {cc:.4f}   ({cc-a:+.4f})")
        print(f"  S30 (10条,同模板): {d:.4f}   ({d-a:+.4f})")

        print("\n判决：")
        # 干扰假说：SIM 明显差于 DIV
        diff_sim_div = cc - b
        print(f"  S10 vs D10 差值 = {diff_sim_div:+.4f}")
        if diff_sim_div > 0.10:
            v = "干扰是主因：同模板干扰显著更差，多样性重要"
        elif diff_sim_div > 0.03:
            v = "干扰有影响，但幅度中等"
        else:
            v = "干扰不是主因：相似度影响很小"
        print(f"  → {v}")

        # 容量假说：条数增加是否单调下降（控制相似度）
        print(f"\n  条数效应（同模板下）：1条 {a:.3f} → 5条 {b:.3f} → 10条 {d:.3f}")
        if d < b < a:
            print(f"  → 条数增加单调下降，容量/干扰效应累积")
        else:
            print(f"  → 条数效应非单调，需进一步查")

        rep["verdict"] = v
        rep["diff_sim_div"] = round(diff_sim_div, 4)

    with open(os.path.join(RESULTS, "e56_interference.json"), "w", encoding="utf-8") as f:
        json.dump({"experiment": "E-56", "report": rep}, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e56_interference.json')}")


if __name__ == "__main__":
    main()




