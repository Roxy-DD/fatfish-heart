"""
E-186b 终极评分：直接生成带 mark 和 evidence 文本的 recoverable，
然后用文本匹配 picks（picks 通过 ep_id -> text 映射到 recoverable 的 candidates）
"""
from __future__ import annotations

import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e186b")
RESULTS = os.path.join(_HERE, "results")
DATA = os.path.join(_HERE, "work", "e165", "locomo10.json")

K = 10
POOL = 30
N_CONV = 2


def norm(s):
    return re.sub(r"\s+", "", (s or "").lower())


def main():
    from store_v2 import MemoryStore

    data = json.load(open(DATA, encoding="utf-8"))
    picks = {r["cid"]: r for r in
             json.load(open(os.path.join(TASK, "picks.json"), encoding="utf-8"))}

    base_hit = 0
    total = 0
    recoverable = []

    # 关键：每个对话单独建库，且把 mark 和文本都记在 recoverable 里
    for conv in data[:N_CONV]:
        sid = conv.get("sample_id")
        qa = conv.get("qa") or []
        c = conv.get("conversation") or {}

        sessions = []
        j = 1
        while f"session_{j}" in c:
            if c[f"session_{j}"]:
                sessions.append((j, c.get(f"session_{j}_date_time", ""),
                                 c[f"session_{j}"]))
            j += 1

        db = os.path.join(TASK, f"{sid}_ult.db")
        for suf in ("", "-wal", "-shm"):
            p = db + suf
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass
        st = MemoryStore(db)

        mark_of = {}
        text_of_mark = {}
        for snum, dt, turns in sessions:
            for ti, turn in enumerate(turns, start=1):
                spk = turn.get("speaker") or ""
                txt = turn.get("text") or turn.get("content") or ""
                txt = re.sub(r"\s+", " ", str(txt)).strip()
                if len(txt) < 5:
                    continue
                full = f"[{dt}] {spk}: {txt}" if dt else f"{spk}: {txt}"
                try:
                    ep = st.add(full[:800], resolve_conflict=False)
                    mark = f"D{snum}:{ti}"
                    mark_of[ep.ep_id] = mark
                    text_of_mark[mark] = full
                except Exception:
                    pass

        for q in qa:
            question = q.get("question") or ""
            ev = q.get("evidence") or []
            if not isinstance(ev, list):
                ev = [ev]
            ev = [str(e).strip() for e in ev if e]
            if not question or not ev:
                continue

            total += 1
            res = st.search(question, top=POOL)
            cands = []
            for tup in res:
                ep = tup[0]
                cands.append({
                    "ep_id": ep.ep_id,
                    "mark": mark_of.get(ep.ep_id),
                    "text": ep.raw_text[:400],
                })

            top10_marks = {c2["mark"] for c2 in cands[:K] if c2["mark"]}
            if any(e in top10_marks for e in ev):
                base_hit += 1
                continue

            pool_marks = {c2["mark"] for c2 in cands if c2["mark"]}
            if not any(e in pool_marks for e in ev):
                continue

            ev_texts = [text_of_mark.get(e, "") for e in ev]
            recoverable.append({
                "question": question,
                "ev": ev,
                "ev_texts": ev_texts,
                "candidates": cands,
            })

        st.con.close()

    print("=" * 94)
    print("E-186b 终极评分（用文本，不依赖 ep_id 跨文件）")
    print("=" * 94)
    print(f"\n总 {total} 题，基线命中 {base_hit}，可救回 {len(recoverable)}")

    # 用 picks 的 ep_id 在 recoverable 的 candidates 里找 text，再比对 ev_text
    hit = 0
    n = 0
    details = []

    for i, rec in enumerate(recoverable):
        cid = f"Q{i:02d}"
        pk = picks.get(cid, {}).get("picks") or []

        ep_to_text = {c["ep_id"]: c["text"] for c in rec["candidates"]}
        pick_texts = [ep_to_text.get(p, "") for p in pk if ep_to_text.get(p)]

        n += 1
        ok = False
        for pt in pick_texts:
            for et in rec["ev_texts"]:
                if norm(pt) == norm(et):
                    ok = True
                    break
                # 子串匹配（去时间戳前缀，比较后半段）
                ptn, etn = norm(pt), norm(et)
                if len(ptn) > 40 and len(etn) > 40:
                    if ptn[-50:] == etn[-50:]:
                        ok = True
                        break
            if ok:
                break

        hit += 1 if ok else 0
        if not ok and len(details) < 8:
            details.append({
                "cid": cid, "q": rec["question"][:50],
                "ev_text_tail": rec["ev_texts"][0][-50:] if rec["ev_texts"] else "",
                "pick_text_tail": pick_texts[0][-50:] if pick_texts else "",
            })

    rate = hit / max(1, n)
    print(f"\n  精排命中: {hit}/{n} = {rate:.4f}")
    print(f"  整体: {base_hit/total:.4f} → {(base_hit+hit)/total:.4f}  "
          f"(+{hit/total:.4f})")

    print(f"\n  未命中案例（前 8）:")
    for d in details:
        print(f"\n    [{d['cid']}] {d['q']}")
        print(f"      ev 尾:  ...{d['ev_text_tail']}")
        print(f"      pick尾: ...{d['pick_text_tail']}")

    out = {"experiment": "E-186b-ultimate", "n": n, "hit": hit,
           "rate": round(rate, 4),
           "baseline": round(base_hit / total, 4),
           "overall_gain": round(hit / total, 4)}
    with open(os.path.join(RESULTS, "e186b_rerank_ultimate.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e186b_rerank_ultimate.json')}")


if __name__ == "__main__":
    main()
