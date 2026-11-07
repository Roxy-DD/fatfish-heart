"""
E-186b 最终评分（修正版）：LLM 精排救回率

之前两次评分都错了：
  ① run_e186b_score.py 重建库导致 ep_id 变化 → 0/54（假阴性）
  ② _score_rerank_text.py mark_text 跨对话覆盖 → 文本对不上

正确做法：
  recoverable.json 里已经存了每题的 candidates（含 text），
  evidence 是 D{session}:{turn} 格式。
  需要按「对话」隔离，重建每个对话的 mark -> text 映射。

但更简单：recoverable.json 生成时（E-186b）没有存 mark，
所以无法直接从 ep_id 反查 mark。

最可靠的方法：重新生成 recoverable.json，这次带上 mark。
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


def main():
    from store_v2 import MemoryStore

    data = json.load(open(DATA, encoding="utf-8"))
    picks = {r["cid"]: r for r in
             json.load(open(os.path.join(TASK, "picks.json"), encoding="utf-8"))}

    # 重新生成，这次记录 ep_id -> mark，并带上 mark 供评分
    hit = 0
    n = 0
    base_hit = 0
    total = 0
    details = []

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

        db = os.path.join(TASK, f"{sid}_v2.db")
        for suf in ("", "-wal", "-shm"):
            p = db + suf
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass
        st = MemoryStore(db)

        mark_of = {}
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
                    mark_of[ep.ep_id] = f"D{snum}:{ti}"
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

            # 基线命中
            top10_marks = {c2["mark"] for c2 in cands[:K] if c2["mark"]}
            b_hit = any(e in top10_marks for e in ev)
            if b_hit:
                base_hit += 1
                continue

            # 只在"可救回"的题里评精排
            pool_marks = {c2["mark"] for c2 in cands if c2["mark"]}
            if not any(e in pool_marks for e in ev):
                continue

            # 精排命中：picks 里的 ep_id 对应的 mark 命中 evidence
            cid = f"Q{len(details):02d}"
            pk = picks.get(cid, {}).get("picks") or []
            pick_marks = set()
            for c2 in cands:
                if c2["ep_id"] in pk:
                    pick_marks.add(c2["mark"])
            pick_marks.discard(None)

            n += 1
            ok = bool(set(ev) & pick_marks)
            hit += 1 if ok else 0

            if not ok:
                details.append({
                    "cid": cid, "q": question[:50], "ev": ev,
                    "pick_marks": sorted(pick_marks),
                })

        st.con.close()

    # ---------- 汇总 ----------
    print("=" * 94)
    print("E-186b 最终：LLM 精排救回率（修正版）")
    print("=" * 94)

    rate = hit / max(1, n)
    print(f"\n  前 {N_CONV} 对话，总 {total} 题，基线命中 {base_hit}")
    print(f"  可救回题（基线漏 + evidence 在 pool）: {n}")
    print(f"  LLM 精排命中: {hit}/{n} = {rate:.4f}")

    print(f"\n  整体:")
    print(f"    基线: {base_hit}/{total} = {base_hit/total:.4f}")
    print(f"    精排后: ({base_hit}+{hit})/{total} = "
          f"{(base_hit + hit)/total:.4f}")

    print(f"\n  精排提升: +{hit/total:.4f}")

    print(f"\n  未命中案例（前 5）:")
    for d in details[:5]:
        print(f"    [{d['cid']}] {d['q']}")
        print(f"      evidence={d['ev']}  精排选了={d['pick_marks']}")

    out = {"experiment": "E-186b-final", "n_recoverable": n,
           "rerank_hit": hit, "rate": round(rate, 4),
           "baseline_hit": base_hit, "total": total,
           "overall_gain": round(hit / total, 4),
           "details": details}
    with open(os.path.join(RESULTS, "e186b_rerank_final.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e186b_rerank_final.json')}")


if __name__ == "__main__":
    main()
