"""
E-191 修正评分：用"包含"判断，而非"尾部相等"

之前评分 bug：ev_text（完整）和 pick_text（截断 200 字符）的尾部对不齐，
导致"尾部 60 字符相等"误判为不命中。

正确：判断 pick_text 的核心内容是否出现在 ev_text 里（或反之）。
用"去时间戳后的核心句"做子串包含。
"""
from __future__ import annotations

import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
TASK = os.path.join(_HERE, "work", "e191")
RESULTS = os.path.join(_HERE, "results")
DATA = os.path.join(_HERE, "work", "e165", "locomo10.json")


def norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (s or "").lower())).strip()


def strip_meta(s):
    """去掉时间戳和说话人前缀，只留内容"""
    s = s or ""
    # 去掉 [Date...] 和 Speaker:
    s = re.sub(r"\[[^\]]*\]", " ", s)
    s = re.sub(r"^\s*(?:Caroline|Melanie|Jon|Gina|John|Maria|Joanna|Nate|Tim|"
               r"Audrey|Andrew|James|Deborah|Jolene|Evan|Sam|Calvin|Dave)\s*:\s*",
               " ", s, flags=re.I)
    return re.sub(r"\s+", " ", s).strip()


def main():
    tasks = json.load(open(os.path.join(TASK, "batch_tasks.json"),
                           encoding="utf-8"))
    picks = {r["cid"]: r for r in
             json.load(open(os.path.join(TASK, "batch_picks.json"),
                            encoding="utf-8"))}

    data = json.load(open(DATA, encoding="utf-8"))

    # question -> evidence 完整文本
    q_to_ev_text = {}
    for conv in data:
        c = conv.get("conversation") or {}
        mark_text = {}
        j = 1
        while f"session_{j}" in c:
            for ti, turn in enumerate(c[f"session_{j}"] or [], start=1):
                spk = turn.get("speaker") or ""
                txt = turn.get("text") or turn.get("content") or ""
                txt = re.sub(r"\s+", " ", str(txt)).strip()
                if len(txt) >= 5:
                    mark_text[f"D{j}:{ti}"] = f"{spk}: {txt}"
            j += 1
        for q in conv.get("qa") or []:
            question = q.get("question") or ""
            ev = q.get("evidence") or []
            if not isinstance(ev, list):
                ev = [ev]
            ev = [str(e).strip() for e in ev if e]
            ev_texts = [mark_text.get(e, "") for e in ev]
            q_to_ev_text[question] = ev_texts

    hit = 0
    n = 0
    details = []

    for t in tasks:
        cid = t["cid"]
        q = t["question"]
        ev_texts = q_to_ev_text.get(q, [])
        if not ev_texts:
            continue

        pk = picks.get(cid, {}).get("picks") or []
        ep_to_text = {c["ep_id"]: c["text"] for c in t["candidates"]}
        pick_texts = [ep_to_text.get(p, "") for p in pk if ep_to_text.get(p)]

        n += 1
        ok = False
        for pt in pick_texts:
            pt_core = strip_meta(pt)
            if not pt_core:
                continue
            for et in ev_texts:
                et_core = strip_meta(et)
                if not et_core:
                    continue
                # 核心内容包含（任一方向，长度 > 30 才可靠）
                if len(pt_core) > 30 and len(et_core) > 30:
                    if pt_core in et_core or et_core in pt_core:
                        ok = True
                        break
                    # 取前 40 字符子串比较（避开时间戳差异）
                    if pt_core[:40] == et_core[:40]:
                        ok = True
                        break
            if ok:
                break

        hit += 1 if ok else 0
        if not ok and len(details) < 8:
            details.append({
                "cid": cid, "q": q[:50],
                "ev_core": strip_meta(ev_texts[0])[:50] if ev_texts else "",
                "pick_core": strip_meta(pick_texts[0])[:50] if pick_texts else "",
            })

    rate = hit / max(1, n)
    print("=" * 94)
    print("E-191 批量精排命中率（修正评分）")
    print("=" * 94)
    print(f"\n  批量精排命中: {hit}/{n} = {rate:.4f}")
    print(f"  逐条精排（E-186b）: 0.9074")

    print(f"\n  未命中案例:")
    for d in details:
        print(f"\n    [{d['cid']}] {d['q']}")
        print(f"      ev 核心:  {d['ev_core']}")
        print(f"      pick核心: {d['pick_core']}")

    out = {"experiment": "E-191-fixed", "n": n, "hit": hit,
           "rate": round(rate, 4),
           "per_item_rate": 0.9074,
           "details": details}
    with open(os.path.join(RESULTS, "e191_batch_fixed.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e191_batch_fixed.json')}")


if __name__ == "__main__":
    main()
