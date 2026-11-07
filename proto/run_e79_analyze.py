"""
E-79 分析：规则推断 vs 模型标注（全新语料交叉验证）
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
TASK = os.path.join(_HERE, "work", "e79")
RESULTS = os.path.join(_HERE, "results")
DIMS = ["existence", "identity", "time_exact", "time_range", "value", "location"]


def main():
    rows = json.load(open(os.path.join(TASK, "corpus.json"), encoding="utf-8"))
    sp = os.path.join(TASK, "scope.json")
    if not os.path.exists(sp):
        print("缺少 scope.json")
        return
    model = {r["rid"]: r for r in json.load(open(sp, encoding="utf-8"))}

    print("=" * 96)
    print("E-79 交叉验证：规则推断 vs 模型标注（全新语料）")
    print("=" * 96)

    agree = total = 0
    diffs = []
    dim_stat = Counter()

    print("\n" + "-" * 96)
    print(f"{'rid':<6}{'维度':<13}{'规则':<7}{'模型':<7}{'一致':<7}{'记忆'}")
    print("-" * 96)

    for r in rows:
        rid = r["rid"]
        if rid not in model:
            continue
        rule = r["rule_scope"]
        mdl = model[rid].get("scope", {})
        row_diff = []
        for d in DIMS:
            a = bool(rule.get(d))
            b = bool(mdl.get(d))
            total += 1
            if a == b:
                agree += 1
            else:
                dim_stat[d] += 1
                row_diff.append((d, a, b))
                print(f"{rid:<6}{d:<13}{str(a):<7}{str(b):<7}✗      {r['memory'][:30]}")
        if row_diff:
            diffs.append((rid, r["memory"], row_diff, model[rid].get("note", "")))
    print("-" * 96)

    print(f"\n  维度级一致率: {agree}/{total} = {agree/total:.4f}")
    print(f"  有分歧的记忆: {len(diffs)}/{len(rows)}")

    if diffs:
        print("\n" + "=" * 96)
        print("分歧明细")
        print("=" * 96)
        for rid, txt, ds, note in diffs:
            print(f"\n  [{rid}] {txt}")
            for d, a, b in ds:
                direction = "规则判✓模型判·" if a else "规则判·模型判✓"
                print(f"    {d:<13}{direction}")
            print(f"    模型说明: {note}")

    print("\n" + "=" * 96)
    print("按维度统计分歧（哪个维度规则最不准）")
    print("=" * 96)
    if dim_stat:
        for d, n in dim_stat.most_common():
            print(f"  {d:<14}{n} 次")
    else:
        print("  （无分歧）")

    out = {"experiment": "E-79", "n_memories": len(rows),
           "dim_total": total, "dim_agree": agree,
           "dim_agree_rate": round(agree / total, 4),
           "n_diff_memories": len(diffs),
           "dim_conflict": dict(dim_stat),
           "diffs": [{"rid": r, "text": t,
                      "conflicts": [{"dim": d, "rule": a, "model": b}
                                    for d, a, b in ds],
                      "model_note": n} for r, t, ds, n in diffs]}
    with open(os.path.join(RESULTS, "e79_crossval.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e79_crossval.json')}")


if __name__ == "__main__":
    main()
