"""
E-95 定义"规则拿不准"的边界，让模型只精筛边界样本

背景：
  两个剩余瓶颈（长而空识别、能力范围过拟合）都需要模型判断，
  但每条都调模型成本太高（×1000）。

  正确做法：规则先粗筛，只有"规则拿不准"的边界样本才交给模型精筛。

本实验回答一个前置问题：
  "规则拿不准"怎么定义？——用规则的置信度（命中判据的强弱）。

设计：
  给 identity 规则加置信度输出：
    high   = 明确命中强判据（专名/编号+绑定/一阶亲属/固定关系槽）
    low    = 命中弱判据（仅姓氏/小X/兜底）
    none   = 未命中（identity=False）

  然后看：low 置信度的样本，模型精筛能纠正多少？
  如果 low 样本很少且模型能纠正 → 精筛层成本可控、收益明确。
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
RESULTS = os.path.join(_HERE, "results")

from capability import (identity_can, _find_name_title, _find_xiao,
                        IDENTIFIER, BOUND, STABLE_SLOT, STABLE_STANDALONE,
                        FIRST_REL, SECOND_ORDER, POSITIONAL, INDEFINITE,
                        DEICTIC, PRECISE_COORD, SURNAME_ONLY)


def identity_conf(text: str) -> tuple[bool, str]:
    """返回 (判定, 置信度)：high/low/none"""
    t = text or ""

    # 强判据 → high
    if IDENTIFIER.search(t) and BOUND.search(t):
        return True, "high"
    if _find_name_title(t) or _find_xiao(t):
        return True, "high"
    if STABLE_SLOT.search(t) or STABLE_STANDALONE.search(t):
        return True, "high"
    if FIRST_REL.search(t):
        return True, "high"
    if SECOND_ORDER.search(t):
        return False, "high"
    if POSITIONAL.search(t):
        return False, "high"
    if INDEFINITE.search(t):
        return False, "high"

    # 弱判据 → low
    if SURNAME_ONLY.search(t):
        return True, "low"
    if PRECISE_COORD.search(t):
        return True, "low"
    if IDENTIFIER.search(t) and not BOUND.search(t):
        return False, "low"

    # 未命中 → none（identity=False）
    return False, "none"


def main():
    print("=" * 92)
    print("E-95 定义规则置信度，看边界样本规模")
    print("=" * 92)

    # 加载 44 条
    batches = [("E-81", os.path.join(_HERE, "work", "e81")),
               ("E-83", os.path.join(_HERE, "work", "e83"))]
    rows = []
    for label, d in batches:
        cp, vp = os.path.join(d, "corpus.json"), os.path.join(d, "verdict.json")
        if not (os.path.exists(cp) and os.path.exists(vp)):
            continue
        r = json.load(open(cp, encoding="utf-8"))
        raw = json.load(open(vp, encoding="utf-8"))
        gold = {x["rid"]: x for x in (raw if isinstance(raw, list)
                                      else raw.get("per_item", []))}
        for item in r:
            if item["rid"] in gold:
                rows.append({"rid": item["rid"], "text": item["memory"],
                             "gold": bool(gold[item["rid"]].get("identity"))})

    # 统计置信度分布
    from collections import Counter
    dist = Counter()
    low_correct, low_wrong = 0, 0
    low_items = []
    for r in rows:
        p, conf = identity_conf(r["text"])
        dist[conf] += 1
        if conf == "low":
            if p == r["gold"]:
                low_correct += 1
            else:
                low_wrong += 1
                low_items.append((r["rid"], r["text"], r["gold"], p))

    print(f"\n{len(rows)} 条，置信度分布：")
    for conf in ["high", "low", "none"]:
        print(f"  {conf:<6} {dist.get(conf, 0)} 条")

    print(f"\n低置信度（low）样本：{dist.get('low', 0)} 条")
    print(f"  规则判对 {low_correct} 条，判错 {low_wrong} 条")
    if low_wrong:
        print("\n  规则判错的 low 样本（这些正是模型精筛该纠正的）：")
        for rid, txt, g, p in low_items:
            print(f"    [{rid}] gold={g} 规则={p}  {txt}")

    # 结论：精筛层收益
    print("\n" + "=" * 92)
    print("结论：模型精筛的边界规模")
    print("=" * 92)
    n_high = dist.get("high", 0)
    n_low = dist.get("low", 0)
    print(f"  high 样本 {n_high} 条 → 规则直接采信，不调模型")
    print(f"  low 样本 {n_low} 条 → 交给模型精筛")
    print(f"  none 样本 {dist.get('none', 0)} 条 → identity=False，不调模型")
    print(f"\n  若模型精筛 low 样本：")
    print(f"    模型调用比例 = {n_low}/{len(rows)} = {n_low/len(rows)*100:.1f}%")
    print(f"    可纠正 {low_wrong} 条错误（若模型判断全对）")

    out = {"experiment": "E-95", "n": len(rows), "dist": dict(dist),
           "low_total": n_low, "low_wrong": low_wrong,
           "model_call_ratio": round(n_low / len(rows), 4)}
    with open(os.path.join(RESULTS, "e95_confidence.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e95_confidence.json')}")


if __name__ == "__main__":
    main()
