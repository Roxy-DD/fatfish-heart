"""
E-77 分析：模型标注的能力范围 vs 我的标注，一致吗

若一致 → "能力范围"这个替代方案成立，可以替换掉"完整度"标签
若不一致 → 看分歧在哪，说明双方对"能答"的定义还不一样
"""
from __future__ import annotations

import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
TASK = os.path.join(_HERE, "work", "e77")
RESULTS = os.path.join(_HERE, "results")


def main():
    rows = json.load(open(os.path.join(TASK, "corpus.json"), encoding="utf-8"))
    sp = os.path.join(TASK, "scope.json")
    if not os.path.exists(sp):
        print("缺少 scope.json")
        return
    scope = {r["rid"]: r for r in json.load(open(sp, encoding="utf-8"))}
    by = {r["rid"]: r for r in rows}

    DIMS = ["existence", "identity", "time_exact", "time_range", "value", "location"]
    print("=" * 96)
    print("E-77 能力范围：模型标注 vs 我的标注")
    print("=" * 96)
    print(f"\n{len(scope)} 条\n")

    print("-" * 96)
    print(f"{'rid':<7}{'维度':<14}{'我':<8}{'模型':<8}{'一致'}")
    print("-" * 96)

    agree = total = 0
    diff_items = []
    for rid in sorted(by):
        if rid not in scope:
            continue
        mine = set(by[rid]["expect_can"])
        theirs = set(scope[rid].get("can", []))
        row_diffs = []
        for d in DIMS:
            a = d in mine
            b = d in theirs
            total += 1
            same = (a == b)
            agree += 1 if same else 0
            if not same:
                row_diffs.append((d, a, b))
                print(f"{rid:<7}{d:<14}{str(a):<8}{str(b):<8}✗")
        if row_diffs:
            diff_items.append((rid, by[rid]["memory"], row_diffs,
                               scope[rid].get("note", "")))
    print("-" * 96)
    print(f"\n维度级一致率: {agree}/{total} = {agree/total:.4f}")

    if diff_items:
        print("\n" + "=" * 96)
        print(f"分歧明细（{len(diff_items)} 条记忆有分歧）")
        print("=" * 96)
        for rid, txt, diffs, note in diff_items:
            print(f"\n  [{rid}] {txt[:56]}")
            for d, a, b in diffs:
                who = "我判能答，模型判不能" if a else "我判不能，模型判能答"
                print(f"    {d:<12}{who}")
            print(f"    模型说明: {note}")

    # 维度统计：哪个维度最容易分歧
    print("\n" + "=" * 96)
    print("按维度统计分歧")
    print("=" * 96)
    from collections import Counter
    c = Counter()
    for _, _, diffs, _ in diff_items:
        for d, _, _ in diffs:
            c[d] += 1
    if c:
        for d, n in c.most_common():
            print(f"  {d:<14}{n} 次")
    else:
        print("  （无分歧）")

    out = {"experiment": "E-77", "n": len(scope),
           "dim_total": total, "dim_agree": agree,
           "dim_agree_rate": round(agree / total, 4),
           "diff_items": [{"rid": r, "text": t,
                           "diffs": [{"dim": d, "mine": a, "theirs": b}
                                     for d, a, b in ds],
                           "model_note": n} for r, t, ds, n in diff_items],
           "dim_conflict_count": dict(c)}
    with open(os.path.join(RESULTS, "e77_scope.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e77_scope.json')}")


if __name__ == "__main__":
    main()
