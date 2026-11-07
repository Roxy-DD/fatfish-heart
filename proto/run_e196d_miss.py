"""
E-196d 直接看 51 个未命中题，判断"gold 错" vs "精排错"

不靠正则，直接输出未命中题的三元组：
  question + evidence 原文 + 精排 pick 的 reason
人工（我）逐条判断。
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

    e196 = json.load(open(os.path.join(RESULTS, "e196_rerank_full.json"),
                          encoding="utf-8"))
    miss_cids = {d["cid"] for d in e196["details"]}

    print("=" * 94)
    print("E-196d 51 个未命中题逐条（供人工判断）")
    print("=" * 94)

    rows = []
    for g in gold:
        cid = g["cid"]
        if cid not in miss_cids:
            continue
        r = picks_all.get(cid, {})
        reason = r.get("reason", "")
        picks = r.get("picks") or []
        ep_to_text = {c["ep_id"]: c["text"] for c in g["candidates"]}
        pick_text = strip_meta(ep_to_text.get(picks[0], ""))[:50] if picks else "(空)"
        ev_text = strip_meta(g["evidence_texts"][0])[:50] if g["evidence_texts"] else ""

        rows.append({
            "cid": cid, "cat": g.get("category"),
            "q": g["question"], "ev": ev_text,
            "pick": pick_text, "reason": reason,
        })

    print(f"\n共 {len(rows)} 个未命中题\n")
    for r in rows:
        print(f"[{r['cid']}] cat={r['cat']}  {r['q'][:50]}")
        print(f"    ev:    {r['ev']}")
        print(f"    pick:  {r['pick']}")
        print(f"    reason: {r['reason'][:70]}")
        print()

    with open(os.path.join(RESULTS, "e196d_miss_rows.json"), "w",
              encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    print(f"写入 {os.path.join(RESULTS, 'e196d_miss_rows.json')}")


if __name__ == "__main__":
    main()
