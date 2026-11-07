"""
E-96 第 2 步：模型判断 vs 规则判断（信息密度）

看模型能否可靠判断"长而空"，若可靠，则 vague_summary 改用模型精筛。
"""
from __future__ import annotations

import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
TASK = os.path.join(_HERE, "work", "e96")
RESULTS = os.path.join(_HERE, "results")


def main():
    rows = json.load(open(os.path.join(TASK, "corpus.json"), encoding="utf-8"))
    jp = os.path.join(TASK, "judge.json")
    if not os.path.exists(jp):
        print("缺少 judge.json")
        return
    judge = {r["rid"]: r for r in json.load(open(jp, encoding="utf-8"))}

    tp = fp = tn = fn = 0
    print("=" * 88)
    print("E-96 模型判断 vs 规则判断（信息密度）")
    print("=" * 88)
    print("-" * 88)
    print(f"{'rid':<6}{'真实':<8}{'模型判vague':<14}{'规则判vague':<14}{'一致':<6}{'内容'}")
    print("-" * 88)

    sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
    from capability import _chk_vague_summary

    for r in rows:
        rid = r["rid"]
        gold = (r["kind"] == "empty")
        m = bool(judge.get(rid, {}).get("vague"))
        rule = _chk_vague_summary(r["memory"])

        # 模型判断
        if gold and m: tp += 1
        elif gold and not m: fn += 1
        elif (not gold) and m: fp += 1
        else: tn += 1

        rule_mark = "✓" if rule else "·"
        m_mark = "✓" if m else "·"
        same = "✓" if m == gold else "✗"
        print(f"{rid:<6}{str(gold):<8}{m_mark:<14}{rule_mark:<14}"
              f"{same:<6}{r['memory'][:30]}")

    n = len(rows)
    acc = (tp + tn) / n
    prec = tp / (tp + fp) if (tp + fp) else 0
    rec = tp / (tp + fn) if (tp + fn) else 0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0

    print("-" * 88)
    print(f"\n模型判断：TP={tp} FP={fp} TN={tn} FN={fn}")
    print(f"  准确率 {acc:.4f} 精确率 {prec:.4f} 召回 {rec:.4f} F1 {f1:.4f}")

    print(f"\n规则判断：TP={sum(1 for r in rows if r['kind']=='empty' and _chk_vague_summary(r['memory']))} "
          f"（E-96 已证规则 TP=0，完全失效）")

    print("\n结论：")
    if f1 >= 0.9:
        print(f"  ✅ 模型判断信息密度可靠（F1={f1:.3f}），vague_summary 改用模型精筛")
    else:
        print(f"  ⚠️ 模型判断 F1={f1:.3f}，需再调 prompt")

    out = {"experiment": "E-96-model", "n": n,
           "tp": tp, "fp": fp, "tn": tn, "fn": fn,
           "accuracy": round(acc, 4), "precision": round(prec, 4),
           "recall": round(rec, 4), "f1": round(f1, 4)}
    with open(os.path.join(RESULTS, "e96_model_judge.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e96_model_judge.json')}")


import sys
if __name__ == "__main__":
    main()
