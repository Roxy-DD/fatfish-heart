"""
E-140 评分：duplicate vs merge 的区分能力
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
TASK = os.path.join(_HERE, "work", "e140")
RESULTS = os.path.join(_HERE, "results")


def main():
    packs = json.load(open(os.path.join(TASK, "packs.json"), encoding="utf-8"))
    mp = os.path.join(TASK, "model.json")
    if not os.path.exists(mp):
        print("缺少 model.json")
        return
    model = {r["cid"]: r for r in json.load(open(mp, encoding="utf-8"))}

    print("=" * 96)
    print("E-140 duplicate vs merge 区分能力")
    print("=" * 96)
    print(f"\n{'cid':<6}{'期望':<12}{'模型':<12}{'对?':<6}{'类型':<12}{'新记忆'}")
    print("-" * 96)

    hit = 0
    details = []
    for p in packs:
        cid = p["cid"]
        m = model.get(cid, {})
        expect = p["expect"]
        pred = m.get("relation", "?")

        # 目标是否正确（E-140 里每组的 gold target 是 candidates 里含 expect 对应那条）
        rel_ok = (pred == expect)
        hit += 1 if rel_ok else 0

        print(f"{cid:<6}{expect:<12}{pred:<12}"
              f"{'✓' if rel_ok else '✗':<6}{p['kind']:<12}{p['new_memory'][:34]}")
        details.append({
            "cid": cid, "expect": expect, "pred": pred,
            "kind": p["kind"], "new": p["new_memory"],
            "rel_ok": rel_ok,
            "reason": m.get("reason", ""),
            "new_info": m.get("new_info"),
        })

    n = len(packs)
    print("-" * 96)
    print(f"\n  总准确率: {hit}/{n} = {hit/n:.4f}")

    # 分组准确率
    from collections import defaultdict
    by = defaultdict(lambda: [0, 0])
    for d in details:
        by[d["expect"]][1] += 1
        if d["rel_ok"]:
            by[d["expect"]][0] += 1

    print(f"\n{'关系':<14}{'对/总':<12}{'准确率'}")
    print("-" * 96)
    for t in ["duplicate", "merge", "replace", "none"]:
        if t in by:
            h, tot = by[t]
            print(f"{t:<14}{f'{h}/{tot}':<12}{h/tot:.4f}")

    # 关键：duplicate vs merge 的混淆矩阵
    print(f"\n" + "-" * 96)
    print("关键：duplicate / merge 混淆")
    print("-" * 96)
    dup_as_merge = sum(1 for d in details
                       if d["expect"] == "duplicate" and d["pred"] == "merge")
    merge_as_dup = sum(1 for d in details
                       if d["expect"] == "merge" and d["pred"] == "duplicate")
    print(f"  duplicate 被误判为 merge: {dup_as_merge}")
    print(f"  merge 被误判为 duplicate: {merge_as_dup}")
    print(f"  （两者都是错误，但方向不同：")
    print(f"    前者会导致该合并的没合并（冗余累积）")
    print(f"    后者会导致信息被丢弃（更严重）")

    fails = [d for d in details if not d["rel_ok"]]
    if fails:
        print(f"\n失败案例:")
        for d in fails:
            print(f"\n  [{d['cid']}] {d['kind']}")
            print(f"    期望={d['expect']}  模型={d['pred']}")
            print(f"    新记忆: {d['new'][:64]}")
            print(f"    模型理由: {d['reason'][:76]}")
            if d["new_info"]:
                print(f"    模型识别的新增信息: {str(d['new_info'])[:60]}")
    else:
        print(f"\n  全部正确")

    out = {"experiment": "E-140", "n": n,
           "accuracy": round(hit / n, 4),
           "by_type": {t: {"hit": v[0], "total": v[1]} for t, v in by.items()},
           "dup_as_merge": dup_as_merge,
           "merge_as_dup": merge_as_dup,
           "details": details}
    with open(os.path.join(RESULTS, "e140_discrimination.json"),
              "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e140_discrimination.json')}")


if __name__ == "__main__":
    main()
