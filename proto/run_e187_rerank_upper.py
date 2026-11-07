"""
E-187 量化精排空间：54 个可救回题的 evidence 实际排名

E-186b 发现：前 2 对话 302 题中，54 题"基线漏掉但证据在 top-30"。

本实验不依赖 LLM，直接算这 54 题的 evidence 在 top-30 里的精确排名，
回答一个关键问题：

  如果精排完美（永远选中证据），能救回多少？
  即：54 题的 evidence 排名的倒数（越靠前越容易救）。

这给出精排的**理论上限**，用来判断值不值得做。
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e187")
os.makedirs(TASK, exist_ok=True)
RESULTS = os.path.join(_HERE, "results")
DATA = os.path.join(_HERE, "work", "e165", "locomo10.json")

K = 10
POOL = 30
N_CONV = 2


def main():
    from store_v2 import MemoryStore
    from store_v1 import Embedder
    import numpy as np

    data = json.load(open(DATA, encoding="utf-8"))
    emb = Embedder.get()

    print("=" * 94)
    print("E-187 精排空间量化：可救回题的 evidence 排名")
    print("=" * 94)

    ranks = []     # 每个可救回题的最好 evidence 排名
    per_conv = Counter()

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

        id_of_mark = {}
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
                    id_of_mark[f"D{snum}:{ti}"] = ep.ep_id
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

            # 最好 evidence 的余弦排名
            best_rank = None
            for e in ev:
                eid = id_of_mark.get(e)
                if not eid or eid not in idx_of_id:
                    continue
                i = idx_of_id[eid]
                r = int((sims > sims[i]).sum()) + 1
                if best_rank is None or r < best_rank:
                    best_rank = r

            if best_rank is None:
                continue

            # 只保留"基线漏掉（rank>10）但在 pool 内（rank<=30）"
            if 11 <= best_rank <= POOL:
                ranks.append(best_rank)
                per_conv[sid] += 1

        st.con.close()

    # ---------- 汇总 ----------
    print(f"\n可救回题（11 <= rank <= {POOL}）: {len(ranks)}")
    print(f"  分布: {dict(per_conv)}")

    if not ranks:
        print("  无数据")
        return

    # 排名分布
    print(f"\n① evidence 排名分布（越靠前越容易精排救回）")
    print("-" * 60)
    buckets = Counter()
    for r in ranks:
        if 11 <= r <= 15:
            buckets["11-15"] += 1
        elif 16 <= r <= 20:
            buckets["16-20"] += 1
        elif 21 <= r <= 25:
            buckets["21-25"] += 1
        else:
            buckets["26-30"] += 1

    for b in ["11-15", "16-20", "21-25", "26-30"]:
        c = buckets.get(b, 0)
        print(f"  {b:<10}{c:<6}{c/len(ranks):.1%}")

    # 中位数
    import statistics
    med = statistics.median(ranks)
    print(f"\n  中位排名: {med}")

    # ---------- 理论上限 ----------
    print("\n" + "=" * 94)
    print("理论上限")
    print("=" * 94)

    # 如果精排完美，能救回全部 54 题 → 基线 114 + 54 = 168
    # 但如果精排只能救回"排名靠前"的一部分，则按比例
    n_rec = len(ranks)
    base = 114
    total = 302

    print(f"\n  基线: {base}/{total} = {base/total:.4f}")
    print(f"  精排完美上限: ({base + n_rec})/{total} = "
          f"{(base + n_rec)/total:.4f}  (+{n_rec/total:.4f})")

    # 现实预期：精排只能救回排名前 15 的
    n_15 = sum(1 for r in ranks if r <= 15)
    print(f"  精排救回排名<=15: ({base + n_15})/{total} = "
          f"{(base + n_15)/total:.4f}  (+{n_15/total:.4f})")

    print(f"\n  结论：")
    print(f"    · 精排的完美上限是 +{n_rec/total:.1%}（{n_rec} 题）")
    print(f"    · 若 LLM 只能救回排名前 15 的，实际 +{n_15/total:.1%}")
    print(f"    · 精排是「锦上添花」，不是「翻倍」")

    out = {"experiment": "E-187", "n_recoverable": n_rec,
           "rank_buckets": dict(buckets),
           "median_rank": med,
           "baseline": base, "total": total,
           "perfect_upper": round((base + n_rec) / total, 4),
           "realistic_upper": round((base + n_15) / total, 4)}
    with open(os.path.join(RESULTS, "e187_rerank_upper.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e187_rerank_upper.json')}")


if __name__ == "__main__":
    main()
