"""
E-188 精排成本优化：候选池从 30 缩到 15，收益保留多少？

E-186b 结果：top-30 精排，救回 49/54 = 90.7%，整体 +16.2pp
成本：54 × 30 = 1620 次 LLM 判断

本实验测：候选池缩小后，收益如何变化？

关键问题：证据在 top-15 内 vs top-30 内的比例
  如果大部分证据在前 15，那 top-15 精排就够了
  如果分散在 15-30，缩小池会损失收益
"""
from __future__ import annotations

import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e188")
os.makedirs(TASK, exist_ok=True)
RESULTS = os.path.join(_HERE, "results")
DATA = os.path.join(_HERE, "work", "e165", "locomo10.json")

N_CONV = 2


def main():
    from store_v2 import MemoryStore

    data = json.load(open(DATA, encoding="utf-8"))

    print("=" * 94)
    print("E-188 精排成本优化：候选池大小 vs 收益")
    print("=" * 94)

    # 对每个可救回题，看证据在不同 pool 大小下是否在池内
    pool_sizes = [10, 15, 20, 25, 30, 50]
    stats = {p: {"recoverable": 0} for p in pool_sizes}

    base_hit = 0
    total = 0

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
            res = st.search(question, top=50)
            got_marks = []
            for tup in res:
                m = mark_of.get(tup[0].ep_id)
                if m:
                    got_marks.append(m)

            # 基线命中（top-10）
            if any(e in got_marks[:10] for e in ev):
                base_hit += 1
                continue

            # 各 pool 大小下，证据是否在池内
            for p in pool_sizes:
                pool = set(got_marks[:p])
                if any(e in pool for e in ev):
                    stats[p]["recoverable"] += 1

        st.con.close()

    # ---------- 汇总 ----------
    print(f"\n前 {N_CONV} 对话，总 {total} 题，基线命中 {base_hit}")
    print(f"基线漏掉: {total - base_hit} 题\n")

    print(f"{'候选池大小':<12}{'可救回题数':<14}{'精排成本(题×池)':<16}"
          f"{'理论上限(整体)'}")
    print("-" * 94)

    for p in pool_sizes:
        n_rec = stats[p]["recoverable"]
        cost = n_rec * p
        upper = (base_hit + n_rec) / total
        print(f"{p:<12}{n_rec:<14}{cost:<16}{upper:.4f}")

    # ---------- 结论 ----------
    print("\n" + "=" * 94)
    print("结论")
    print("=" * 94)

    # 关键对比：pool=15 vs pool=30
    n15 = stats[15]["recoverable"]
    n30 = stats[30]["recoverable"]
    print(f"\n  top-15 可救回 {n15} 题，top-30 可救回 {n30} 题")
    print(f"  top-15 保留了 {n15/max(1,n30):.1%} 的可救回题")

    cost15 = n15 * 15
    cost30 = n30 * 30
    print(f"\n  成本对比：top-15 = {cost15} 次判断，top-30 = {cost30} 次")
    print(f"  节省: {(1 - cost15/cost30):.1%}")

    # 收益对比（假设精排命中率 90%，与 E-186b 一致）
    gain15 = n15 * 0.9
    gain30 = n30 * 0.9
    print(f"\n  预期收益（精排命中率 90%）：")
    print(f"    top-15: +{gain15/total:.4f}（{gain15:.0f} 题）")
    print(f"    top-30: +{gain30/total:.4f}（{gain30:.0f} 题）")
    print(f"    效率（每题提升/每判断）: "
          f"top-15={gain15/cost15:.4f}  top-30={gain30/cost30:.4f}")

    out = {"experiment": "E-188", "total": total, "base_hit": base_hit,
           "pool_stats": stats,
           "n15": n15, "n30": n30}
    with open(os.path.join(RESULTS, "e188_pool_cost.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e188_pool_cost.json')}")


if __name__ == "__main__":
    main()
