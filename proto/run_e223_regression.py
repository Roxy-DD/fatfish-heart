"""
E-223 全量回归：当前代码状态下的 LoCoMo 基线 recall

目的：确认所有已落地改动（停用词、structured默认关、rerank参数、sem_pool/like_pool）
叠加后，基线 recall 与最初的 0.5802 是否一致（未退化）。

只跑基线（不 rerank），对比 E-166 的 0.5802。
"""
from __future__ import annotations
import json
import os
import re
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e223")
os.makedirs(TASK, exist_ok=True)
RESULTS = os.path.join(_HERE, "results")
DATA = os.path.join(_HERE, "work", "e165", "locomo10.json")

K = 10


def main():
    from store_v2 import MemoryStore

    data = json.load(open(DATA, encoding="utf-8"))

    print("=" * 94)
    print("E-223 全量回归：当前代码状态 LoCoMo 基线")
    print("=" * 94)

    t0 = time.perf_counter()
    base_hit = 0
    total = 0
    by_cat = {}

    for conv in data:
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

        for q in qa:
            question = q.get("question") or ""
            ev = q.get("evidence") or []
            cat = str(q.get("category"))
            if not isinstance(ev, list):
                ev = [ev]
            ev = [str(e).strip() for e in ev if e]
            if not question or not ev:
                continue
            total += 1
            res = st.search(question, top=K)
            got = {mark_of.get(t[0].ep_id) for t in res}
            got.discard(None)
            hit = any(e in got for e in ev)
            base_hit += 1 if hit else 0
            by_cat[cat] = by_cat.get(cat, [0, 0])
            by_cat[cat][1] += 1
            by_cat[cat][0] += 1 if hit else 0

        st.con.close()

    dt = time.perf_counter() - t0

    print(f"\n  总题数 {total}，耗时 {dt:.0f}s")
    print(f"  基线 recall@10: {base_hit}/{total} = {base_hit/total:.4f}")
    print(f"  对比 E-166 基线: 0.5802")
    print(f"  差异: {base_hit/total - 0.5802:+.4f}")

    print(f"\n  分题型:")
    CAT = {"1": "单跳", "2": "时间", "3": "多跳", "4": "常识", "5": "对抗"}
    for c in sorted(by_cat):
        h, n = by_cat[c]
        print(f"    {CAT.get(c, c)}: {h}/{n} = {h/n:.4f}")

    verdict = "✅ 未退化" if abs(base_hit/total - 0.5802) < 0.01 else "❌ 有退化"
    print(f"\n  结论: {verdict}")

    out = {"experiment": "E-223", "total": total,
           "baseline": round(base_hit/total, 4),
           "e166_baseline": 0.5802,
           "diff": round(base_hit/total - 0.5802, 4),
           "by_cat": by_cat}
    with open(os.path.join(RESULTS, "e223_regression.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e223_regression.json')}")


if __name__ == "__main__":
    main()
