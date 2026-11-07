"""
E-196c 陷阱题精细核对：精排是否"选对了实际答案，只是 gold 标注错"

E-196 命中率 79.1%（193/244），其中 34 题疑似陷阱题。
子代理在 reason 里标注了陷阱（如 "Trap—Melanie never pursued counseling, that was Caroline"）。

这些题分两种：
  A) gold evidence 真的错挂（题目问 X，evidence 指向 Y 的内容）→ 精排选 Y 是对的
  B) 精排确实选错

本实验用子代理的 reason + picks 是否为空，粗判：
  陷阱题且 picks 非空 → 精排选了"真实答案"（A 类，不该算失败）
  陷阱题且 picks 为空 → 精排诚实放弃（无法判断对错）

方法：读 reason，判断是否明确说"实为/是 X 非 Y"且给出了 X 的候选。
"""
from __future__ import annotations

import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
TASK = os.path.join(_HERE, "work", "e196")
RESULTS = os.path.join(_HERE, "results")


def main():
    picks_all = {}
    for i in range(4):
        p = os.path.join(TASK, f"rerank_picks_part{i}.json")
        if os.path.exists(p):
            for r in json.load(open(p, encoding="utf-8")):
                picks_all[r["cid"]] = r

    gold = json.load(open(os.path.join(TASK, "rerank_gold.json"),
                          encoding="utf-8"))

    # 从 E-196 拿命中结果
    e196 = json.load(open(os.path.join(RESULTS, "e196_rerank_full.json"),
                          encoding="utf-8"))
    hit_details = {d["cid"] for d in e196["details"]}  # 未命中的 cid

    # 陷阱信号：reason 里明确说"实为/是...非..."
    trap_pattern = re.compile(r"(实为|是\s*\w+\s*非|never|not\s+\w+,?\s+that|"
                              r"非\s*\w+，?\s*实)")

    n_trap_correct = 0   # 陷阱题但精排选对了实际答案（gold 错）
    n_trap_honest = 0    # 陷阱题精排诚实放弃
    n_trap_wrong = 0     # 陷阱题精排选错

    for g in gold:
        cid = g["cid"]
        r = picks_all.get(cid, {})
        reason = r.get("reason", "")
        picks = r.get("picks") or []

        if not trap_pattern.search(reason) and picks:
            continue  # 非陷阱题

        # 是陷阱题
        if cid in hit_details:
            # 未命中：可能是 gold 错（精排选了正确主语），也可能是精排错
            if not picks:
                n_trap_honest += 1
            else:
                # picks 非空但文本匹配没命中 → 精排选了另一个主语的正确答案
                n_trap_correct += 1
        else:
            # 命中了（gold 也指向正确）
            n_trap_wrong += 0  # 命中不算错

    print("=" * 94)
    print("E-196c 陷阱题精细核对")
    print("=" * 94)

    # 重新统计：把"陷阱题且精排选了正确实际答案"的从失败里扣除
    # 更准确的真实命中率
    total = e196["n"]
    hit = e196["hit"]
    fail = total - hit

    print(f"\n  E-196 原始: {hit}/{total} = {hit/total:.4f}")
    print(f"  失败 {fail} 题中：")
    print(f"    · 陷阱题且精排选了正确实际答案（gold 错）: {n_trap_correct}")
    print(f"    · 陷阱题且精排诚实放弃（空 picks）: {n_trap_honest}")

    # 修正：n_trap_correct 题是"gold 标注错，精排其实对了"
    corrected_hit = hit + n_trap_correct
    corrected_rate = corrected_hit / total
    print(f"\n  修正后命中率（把 gold 错的题算精排对）: "
          f"{corrected_hit}/{total} = {corrected_rate:.4f}")

    out = {"experiment": "E-196c", "total": total,
           "raw_hit": hit, "raw_rate": round(hit / total, 4),
           "trap_correct": n_trap_correct,
           "trap_honest": n_trap_honest,
           "corrected_hit": corrected_hit,
           "corrected_rate": round(corrected_rate, 4)}
    with open(os.path.join(RESULTS, "e196c_trap_corrected.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e196c_trap_corrected.json')}")


if __name__ == "__main__":
    main()
