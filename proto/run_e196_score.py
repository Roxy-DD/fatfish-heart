"""
E-196 全量精排评分（244 题，4 个子代理结果合并）

评分用「配套 ep_id + 文本包含」，不重建库。
任务包 rerank_gold.json 里已存 evidence 原文文本。
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
    # 合并 4 份 picks
    picks_all = {}
    for i in range(4):
        p = os.path.join(TASK, f"rerank_picks_part{i}.json")
        if not os.path.exists(p):
            print(f"缺少 part{i}")
            continue
        for r in json.load(open(p, encoding="utf-8")):
            picks_all[r["cid"]] = r

    gold = json.load(open(os.path.join(TASK, "rerank_gold.json"),
                          encoding="utf-8"))

    print("=" * 94)
    print("E-196 全量精排评分")
    print("=" * 94)
    print(f"\n  gold 题数: {len(gold)}   picks 数: {len(picks_all)}")

    hit = 0
    n = 0
    empty_picks = 0
    by_cat = {}
    details = []

    for g in gold:
        cid = g["cid"]
        cat = g.get("category", "?")
        pk = picks_all.get(cid, {}).get("picks") or []

        if not pk:
            empty_picks += 1

        # pick 文本 vs evidence 文本
        ep_to_text = {c["ep_id"]: c["text"] for c in g["candidates"]}
        pick_texts = [ep_to_text.get(p, "") for p in pk if ep_to_text.get(p)]

        n += 1
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

        hit += 1 if ok else 0
        by_cat[cat] = by_cat.get(cat, [0, 0])
        by_cat[cat][1] += 1
        by_cat[cat][0] += 1 if ok else 0

        if not ok and len(details) < 10:
            details.append({
                "cid": cid, "cat": cat, "q": g["question"][:45],
                "ev_core": strip_meta(g["evidence_texts"][0])[:45]
                if g["evidence_texts"] else "",
                "pick_core": strip_meta(pick_texts[0])[:45]
                if pick_texts else "(空 picks)",
            })

    rate = hit / max(1, n)
    print(f"\n  精排命中: {hit}/{n} = {rate:.4f}")
    print(f"  空 picks 题数: {empty_picks}")

    print(f"\n  分题型命中:")
    print(f"  {'类别':<10}{'命中/总':<14}{'命中率'}")
    print("  " + "-" * 40)
    for c in sorted(by_cat):
        h, t = by_cat[c]
        print(f"  {c:<10}{f'{h}/{t}':<14}{h/t:.4f}")

    print(f"\n  未命中案例（前 10）:")
    for d in details:
        print(f"\n    [{d['cid']}] cat={d['cat']} {d['q']}")
        print(f"      ev 核心:  {d['ev_core']}")
        print(f"      pick核心: {d['pick_core']}")

    # 全量收益
    total = 1982
    base = 1150
    saved = hit
    after = (base + saved) / total
    print(f"\n" + "=" * 94)
    print("全量收益")
    print("=" * 94)
    print(f"\n  基线: {base}/{total} = {base/total:.4f}")
    print(f"  精排后: ({base}+{saved})/{total} = {after:.4f}")
    print(f"  提升: {after - base/total:+.4f}")

    out = {"experiment": "E-196", "n": n, "hit": hit,
           "rate": round(rate, 4), "empty_picks": empty_picks,
           "by_cat": by_cat,
           "baseline": round(base / total, 4),
           "after": round(after, 4),
           "gain": round(after - base / total, 4),
           "details": details}
    with open(os.path.join(RESULTS, "e196_rerank_full.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e196_rerank_full.json')}")


if __name__ == "__main__":
    main()
