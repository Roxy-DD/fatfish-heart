"""
E-217 修正版：改写 + 精排组合（多跳题）

修正 E-216 的 bug：top=30 会触发 LIKE 扫描半径放大（E-174 陷阱），
导致"前10名"变差。正确做法：
  · 基线/改写 top-10：独立用 st.search(q, top=10)
  · 改写 pool：用 st.search(q, top=30) 取候选，再用 cross-encoder 精排回 top-10

关键：改写 top-30 的候选池，用 cross-encoder 对"原问题"精排。
"""
from __future__ import annotations
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e214")
RESULTS = os.path.join(_HERE, "results")
DATA = os.path.join(_HERE, "work", "e165", "locomo10.json")

K = 10
POOL = 30


def main():
    from store_v2 import MemoryStore
    from rerank_v1 import CrossEncoderReranker

    gold = json.load(open(os.path.join(TASK, "rewrite_gold.json"), encoding="utf-8"))
    rewrites = {r["cid"]: r.get("rewrites", []) for r in
                json.load(open(os.path.join(TASK, "rewrites.json"), encoding="utf-8"))}

    data = json.load(open(DATA, encoding="utf-8"))
    conv_by_sid = {conv.get("sample_id"): conv for conv in data}

    print("=" * 94)
    print("E-217 修正：改写 + 精排组合（多跳题）")
    print("=" * 94)

    rk = CrossEncoderReranker()
    _ = rk.score("warmup", ["hello"])
    print("  provider:", rk._sess.get_providers())

    base_hit = 0       # 原问题 top-10
    rw_hit = 0         # 改写 top-10（正确：独立 top=10）
    rw_rerank_hit = 0  # 改写 top-30 池 + 精排 top-10
    total = 0

    for g in gold:
        cid = g["cid"]
        sid = g["sid"]
        question = g["question"]
        ev = g["evidence"]
        conv = conv_by_sid.get(sid)
        if not conv:
            continue

        c = conv.get("conversation") or {}
        sessions = []
        j = 1
        while f"session_{j}" in c:
            if c[f"session_{j}"]:
                sessions.append((j, c.get(f"session_{j}_date_time", ""),
                                 c[f"session_{j}"]))
            j += 1

        db = os.path.join(TASK, f"{sid}.db")
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

        total += 1

        # 基线：原问题 top-10
        res0 = st.search(question, top=K)
        got0 = {mark_of.get(t[0].ep_id) for t in res0}
        got0.discard(None)
        base_hit += 1 if any(e in got0 for e in ev) else 0

        # 改写 top-10（正确：独立 top=10）
        all_top10 = set()
        for rq in rewrites.get(cid, []):
            res = st.search(rq, top=K)
            got = {mark_of.get(t[0].ep_id) for t in res}
            got.discard(None)
            all_top10 |= got
        rw_hit += 1 if any(e in all_top10 for e in ev) else 0

        # 改写 top-30 池 + 精排（用原问题精排）
        pool = {}
        for rq in rewrites.get(cid, []):
            res = st.search(rq, top=POOL)
            for tup in res:
                m = mark_of.get(tup[0].ep_id)
                if m and m not in pool:
                    pool[m] = tup[0].raw_text

        if pool:
            cand_marks = list(pool.keys())
            cand_texts = [pool[m] for m in cand_marks]
            rk_scores = rk.score(question, cand_texts)
            idx = sorted(range(len(rk_scores)), key=lambda i: -rk_scores[i])
            top10 = {cand_marks[i] for i in idx[:K]}
            rw_rerank_hit += 1 if any(e in top10 for e in ev) else 0

        st.con.close()

    # ---------- 汇总 ----------
    print(f"\n  多跳题 {total} 题")
    print(f"\n  {'方案':<32}{'命中率'}")
    print("-" * 50)
    print(f"{'原问题 top-10':<32}{base_hit/total:.4f}")
    print(f"{'改写 top-10':<32}{rw_hit/total:.4f}")
    print(f"{'改写pool30 + 精排top10':<32}{rw_rerank_hit/total:.4f}")

    print(f"\n  改写 vs 基线: {rw_hit/total - base_hit/total:+.4f}")
    print(f"  精排 vs 改写top10: {rw_rerank_hit/total - rw_hit/total:+.4f}")

    out = {"experiment": "E-217", "n": total,
           "base": round(base_hit/total, 4),
           "rewrite": round(rw_hit/total, 4),
           "rewrite_rerank": round(rw_rerank_hit/total, 4)}
    with open(os.path.join(RESULTS, "e217_rewrite_rerank_fixed.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e217_rewrite_rerank_fixed.json')}")


if __name__ == "__main__":
    main()
