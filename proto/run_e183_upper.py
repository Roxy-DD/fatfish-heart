"""
E-183 embedding 能力上限测试：现有模型 vs 理论最优

E-181/E-182 证明：检索层的各种技巧都无效，瓶颈在 embedding。

本实验回答两个问题：
  ① 现有 BGE-small-zh 在 LoCoMo 上的**纯语义召回上限**是多少？
     （不考虑任何通道，只看向量余弦 top-10）
  ② 证据如果"能被找到"，它应该排第几？
     （用 evidence 与 question 的余弦分布判断）

方法：
  不用 store.search，直接算：
    · 每个 question 的向量
    · 每个 evidence 轮次的向量
    · evidence 在**全库余弦排名**里的位置分布

这能回答：如果 embedding 完美，能到多少？现在差多少？

注意：这不是手写复现 store.search（E-173 的错），
      而是独立的"理论上限"分析，两者目的不同。
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e183")
os.makedirs(TASK, exist_ok=True)
RESULTS = os.path.join(_HERE, "results")
DATA = os.path.join(_HERE, "work", "e165", "locomo10.json")

K = 10
N_CONV = 3


def main():
    from store_v2 import MemoryStore
    from store_v1 import Embedder
    import numpy as np

    data = json.load(open(DATA, encoding="utf-8"))
    emb = Embedder.get()

    print("=" * 94)
    print("E-183 embedding 能力上限分析")
    print("=" * 94)
    print(f"\n跑前 {N_CONV} 个对话\n")

    all_ranks = []          # 每个 evidence 的余弦排名
    per_q_best_rank = []    # 每题最好的 evidence 排名
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

        mark_of, id_of_mark = {}, {}
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
                    id_of_mark[mark] = ep.ep_id
                except Exception:
                    pass

        st._load_vectors(include_invalid=False)
        norms = np.linalg.norm(st._vec, axis=1)
        norms[norms == 0] = 1.0
        idx_of_id = {eid: i for i, eid in enumerate(st._vec_ids)}

        for q in qa:
            question = q.get("question") or ""
            ev = q.get("evidence") or []
            if not isinstance(ev, list):
                ev = [ev]
            ev = [str(e).strip() for e in ev if e]
            if not question or not ev:
                continue

            nv = emb.encode(question)[0]
            sims = (st._vec @ nv) / norms

            best = None
            for e in ev:
                eid = id_of_mark.get(e)
                if not eid or eid not in idx_of_id:
                    continue
                r = int((sims > sims[idx_of_id[eid]]).sum()) + 1
                all_ranks.append(r)
                if best is None or r < best:
                    best = r

            if best is not None:
                per_q_best_rank.append(best)
                total += 1

        st.con.close()

    # ---------- 分析 ----------
    print("=" * 94)
    print("分析")
    print("=" * 94)

    n = len(per_q_best_rank)
    print(f"\n题目数: {n}")
    print(f"evidence 条目总数: {len(all_ranks)}")

    # 每题最好 evidence 的排名分布
    buckets = Counter()
    for r in per_q_best_rank:
        if r <= 1:
            buckets["第 1 名"] += 1
        elif r <= 3:
            buckets["第 2-3 名"] += 1
        elif r <= 10:
            buckets["第 4-10 名"] += 1
        elif r <= 30:
            buckets["第 11-30 名"] += 1
        elif r <= 100:
            buckets["第 31-100 名"] += 1
        else:
            buckets["100 名以外"] += 1

    print(f"\n① 每题「最好的那条 evidence」在全库余弦排名中的位置")
    print("-" * 70)
    cum = 0
    for b in ["第 1 名", "第 2-3 名", "第 4-10 名", "第 11-30 名",
              "第 31-100 名", "100 名以外"]:
        c = buckets.get(b, 0)
        cum += c
        print(f"  {b:<14}{c:<6}{c/max(1,n):>7.1%}   累计 {cum/max(1,n):>6.1%}")

    # 纯语义上限
    within10 = sum(1 for r in per_q_best_rank if r <= 10)
    within30 = sum(1 for r in per_q_best_rank if r <= 30)

    print(f"\n② 纯语义 recall@10 上限: {within10}/{n} = {within10/n:.4f}")
    print(f"   纯语义 recall@30 上限: {within30}/{n} = {within30/n:.4f}")

    # ---------- 结论 ----------
    print("\n" + "=" * 94)
    print("结论")
    print("=" * 94)

    print(f"\n  纯语义 Recall@10 = {within10/n:.4f}")
    print(f"  实际 store.search Recall@10 = 0.5919（同 3 个对话）")

    delta = 0.5919 - within10 / n
    print(f"\n  多通道融合 vs 纯语义: {delta:+.4f}")

    print(f"\n  如果 embedding 完美（evidence 都排第 1）:")
    print(f"    理论上限 = 1.0000")
    print(f"    当前实际 = 0.5919")
    print(f"    差距     = {1 - 0.5919:.4f}")

    far = (buckets.get("第 31-100 名", 0) +
           buckets.get("100 名以外", 0)) / max(1, n)
    print(f"\n  排在 30 名以外的题: {far:.1%}")
    if far > 0.3:
        print(f"  → 这 {far:.0%} 的题，evidence 与问题的语义距离确实远")
        print(f"  → 换更强 embedding 或查询改写是唯一出路")

    out = {"experiment": "E-183", "n": n,
           "n_evidence": len(all_ranks),
           "buckets": dict(buckets),
           "semantic_recall_10": round(within10 / n, 4),
           "semantic_recall_30": round(within30 / n, 4),
           "actual": 0.5919,
           "far_ratio": round(far, 4)}
    with open(os.path.join(RESULTS, "e183_upper.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e183_upper.json')}")


if __name__ == "__main__":
    main()
