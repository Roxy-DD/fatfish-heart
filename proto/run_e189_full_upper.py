"""
E-189 LoCoMo 全量精排理论上限（10 对话 1982 题）

E-186b（前 2 对话）发现 LLM 精排能 +16.2pp。
E-188（前 2 对话）发现 top-50 可救回 73 题，远多于 top-30 的 24 题。

本实验在全量 10 对话上，纯计算（不调 LLM）量化：
  ① 基线 recall@10（真实 store.search）
  ② 不同候选池大小下，多少题"基线漏掉但证据在池内"（可救回）
  ③ 精排的理论上限 = (基线 + 可救回) / 总题

这回答：精排在全量上的空间，以及成本是否可控。
"""
from __future__ import annotations

import json
import os
import re
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e189")
os.makedirs(TASK, exist_ok=True)
RESULTS = os.path.join(_HERE, "results")
DATA = os.path.join(_HERE, "work", "e165", "locomo10.json")

POOLS = [10, 15, 20, 30, 50]
CAT = {"1": "单跳", "2": "时间", "3": "多跳", "4": "常识", "5": "对抗"}


def main():
    from store_v2 import MemoryStore

    data = json.load(open(DATA, encoding="utf-8"))

    print("=" * 94)
    print("E-189 LoCoMo 全量精排理论上限（10 对话）")
    print("=" * 94)

    t0 = time.perf_counter()
    base_hit = 0
    total = 0
    pool_rec = {p: 0 for p in POOLS}          # 各池可救回题数
    by_cat_base = {}
    by_cat_pool = {p: {} for p in POOLS}

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
            res = st.search(question, top=max(POOLS))
            got = []
            for tup in res:
                m = mark_of.get(tup[0].ep_id)
                if m:
                    got.append(m)

            # 基线 top-10
            if any(e in got[:10] for e in ev):
                base_hit += 1
                by_cat_base[cat] = by_cat_base.get(cat, 0) + 1
                continue

            # 各池可救回
            for p in POOLS:
                if any(e in got[:p] for e in ev):
                    pool_rec[p] += 1
                    by_cat_pool[p][cat] = by_cat_pool[p].get(cat, 0) + 1

        st.con.close()
        print(f"  [{sid}] 完成")

    dt = time.perf_counter() - t0

    # ---------- 汇总 ----------
    print("\n" + "=" * 94)
    print("汇总")
    print("=" * 94)

    print(f"\n  总题数: {total}  耗时: {dt:.0f}s")
    print(f"  基线 recall@10: {base_hit}/{total} = {base_hit/total:.4f}")

    print(f"\n  {'候选池':<10}{'可救回题':<12}{'精排成本(题×池)':<18}{'理论整体上限'}")
    print("-" * 94)
    for p in POOLS:
        n = pool_rec[p]
        cost = n * p
        upper = (base_hit + n) / total
        print(f"  {p:<10}{n:<12}{cost:<18}{upper:.4f}")

    # 分题型（top-30）
    print(f"\n  分题型基线 + 可救回（top-30）:")
    print(f"  {'类别':<10}{'基线命中':<12}{'可救回':<10}{'总':<8}{'上限'}")
    print("  " + "-" * 60)
    n30 = pool_rec[30]
    for c in sorted(set(list(by_cat_base.keys()) + list(by_cat_pool[30].keys()))):
        b = by_cat_base.get(c, 0)
        r = by_cat_pool[30].get(c, 0)
        # 每类总数需另算，这里简化显示命中数
        print(f"  {CAT.get(c, c):<10}{b:<12}{r:<10}")

    # ---------- 结论 ----------
    print("\n" + "=" * 94)
    print("结论")
    print("=" * 94)

    n30 = pool_rec[30]
    n50 = pool_rec[50]
    print(f"\n  top-30 精排理论上限: {(base_hit + n30)/total:.4f}  (+{n30/total:.4f})")
    print(f"  top-50 精排理论上限: {(base_hit + n50)/total:.4f}  (+{n50/total:.4f})")

    print(f"\n  成本（假设精排命中率 90%，E-186b 实测）:")
    print(f"    top-30: {n30*30} 次判断 → 预期 +{n30*0.9/total:.4f}")
    print(f"    top-50: {n50*50} 次判断 → 预期 +{n50*0.9/total:.4f}")

    out = {"experiment": "E-189", "total": total,
           "base_hit": base_hit,
           "base_recall": round(base_hit / total, 4),
           "pool_recoverable": pool_rec,
           "pool_upper": {p: round((base_hit + n) / total, 4)
                          for p, n in pool_rec.items()},
           "elapsed_s": round(dt, 1)}
    with open(os.path.join(RESULTS, "e189_full_upper.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e189_full_upper.json')}")


if __name__ == "__main__":
    main()
