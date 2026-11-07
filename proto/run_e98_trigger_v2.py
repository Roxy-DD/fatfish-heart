"""
E-98 修正触发条件：真正的判据是"空"，不是"长"

E-97 发现：
  E03（34字）、E02（38字）也"空"，但被 40 字阈值漏掉。
  → "空泛"与长度弱相关，本质是"信息密度低"。

真正的判据应该是：
  1. 无具体锚点（数字/日期/地名/人名/机构）
  2. 含模糊表态词（还行/再讨论/到时候/再想想/再看看/先做着/可能）

重新设计触发条件：不再要求"长"，只要"无锚点 + 有模糊表态"就触发模型精筛。
"""
from __future__ import annotations

import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(_HERE, "results")
TASK = os.path.join(_HERE, "work", "e96")

# 模糊表态词（E-98 扩充）
VAGUE = ["还行", "再讨论", "再想想", "再看看", "到时候", "再说", "可能",
         "差不多", "看情况", "先做着", "再说吧", "需要再", "反正", "总体上",
         "觉得可以", "还没想清楚", "完善一下", "进一步", "回头"]


def should_call_model(text: str) -> bool:
    t = text or ""
    # 有具体锚点 → 信息密度可能没问题，不调
    if re.search(r"\d{2,}|\d{1,2}月\d{1,2}|\d{4}[-/年]", t):
        return False
    if re.search(r"([\u4e00-\u9fa5]{2,6}(?:路|街|巷|道)|医院|学校|公司|幼儿园|"
                 r"[A-Z][a-z]+)", t):
        return False
    # 有模糊表态词 → 触发模型精筛
    return any(w in t for w in VAGUE)


def main():
    rows = json.load(open(os.path.join(TASK, "corpus.json"), encoding="utf-8"))
    e73 = json.load(open(os.path.join(_HERE, "work", "e73", "corpus.json"),
                         encoding="utf-8"))

    all_rows = []
    for r in rows:
        all_rows.append({"rid": r["rid"], "text": r["memory"],
                         "is_empty": r["kind"] == "empty"})
    for r in e73:
        all_rows.append({"rid": r["rid"], "text": r["memory"],
                         "is_empty": (r["rid"] == "R20")})

    print("=" * 92)
    print("E-98 修正触发条件：无锚点 + 有模糊表态")
    print("=" * 92)
    print(f"\n{len(all_rows)} 条\n")

    call = [r for r in all_rows if should_call_model(r["text"])]
    not_call = [r for r in all_rows if not should_call_model(r["text"])]

    tp = sum(1 for r in call if r["is_empty"])
    fp = sum(1 for r in call if not r["is_empty"])
    fn = sum(1 for r in not_call if r["is_empty"])
    tn = sum(1 for r in not_call if not r["is_empty"])

    print("-" * 92)
    print(f"{'结果':<24}{'数量'}")
    print("-" * 92)
    print(f"{'触发且真是空泛(TP)':<24}{tp}")
    print(f"{'触发但非空泛(FP)':<24}{fp}")
    print(f"{'未触发但是空泛(FN)':<24}{fn}")
    print(f"{'未触发且非空泛(TN)':<24}{tn}")
    print("-" * 92)

    recall = tp / (tp + fn) if (tp + fn) else 0
    precision = tp / (tp + fp) if (tp + fp) else 0
    print(f"\n  召回 {recall:.4f}  精确率 {precision:.4f}")
    print(f"  模型调用比例 {len(call)/len(all_rows)*100:.0f}%")

    if fn:
        print("\n  漏掉：")
        for r in not_call:
            if r["is_empty"]:
                print(f"    [{r['rid']}] {r['text'][:44]}")
    if fp:
        print("\n  误触发（多调了模型，但不影响正确性，只影响成本）：")
        for r in call:
            if not r["is_empty"]:
                print(f"    [{r['rid']}] {r['text'][:44]}")

    # 对比 E-97
    print(f"\n对比：")
    print(f"  E-97（长度>40 触发）: 召回 0.6000，调用 10%")
    print(f"  E-98（无锚点+模糊词）: 召回 {recall:.4f}，调用 {len(call)/len(all_rows)*100:.0f}%")

    out = {"experiment": "E-98", "n": len(all_rows),
           "tp": tp, "fp": fp, "fn": fn, "tn": tn,
           "recall": round(recall, 4), "precision": round(precision, 4),
           "model_call_ratio": round(len(call) / len(all_rows), 4)}
    with open(os.path.join(RESULTS, "e98_trigger_v2.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e98_trigger_v2.json')}")


if __name__ == "__main__":
    main()
