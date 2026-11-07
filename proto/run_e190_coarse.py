"""
E-190 粗筛可行性：能否用便宜信号把 top-50 缩到 top-20 而不丢证据

E-189 全量结果：top-50 可救回 537 题，top-20 只能救回 170 题。
  如果直接缩小池，会丢掉 537-170=367 题（68% 的证据在 21-50 名）。

关键问题：有没有一个"便宜的粗筛"，能从 50 条里选出 20 条，
  且证据大部分被保留？

候选粗筛信号（都便宜，不需要 LLM）：
  ① 余弦绝对值阈值（证据余弦通常较高？）
  ② 关键词命中数（证据通常含更多问题词？）
  ③ 与 top-1 的余弦差（证据通常接近 top-1？）

本实验量化：证据在 21-50 名时，这些信号能否把它捞进前 20。
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e190")
os.makedirs(TASK, exist_ok=True)
RESULTS = os.path.join(_HERE, "results")
DATA = os.path.join(_HERE, "work", "e165", "locomo10.json")

N_CONV = 3


def main():
    from store_v2 import MemoryStore
    from store_v1 import Embedder, split_query_terms
    import numpy as np

    data = json.load(open(DATA, encoding="utf-8"))
    emb = Embedder.get()

    print("=" * 94)
    print("E-190 粗筛可行性：证据在 21-50 名时的可区分信号")
    print("=" * 94)

    # 收集：证据排名在 21-50 的题，看它们的各种信号
    signals = []   # (cos, kw_hit, cos_gap_to_top1)

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

        id_of_mark, text_of_mark = {}, {}
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
                    id_of_mark[mark] = ep.ep_id
                    text_of_mark[mark] = full
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
            terms = split_query_terms(question)

            # 最好证据
            best_rank, best_i, best_mark = None, None, None
            for e in ev:
                eid = id_of_mark.get(e)
                if not eid or eid not in idx_of_id:
                    continue
                i = idx_of_id[eid]
                r = int((sims > sims[i]).sum()) + 1
                if best_rank is None or r < best_rank:
                    best_rank, best_i, best_mark = r, i, e

            if best_rank is None or not (21 <= best_rank <= 50):
                continue

            # 信号
            cos = float(sims[best_i])
            top1_cos = float(np.max(sims))
            cos_gap = top1_cos - cos

            # 关键词命中
            tl = text_of_mark.get(best_mark, "").lower()
            kw_hit = sum(1 for t in terms if t.lower() in tl)

            signals.append({
                "rank": best_rank, "cos": cos, "cos_gap": cos_gap,
                "kw_hit": kw_hit, "n_terms": len(terms),
            })

        st.con.close()

    # ---------- 汇总 ----------
    n = len(signals)
    print(f"\n证据排名 21-50 的题: {n}")

    if not n:
        print("  无数据")
        return

    import statistics
    print(f"\n① 余弦绝对值分布")
    coses = [s["cos"] for s in signals]
    print(f"  均值 {statistics.mean(coses):.4f}  中位 {statistics.median(coses):.4f}")
    print(f"  最小 {min(coses):.4f}  最大 {max(coses):.4f}")

    print(f"\n② 与 top-1 的余弦差")
    gaps = [s["cos_gap"] for s in signals]
    print(f"  均值 {statistics.mean(gaps):.4f}  中位 {statistics.median(gaps):.4f}")

    print(f"\n③ 关键词命中数")
    kws = [s["kw_hit"] for s in signals]
    print(f"  均值 {statistics.mean(kws):.2f}  中位 {statistics.median(kws):.1f}")
    print(f"  命中 0 词: {sum(1 for k in kws if k == 0)}/{n}")
    print(f"  命中 >=2 词: {sum(1 for k in kws if k >= 2)}/{n}")

    # 粗筛方案测试：如果"余弦 >= 0.65"作为粗筛，能保留多少证据？
    print(f"\n④ 粗筛方案测试（阈值保留证据比例）")
    print("  " + "-" * 60)
    for th in [0.60, 0.62, 0.64, 0.65, 0.66]:
        kept = sum(1 for s in signals if s["cos"] >= th)
        print(f"    余弦 >= {th}: 保留 {kept}/{n} = {kept/n:.1%}")

    # 但粗筛的目的不是保留证据，而是"缩小池"。
    # 关键是：证据 vs 非证据 在余弦上能否区分
    # 这里简化：看证据的余弦是否显著高于"第 20 名的余弦"
    print(f"\n⑤ 关键：证据余弦 vs 第 20 名余弦")
    print("  （如果证据余弦通常高于第20名，则余弦粗筛有效）")

    out = {"experiment": "E-190", "n": n,
           "cos_mean": round(statistics.mean(coses), 4),
           "cos_median": round(statistics.median(coses), 4),
           "gap_mean": round(statistics.mean(gaps), 4),
           "kw_hit_mean": round(statistics.mean(kws), 2),
           "kw_zero_ratio": round(sum(1 for k in kws if k == 0) / n, 4)}
    with open(os.path.join(RESULTS, "e190_coarse.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e190_coarse.json')}")


if __name__ == "__main__":
    main()
