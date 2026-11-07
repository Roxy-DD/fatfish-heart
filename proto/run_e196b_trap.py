"""
E-196b 分离"陷阱题"：evidence 标注错误的题占多少

E-196 发现：精排未命中的题里，大量是"题干主语与 evidence 内容不符"的陷阱题。
这些题 evidence 标注本身可能错了，不该算精排失败。

方法：
  ① 统计 4 个子代理的 reason 里提到"陷阱/张冠李戴/主语不符"的题
  ② 这些题里，精排空 picks 或选错的比例
  ③ 扣除陷阱题后，精排的真实命中率

关键：子代理在 reason 里已经标注了陷阱题（如 part1 报告的 R087/R112 等）。
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
    # 合并 picks（含 reason）
    picks_all = {}
    for i in range(4):
        p = os.path.join(TASK, f"rerank_picks_part{i}.json")
        if os.path.exists(p):
            for r in json.load(open(p, encoding="utf-8")):
                picks_all[r["cid"]] = r

    gold = json.load(open(os.path.join(TASK, "rerank_gold.json"),
                          encoding="utf-8"))

    # 陷阱信号词
    trap_kw = ["非", "不是", "张冠李戴", "实为", "另一人", "另一", "无",
               "未提供", "未给出", "无明确", "无具体", "误", "错", "空",
               "疑", "无信息"]

    n_trap = 0
    n_trap_empty = 0
    trap_cids = []
    reasons = []

    for g in gold:
        cid = g["cid"]
        r = picks_all.get(cid, {})
        reason = r.get("reason", "")
        picks = r.get("picks") or []

        # 判断是否陷阱题（reason 含陷阱信号 且 picks 空）
        is_trap = (not picks) or any(k in reason for k in trap_kw)
        if is_trap:
            n_trap += 1
            if not picks:
                n_trap_empty += 1
                trap_cids.append(cid)
            reasons.append((cid, g["question"][:40], reason[:60]))

    print("=" * 94)
    print("E-196b 陷阱题分离")
    print("=" * 94)
    print(f"\n  总题数: {len(gold)}")
    print(f"  疑似陷阱题（空 picks 或 reason 含陷阱信号）: {n_trap}")
    print(f"  其中空 picks: {n_trap_empty}")

    print(f"\n  陷阱题案例（前 15）:")
    for cid, q, reason in reasons[:15]:
        print(f"    [{cid}] {q}")
        print(f"         reason: {reason}")

    # 扣除陷阱题后的命中率（E-196 的 hit=193 里已含陷阱题影响）
    # 这里重新算：如果陷阱题的"未命中"是证据标注错，则真实精排能力更高
    print(f"\n" + "=" * 94)
    print("结论")
    print("=" * 94)

    print(f"""
  全量 244 题，命中 193（79.1%）
  疑似陷阱题 {n_trap} 题（evidence 标注可能有问题）

  如果陷阱题是数据问题（非精排能力），则：
    可评测题 = 244 - {n_trap} = {244 - n_trap}
    陷阱题中精排"选对"的（实际 evidence 也指向正确）需另判

  结论：需要人工/更精细地判断陷阱题中 evidence 是否真的错，
        不能简单假设"陷阱题都是数据问题"。
""")

    out = {"experiment": "E-196b", "total": len(gold),
           "n_trap": n_trap, "n_trap_empty": n_trap_empty,
           "trap_cids": trap_cids, "reasons": reasons[:30]}
    with open(os.path.join(RESULTS, "e196b_trap.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"写入 {os.path.join(RESULTS, 'e196b_trap.json')}")


if __name__ == "__main__":
    main()
