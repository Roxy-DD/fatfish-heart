"""
E-76 分析：规则闸 vs 模型自判的分歧，能否归纳出第 8 类规则
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e76")
RESULTS = os.path.join(_HERE, "results")


def main():
    rows = json.load(open(os.path.join(TASK, "corpus.json"), encoding="utf-8"))
    jp = os.path.join(TASK, "judge.json")
    if not os.path.exists(jp):
        print("缺少 judge.json")
        return
    judge = {r["rid"]: r for r in json.load(open(jp, encoding="utf-8"))}

    by = {r["rid"]: r for r in rows}
    common = [rid for rid in by if rid in judge]

    print("=" * 96)
    print("E-76 规则闸 vs 模型自判")
    print("=" * 96)
    print(f"\n可比对 {len(common)} 条\n")

    tp = fp = tn = fn = 0
    fp_items, fn_items = [], []

    for rid in common:
        r = by[rid]
        rule_bad = bool(r["rule_says_bad"])
        model_ok = bool(judge[rid].get("ok"))
        model_bad = not model_ok
        if rule_bad and model_bad:
            tp += 1
        elif rule_bad and model_ok:
            fp += 1
            fp_items.append((rid, r["memory"], r["rule_issues"],
                             judge[rid].get("reason", "")))
        elif (not rule_bad) and model_bad:
            fn += 1
            fn_items.append((rid, r["memory"], judge[rid].get("reason", "")))
        else:
            tn += 1

    n = len(common)
    acc = (tp + tn) / n
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    agree = (tp + tn) / n

    print("-" * 96)
    print(f"{'':<20}{'模型:有问题':<14}{'模型:没问题':<14}{'合计'}")
    print("-" * 96)
    print(f"{'规则:有问题':<20}{tp:<14}{fp:<14}{tp+fp}")
    print(f"{'规则:没问题':<20}{fn:<14}{tn:<14}{fn+tn}")
    print(f"{'合计':<20}{tp+fn:<14}{fp+tn:<14}{n}")
    print("-" * 96)
    print(f"\n  一致率 {agree:.4f}")
    print(f"  准确率 {acc:.4f}  精确率 {prec:.4f}  召回 {rec:.4f}  F1 {f1:.4f}")

    # A 类分歧：规则报警但模型说能答（误报）
    print("\n" + "=" * 96)
    print(f"A 类分歧：规则报警，模型说能答（误报 {len(fp_items)} 条）")
    print("=" * 96)
    for rid, txt, iss, why in fp_items:
        print(f"  [{rid}] 规则判={iss}")
        print(f"    记忆: {txt[:56]}")
        print(f"    模型理由: {why}")

    # B 类分歧：规则没报警但模型说答不出（漏报）
    print("\n" + "=" * 96)
    print(f"B 类分歧：规则放过，模型说答不出（漏报 {len(fn_items)} 条）")
    print("=" * 96)
    for rid, txt, why in fn_items:
        print(f"  [{rid}]")
        print(f"    记忆: {txt[:56]}")
        print(f"    模型理由: {why}")

    # 分析 B 类共同特征
    print("\n" + "=" * 96)
    print("B 类分歧的共同特征（这就是第 8 类规则的线索）")
    print("=" * 96)
    import re
    for rid, txt, why in fn_items:
        feats = []
        if re.search(r"(那天|上次|那次|那时候|当时|大概)", txt):
            feats.append("相对时间")
        if re.search(r"(这个|那个|那边|那里)", txt):
            feats.append("代词")
        if len(txt) > 45:
            feats.append("长句")
        if re.search(r"(还行|不错|可以|应该|可能|差不多|整体)", txt):
            feats.append("评价词")
        if not re.search(r"\d", txt):
            feats.append("无数字")
        print(f"  [{rid}] 特征: {feats or ['（无明显特征）']}")
        print(f"    {txt[:60]}")

    out = {"experiment": "E-76", "n": n,
           "tp": tp, "fp": fp, "tn": tn, "fn": fn,
           "agree_rate": round(agree, 4), "accuracy": round(acc, 4),
           "precision": round(prec, 4), "recall": round(rec, 4),
           "f1": round(f1, 4),
           "false_pos": [{"rid": r, "text": t, "issues": i, "model_reason": w}
                         for r, t, i, w in fp_items],
           "false_neg": [{"rid": r, "text": t, "model_reason": w}
                         for r, t, w in fn_items]}
    with open(os.path.join(RESULTS, "e76_divergence.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e76_divergence.json')}")


if __name__ == "__main__":
    main()
