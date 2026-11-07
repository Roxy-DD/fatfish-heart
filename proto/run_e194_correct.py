"""
E-194 修正全量基线：用 top=10 直接判基线（不用 top=50 的池）

E-193 发现：E-189 的基线 0.3300 是用 top=50 算的（候选池膨胀导致前10名变差）。
E-174 已证明：top 越大，前10名越差（LIKE 扫描半径变大）。

正确基线：top=10 直接判（与 E-166 一致，0.5802）。

本实验重跑全量 10 对话，用 top=10 判基线，同时记录：
  ① top=10 基线（正确）
  ② 各候选池的可救回题数（用 top=POOL 的 search，但判基线用独立的 top=10）
"""
from __future__ import annotations

import json
import os
import re
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e194")
os.makedirs(TASK, exist_ok=True)
RESULTS = os.path.join(_HERE, "results")
DATA = os.path.join(_HERE, "work", "e165", "locomo10.json")

POOLS = [20, 30, 50]


def main():
    from store_v2 import MemoryStore

    data = json.load(open(DATA, encoding="utf-8"))

    print("=" * 94)
    print("E-194 修正全量基线 + 精排可救回题数")
    print("=" * 94)

    t0 = time.perf_counter()
    base_hit = 0
    total = 0
    pool_rec = {p: 0 for p in POOLS}

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
            if not isinstance(ev, list):
                ev = [ev]
            ev = [str(e).strip() for e in ev if e]
            if not question or not ev:
                continue

            total += 1

            # 基线：top=10 独立判（正确做法）
            res10 = st.search(question, top=10)
            got10 = {mark_of.get(t[0].ep_id) for t in res10}
            got10.discard(None)
            if any(e in got10 for e in ev):
                base_hit += 1
                continue

            # 可救回：用 top=50 的 search，看证据在不在更大池里
            res50 = st.search(question, top=50)
            got50 = []
            for tup in res50:
                m = mark_of.get(tup[0].ep_id)
                if m:
                    got50.append(m)

            for p in POOLS:
                if any(e in got50[:p] for e in ev):
                    pool_rec[p] += 1

        st.con.close()
        print(f"  [{sid}] 完成")

    dt = time.perf_counter() - t0

    # ---------- 汇总 ----------
    print("\n" + "=" * 94)
    print("汇总")
    print("=" * 94)

    print(f"\n  总题数: {total}  耗时: {dt:.0f}s")
    print(f"  基线 recall@10（top=10 正确）: {base_hit}/{total} = "
          f"{base_hit/total:.4f}")

    print(f"\n  {'候选池':<10}{'可救回题':<12}{'精排后整体(99%命中)'}")
    print("-" * 60)
    for p in POOLS:
        n = pool_rec[p]
        saved = n * 0.99
        after = (base_hit + saved) / total
        print(f"  {p:<10}{n:<12}{after:.4f}")

    # ---------- 结论 ----------
    print("\n" + "=" * 94)
    print("结论")
    print("=" * 94)

    n30 = pool_rec.get(30, 0)
    n50 = pool_rec.get(50, 0)
    print(f"\n  正确基线: {base_hit/total:.4f}")
    print(f"  top-30 精排后: {(base_hit + n30*0.99)/total:.4f}  "
          f"(+{n30*0.99/total:.4f})")
    print(f"  top-50 精排后: {(base_hit + n50*0.99)/total:.4f}  "
          f"(+{n50*0.99/total:.4f})")

    out = {"experiment": "E-194", "total": total,
           "base_hit": base_hit,
           "baseline": round(base_hit / total, 4),
           "pool_recoverable": pool_rec,
           "rerank_after": {
               str(p): round((base_hit + pool_rec[p] * 0.99) / total, 4)
               for p in POOLS},
           "elapsed_s": round(dt, 1)}
    with open(os.path.join(RESULTS, "e194_correct_baseline.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e194_correct_baseline.json')}")


if __name__ == "__main__":
    main()
