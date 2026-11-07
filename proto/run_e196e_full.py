"""
E-196e 完整统计 51 个未命中题（修正 E-196 details 截断问题）

E-196 的 details 只存了前 10 个，导致 E-196d 只看到 10 个。
这里重新完整统计，并把 51 个未命中题分类：
  T1 精排空 picks（诚实放弃）
  T2 精排选了，但文本不匹配（可能 gold 错，可能答案合成，可能精排错）
"""
from __future__ import annotations

import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
TASK = os.path.join(_HERE, "work", "e196")
RESULTS = os.path.join(_HERE, "results")


def strip_meta(s):
    s = s or ""
    s = re.sub(r"\[[^\]]*\]", " ", s)
    s = re.sub(r"^\s*(?:Caroline|Melanie|Jon|Gina|John|Maria|Joanna|Nate|Tim|"
               r"Audrey|Andrew|James|Deborah|Jolene|Evan|Sam|Calvin|Dave)\s*:\s*",
               " ", s, flags=re.I)
    return re.sub(r"\s+", " ", s).strip()


def main():
    picks_all = {}
    for i in range(4):
        p = os.path.join(TASK, f"rerank_picks_part{i}.json")
        if os.path.exists(p):
            for r in json.load(open(p, encoding="utf-8")):
                picks_all[r["cid"]] = r

    gold = json.load(open(os.path.join(TASK, "rerank_gold.json"),
                          encoding="utf-8"))

    # 重新完整判定命中（与 E-196 相同逻辑）
    hit = 0
    miss = []
    for g in gold:
        cid = g["cid"]
        pk = picks_all.get(cid, {}).get("picks") or []
        ep_to_text = {c["ep_id"]: c["text"] for c in g["candidates"]}
        pick_texts = [ep_to_text.get(p, "") for p in pk if ep_to_text.get(p)]

        ok = False
        for pt in pick_texts:
            pt_core = strip_meta(pt)
            if not pt_core:
                continue
            for et in g["evidence_texts"]:
                et_core = strip_meta(et)
                if not et_core:
                    continue
                if len(pt_core) > 25 and len(et_core) > 25:
                    if pt_core in et_core or et_core in pt_core:
                        ok = True
                        break
                    if pt_core[:40] == et_core[:40]:
                        ok = True
                        break
            if ok:
                break

        if ok:
            hit += 1
        else:
            miss.append({
                "cid": cid, "cat": g.get("category"),
                "q": g["question"],
                "empty": not pick_texts,
                "reason": picks_all.get(cid, {}).get("reason", ""),
            })

    print("=" * 94)
    print("E-196e 完整统计")
    print("=" * 94)
    print(f"\n  总 {len(gold)}，命中 {hit}，未命中 {len(miss)}")
    print(f"  命中率 {hit/len(gold):.4f}")

    n_empty = sum(1 for m in miss if m["empty"])
    n_picked = len(miss) - n_empty
    print(f"\n  未命中 {len(miss)} 题中:")
    print(f"    · 空 picks（诚实放弃）: {n_empty}")
    print(f"    · 有 picks 但文本不匹配: {n_picked}")

    # 分类统计
    from collections import Counter
    cat_dist = Counter(m["cat"] for m in miss)
    print(f"\n  未命中题分题型:")
    for c in sorted(cat_dist):
        print(f"    类别 {c}: {cat_dist[c]}")

    # 保存完整 miss 列表
    with open(os.path.join(RESULTS, "e196e_miss_full.json"), "w",
              encoding="utf-8") as f:
        json.dump(miss, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e196e_miss_full.json')}")

    out = {"experiment": "E-196e", "total": len(gold), "hit": hit,
           "rate": round(hit / len(gold), 4),
           "n_miss": len(miss), "n_empty": n_empty, "n_picked": n_picked,
           "cat_dist": dict(cat_dist)}
    with open(os.path.join(RESULTS, "e196e_summary.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
