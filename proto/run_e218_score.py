"""
E-218 评分：多跳题端到端准确率（LLM 判等）

多跳题答案开放（"Likely no" / "No, adopting"），需语义等价判断。
用字符串归一化 + 关键词覆盖做初步判断，人工抽查。

先跑字符串匹配，若太严再用 LLM 判等。
"""
from __future__ import annotations
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
TASK = os.path.join(_HERE, "work", "e218")
RESULTS = os.path.join(_HERE, "results")


def norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", str(s or "").lower())).strip()


def is_correct(pred, gold):
    p, g = norm(pred), norm(gold)
    if not p or not g or p == "i don't know":
        return False
    if p == g:
        return True
    # 包含
    if g in p or p in g:
        return len(g) >= 3 or len(p) >= 3
    # 关键词覆盖
    gwords = [w for w in g.split() if len(w) >= 3]
    if gwords:
        cov = sum(1 for w in gwords if w in p)
        if cov / len(gwords) >= 0.5:
            return True
    return False


def main():
    tasks = json.load(open(os.path.join(TASK, "qa_gold.json"), encoding="utf-8"))
    ap = os.path.join(TASK, "qa_answers.json")
    if not os.path.exists(ap):
        print("缺少 qa_answers.json")
        return
    answers = {r["cid"]: r for r in json.load(open(ap, encoding="utf-8"))}

    print("=" * 94)
    print("E-218 多跳题端到端准确率（字符串近似判等）")
    print("=" * 94)

    hit = 0
    n = 0
    details = []

    for t in tasks:
        cid = t["cid"]
        pred = answers.get(cid, {}).get("answer", "")
        gold = t["answer"]
        ok = is_correct(pred, gold)
        n += 1
        hit += 1 if ok else 0
        if not ok:
            details.append({"cid": cid, "q": t["question"][:45],
                            "gold": str(gold)[:50], "pred": str(pred)[:50]})

    rate = hit / max(1, n)
    print(f"\n  端到端准确率: {hit}/{n} = {rate:.4f}")

    print(f"\n  未判对案例（前 15）:")
    for d in details[:15]:
        print(f"    [{d['cid']}] {d['q']}")
        print(f"      gold: {d['gold']}")
        print(f"      pred: {d['pred']}")

    # 对比：纯检索证据命中 54.3%，端到端作答准确率应该是另一个数
    print(f"\n  参考：改写检索证据命中率 0.5435（E-215）")
    print(f"  本实验端到端准确率 {rate:.4f}（检索素材 + LLM 推理作答）")

    out = {"experiment": "E-218", "n": n, "hit": hit,
           "rate": round(rate, 4),
           "evidence_recall": 0.5435,
           "details": details}
    with open(os.path.join(RESULTS, "e218_multihop_qa.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e218_multihop_qa.json')}")


if __name__ == "__main__":
    main()
