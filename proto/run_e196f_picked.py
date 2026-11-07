"""
E-196f 抽样看 32 个"有 picks 但文本不匹配"的题

判断：精排的 reason 是否体现了正确理解，只是文本匹配没对上。
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

    # 找"有 picks 但文本不匹配"的题
    rows = []
    for g in gold:
        cid = g["cid"]
        r = picks_all.get(cid, {})
        pk = r.get("picks") or []
        ep_to_text = {c["ep_id"]: c["text"] for c in g["candidates"]}
        pick_texts = [ep_to_text.get(p, "") for p in pk if ep_to_text.get(p)]

        if not pick_texts:
            continue  # 空 picks，跳过

        # 判定是否命中
        ok = False
        for pt in pick_texts:
            pt_core = strip_meta(pt)
            for et in g["evidence_texts"]:
                et_core = strip_meta(et)
                if len(pt_core) > 25 and len(et_core) > 25:
                    if pt_core in et_core or et_core in pt_core:
                        ok = True
                        break
                    if pt_core[:40] == et_core[:40]:
                        ok = True
                        break
            if ok:
                break

        if not ok:
            rows.append({
                "cid": cid, "cat": g.get("category"),
                "q": g["question"],
                "answer": g.get("answer", ""),
                "ev": strip_meta(g["evidence_texts"][0])[:60] if g["evidence_texts"] else "",
                "pick": strip_meta(pick_texts[0])[:60],
                "reason": r.get("reason", "")[:70],
            })

    print(f"有 picks 但文本不匹配: {len(rows)} 题\n")
    # 打印全部（供判断）
    for r in rows:
        print(f"[{r['cid']}] cat={r['cat']}  Q: {r['q'][:48]}")
        print(f"    gold答案: {r['answer'][:40]}")
        print(f"    ev:    {r['ev']}")
        print(f"    pick:  {r['pick']}")
        print(f"    reason: {r['reason']}")
        print()

    with open(os.path.join(RESULTS, "e196f_picked_miss.json"), "w",
              encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
