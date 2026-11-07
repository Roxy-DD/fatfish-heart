"""
E-216 改写检索 + cross-encoder 精排 组合（多跳题）

E-215 改写检索 54.3%（top-10）。
本实验测：改写检索取 top-30，再 cross-encoder 精排取 top-10，能否再提升。
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
    print("E-216 改写检索 + 精排组合（多跳题）")
    print("=" * 94)

    rk = CrossEncoderReranker()
    _ = rk.score("warmup", ["hello"])
    print("  cross-encoder provider:", rk._sess.get_providers())

    base_hit = 0       # 原问题 top-10
    rw_hit = 0         # 改写 top-10
    rw_pool_hit = 0    # 改写 top-30
    rw_rerank_hit = 0  # 改写 top-30 + 精排 top-10
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

        # 基线
        res0 = st.search(question, top=K)
        got0 = {mark_of.get(t[0].ep_id) for t in res0}
        got0.discard(None)
        base_hit += 1 if any(e in got0 for e in ev) else 0

        # 改写 top-10
        all_top10 = set()
        all_pool = set()
        all_texts = {}
        for rq in rewrites.get(cid, []):
            res = st.search(rq, top=POOL)
            for i, tup in enumerate(res):
                m = mark_of.get(tup[0].ep_id)
                if m:
                    if i < K:
                        all_top10.add(m)
                    all_pool.add(m)
                    all_texts[m] = tup[0].raw_text

        rw_hit += 1 if any(e in all_top10 for e in ev) else 0
        rw_pool_hit += 1 if any(e in all_pool for e in ev) else 0

        # 改写 top-30 + 精排
        if all_pool:
            cand_marks = list(all_pool)
            cand_texts = [all_texts[m] for m in cand_marks]
            # 用原问题做精排（不是改写）
            rk_scores = rk.score(question, cand_texts)
            idx = sorted(range(len(rk_scores)), key=lambda i: -rk_scores[i])
            top10_marks = {cand_marks[i] for i in idx[:K]}
            rw_rerank_hit += 1 if any(e in top10_marks for e in ev) else 0

        st.con.close()

    # ---------- 汇总 ----------
    print(f"\n  多跳题 {total} 题")
    print(f"\n  {'方案':<30}{'命中率':<12}")
    print("-" * 50)
    print(f"{'原问题 top-10':<30}{base_hit/total:.4f}")
    print(f"{'改写 top-10':<30}{rw_hit/total:.4f}")
    print(f"{'改写 top-30':<30}{rw_pool_hit/total:.4f}")
    print(f"{'改写top30 + 精排top10':<30}{rw_rerank_hit/total:.4f}")

    print(f"\n  改写 vs 基线: {rw_hit/total - base_hit/total:+.4f}")
    print(f"  精排 vs 改写top10: {rw_rerank_hit/total - rw_hit/total:+.4f}")

    out = {"experiment": "E-216", "n": total,
           "base": round(base_hit/total, 4),
           "rewrite": round(rw_hit/total, 4),
           "rewrite_pool30": round(rw_pool_hit/total, 4),
           "rewrite_rerank": round(rw_rerank_hit/total, 4)}
    with open(os.path.join(RESULTS, "e216_rewrite_rerank.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e216_rewrite_rerank.json')}")


if __name__ == "__main__":
    main()
