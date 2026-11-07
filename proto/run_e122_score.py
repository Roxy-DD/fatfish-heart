"""
E-122 评测：三种职责划分方案对比

G1 硬编码词表（系统做判断，但靠规则词表）
G2 纯余弦阈值（系统做判断，靠阈值）
G3 系统提取 + 模型判断（用户的原则）

指标：关系判定准确率 + 目标选择准确率
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
TASK = os.path.join(_HERE, "work", "e122")
RESULTS = os.path.join(_HERE, "results")


def main():
    gold = {r["cid"].replace("C", "S"): r for r in
            json.load(open(os.path.join(TASK, "gold.json"), encoding="utf-8"))}
    pack = {r["cid"]: r for r in
            json.load(open(os.path.join(TASK, "pack.json"), encoding="utf-8"))}

    mp = os.path.join(TASK, "model.json")
    if not os.path.exists(mp):
        print("缺少 model.json")
        return
    model = {r["cid"]: r for r in json.load(open(mp, encoding="utf-8"))}

    print("=" * 92)
    print("E-122 职责划分对比：G1 词表 / G2 阈值 / G3 模型判断")
    print("=" * 92)

    print("\n" + "-" * 92)
    print(f"{'cid':<6}{'gold':<12}{'模型判':<12}{'关系对?':<9}"
          f"{'模型选目标':<26}{'目标对?'}")
    print("-" * 92)

    rel_hit = 0
    tgt_hit = 0
    tgt_total = 0
    details = []

    for cid in sorted(gold):
        g = gold[cid]
        m = model.get(cid, {})
        gold_rel = g["gold"]
        pred_rel = m.get("relation", "?")
        pred_tgt = m.get("target")
        gold_tgt = g["target"]

        rel_ok = (pred_rel == gold_rel)

        # 目标正确性：none 类不该选目标；其他类应选中 gold target
        if gold_rel == "none":
            tgt_ok = (pred_tgt is None)
        else:
            tgt_ok = (pred_tgt == gold_tgt)
            tgt_total += 1

        rel_hit += 1 if rel_ok else 0
        tgt_hit += 1 if tgt_ok else 0

        tgt_show = (pred_tgt[:22] if pred_tgt else "（无目标）")
        print(f"{cid:<6}{gold_rel:<12}{pred_rel:<12}"
              f"{'✓' if rel_ok else '✗':<9}{tgt_show:<26}"
              f"{'✓' if tgt_ok else '✗'}")
        details.append({
            "cid": cid, "gold": gold_rel, "pred": pred_rel,
            "rel_ok": rel_ok,
            "gold_target": gold_tgt, "pred_target": pred_tgt,
            "tgt_ok": tgt_ok,
            "reason": m.get("reason", ""),
        })
    print("-" * 92)

    n = len(gold)
    print(f"\n  关系判定: {rel_hit}/{n} = {rel_hit/n:.4f}")
    print(f"  目标选择: {tgt_hit}/{n} = {tgt_hit/n:.4f}"
          f"（其中需选目标 {tgt_total} 组）")

    # 与基线对比
    print("\n" + "=" * 92)
    print("三方案对比")
    print("=" * 92)
    print(f"{'方案':<28}{'关系准确率':<14}{'说明'}")
    print("-" * 92)
    print(f"{'G1 硬编码词表':<28}{'12/14 = 0.8571':<14}系统用规则词表做判断")
    print(f"{'G2 纯余弦+硬阈值':<28}{'13/14 = 0.9286':<14}系统用阈值做判断")
    print(f"{'G3 系统提取+模型判断':<28}{f'{rel_hit}/{n} = {rel_hit/n:.4f}':<14}系统只做机械提取，判断交给模型")
    print("-" * 92)

    # 失败分析
    fails = [d for d in details if not d["rel_ok"] or not d["tgt_ok"]]
    if fails:
        print("\n模型判错的案例：")
        for d in fails:
            print(f"\n  [{d['cid']}] 人工={d['gold']}  模型={d['pred']}")
            print(f"    正确目标: {d['gold_target'][:40]}")
            print(f"    模型选:   {(d['pred_target'] or '（无）')[:40]}")
            print(f"    模型理由: {d['reason'][:80]}")
    else:
        print("\n  模型 14/14 全对")

    out = {"experiment": "E-122", "n": n,
           "g1_rule": 0.8571, "g2_threshold": 0.9286,
           "g3_model": round(rel_hit / n, 4),
           "target_accuracy": round(tgt_hit / n, 4),
           "details": details}
    with open(os.path.join(RESULTS, "e122_division.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e122_division.json')}")


if __name__ == "__main__":
    main()
