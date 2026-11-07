"""
E-97 模型精筛的触发条件：何时该调模型，而不是规则

E-96 结论：
  模型判断"长而空" F1=1.0（8/8），规则 TP=0（完全失效）
  → vague_summary 必须模型精筛

但关键成本问题：不能每条都调模型。
需要定义"什么时候该调模型"——即规则的触发条件。

设计（两级）：
  第 1 级：规则粗筛，只对"可能长而空"的候选调模型
    触发条件：句长 > 40 字 且 无具体数字/专名/日期
    （这是"可能有信息密度问题"的必要条件，不充分）
  第 2 级：模型精筛，只对第 1 级命中的样本判断

验证：第 1 级的触发条件，能否把"必须调模型"的样本收敛到最小，
同时不漏掉真正的"长而空"。
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
RESULTS = os.path.join(_HERE, "results")
TASK = os.path.join(_HERE, "work", "e96")

import re

# 第 1 级触发条件：长 + 无具体信息锚点
def should_call_model(text: str) -> bool:
    t = text or ""
    if len(t) <= 40:
        return False
    # 有具体数字/日期/专名 → 信息密度可能没问题，不调模型
    if re.search(r"\d{2,}|\d{1,2}月\d{1,2}|\d{4}[-/年]", t):
        return False
    # 有具体地点/机构/人名 → 不调
    if re.search(r"([\u4e00-\u9fa5]{2,6}(?:路|街|巷|道)|医院|学校|公司|幼儿园|"
                 r"[A-Z][a-z]+)", t):
        return False
    return True


def main():
    # 用 E-96 的 8 条 + 之前 E-73 的 21 条一起测触发条件
    rows = json.load(open(os.path.join(TASK, "corpus.json"), encoding="utf-8"))
    # 补充 E-73 的语料（长句 R20 等）
    e73 = json.load(open(os.path.join(_HERE, "work", "e73", "corpus.json"),
                         encoding="utf-8"))

    all_rows = []
    for r in rows:
        all_rows.append({"rid": r["rid"], "text": r["memory"],
                         "is_empty": r["kind"] == "empty"})
    # E-73 里 R20 是长而空
    for r in e73:
        if r["rid"] == "R20":
            all_rows.append({"rid": "R20", "text": r["memory"],
                             "is_empty": True})
        else:
            all_rows.append({"rid": r["rid"], "text": r["memory"],
                             "is_empty": False})

    print("=" * 92)
    print("E-97 模型精筛的触发条件")
    print("=" * 92)
    print(f"\n{len(all_rows)} 条（E-96 的 8 + E-73 的 21）\n")

    # 统计：触发调模型的样本
    call = [r for r in all_rows if should_call_model(r["text"])]
    not_call = [r for r in all_rows if not should_call_model(r["text"])]

    print(f"触发调模型：{len(call)} 条（占 {len(call)/len(all_rows)*100:.0f}%）")
    print(f"不调模型：{len(not_call)} 条\n")

    # 检查：触发样本里，真正的"长而空"有多少；漏掉的"长而空"有多少
    tp = sum(1 for r in call if r["is_empty"])
    fp = sum(1 for r in call if not r["is_empty"])
    fn = sum(1 for r in not_call if r["is_empty"])
    tn = sum(1 for r in not_call if not r["is_empty"])

    print("-" * 92)
    print(f"{'结果':<20}{'数量'}")
    print("-" * 92)
    print(f"{'触发且真是长空(TP)':<20}{tp}")
    print(f"{'触发但非长空(FP)':<20}{fp}")
    print(f"{'未触发但是长空(FN)':<20}{fn}")
    print(f"{'未触发且非长空(TN)':<20}{tn}")
    print("-" * 92)

    recall = tp / (tp + fn) if (tp + fn) else 0
    print(f"\n  召回（真长空被触发）: {recall:.4f}")
    print(f"  模型调用比例: {len(call)/len(all_rows)*100:.0f}%")

    if fn > 0:
        print("\n  漏掉的长空样本（触发条件太严）：")
        for r in not_call:
            if r["is_empty"]:
                print(f"    [{r['rid']}] {r['text'][:40]}")
    if fp > 0:
        print("\n  误触发的样本（触发条件太松，多调了模型）：")
        for r in call:
            if not r["is_empty"]:
                print(f"    [{r['rid']}] {r['text'][:40]}")

    out = {"experiment": "E-97", "n": len(all_rows),
           "call": len(call), "not_call": len(not_call),
           "tp": tp, "fp": fp, "fn": fn, "tn": tn,
           "recall": round(recall, 4),
           "model_call_ratio": round(len(call) / len(all_rows), 4)}
    with open(os.path.join(RESULTS, "e97_trigger.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e97_trigger.json')}")


if __name__ == "__main__":
    main()
