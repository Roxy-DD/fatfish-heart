"""
E-185 验证：是"语义不够"还是"排序不精"

E-184 的关键数据：
  所有证据余弦 >= 0.4（中位 0.6683）→ 语义匹配没问题
  但 recall@10 = 0.5466，recall@30 = 0.6680 → 差 12.1pp

假设：语义已经把证据放进 top-30，但排序精度不足，挤不进 top-10。

验证方法：
  ① 对每个"证据在 11-30 名"的题，检查证据与 top-10 的余弦差距
     如果差距 <0.02 → 纯排序噪声，加个精排就能捞回来
     如果差距 >0.1 → 语义确实弱
  ② 测一个简单精排：用问题+证据的**完整文本相似度**（而非单向量）
     看能否把 11-30 名的捞进前 10

先做 ①，量化差距有多大。
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e185")
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
    print("E-185 量化：证据与 top-10 门槛的余弦差距")
    print("=" * 94)

    gaps = []           # 证据余弦 vs 第 10 名余弦的差距
    cases = []

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

        id_of_mark, text_of = {}, {}
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
                    text_of[mark] = full
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

            # 每题最好的 evidence
            best_rank, best_i, best_mark = None, None, None
            for e in ev:
                eid = id_of_mark.get(e)
                if not eid or eid not in idx_of_id:
                    continue
                i = idx_of_id[eid]
                r = int((sims > sims[i]).sum()) + 1
                if best_rank is None or r < best_rank:
                    best_rank, best_i, best_mark = r, i, e

            if best_rank is None:
                continue

            # 只关心排在 11-30 的
            if not (11 <= best_rank <= 30):
                continue

            # 第 10 名的余弦
            order = np.argsort(-sims)
            tenth = float(sims[order[9]])
            evsim = float(sims[best_i])
            gap = tenth - evsim
            gaps.append(gap)

            if len(cases) < 8:
                cases.append({
                    "q": question, "mark": best_mark,
                    "rank": best_rank, "ev_sim": round(evsim, 4),
                    "tenth_sim": round(tenth, 4),
                    "gap": round(gap, 4),
                    "ev_text": text_of.get(best_mark, "")[:120],
                    "tenth_text": text_of.get(
                        mark_of_id(st, idx_of_id), "") if False else "",
                })

        st.con.close()

    # ---------- 汇总 ----------
    print(f"\n证据排在 11-30 名的题: {len(gaps)}")

    if not gaps:
        print("  无此类题")
        return

    import statistics
    print(f"\n  证据余弦 vs 第 10 名余弦的差距：")
    print(f"    中位数: {statistics.median(gaps):.4f}")
    print(f"    均值:   {statistics.mean(gaps):.4f}")
    print(f"    最小:   {min(gaps):.4f}")
    print(f"    最大:   {max(gaps):.4f}")

    buckets = Counter()
    for g in gaps:
        if g <= 0.01:
            buckets["<=0.01（几乎并列）"] += 1
        elif g <= 0.03:
            buckets["0.01-0.03"] += 1
        elif g <= 0.06:
            buckets["0.03-0.06"] += 1
        elif g <= 0.10:
            buckets["0.06-0.10"] += 1
        else:
            buckets[">0.10（差距明显）"] += 1

    print(f"\n  差距分布：")
    for b in ["<=0.01（几乎并列）", "0.01-0.03", "0.03-0.06",
              "0.06-0.10", ">0.10（差距明显）"]:
        c = buckets.get(b, 0)
        if c:
            print(f"    {b:<22}{c:<6}{c/len(gaps):.1%}")

    # ---------- 案例 ----------
    print(f"\n" + "=" * 94)
    print("典型案例")
    print("=" * 94)
    for c in cases[:4]:
        print(f"\n  Q: {c['q'][:66]}")
        print(f"  evidence [{c['mark']}] 排名 #{c['rank']}  "
              f"余弦 {c['ev_sim']:.4f}")
        print(f"    第 10 名余弦 {c['tenth_sim']:.4f}  差距 {c['gap']:.4f}")
        print(f"    证据原文: {c['ev_text'][:90]}")

    # ---------- 结论 ----------
    print("\n" + "=" * 94)
    print("结论")
    print("=" * 94)

    close = (buckets.get("<=0.01（几乎并列）", 0)
             + buckets.get("0.01-0.03", 0)) / len(gaps)
    far = buckets.get(">0.10（差距明显）", 0) / len(gaps)

    print(f"\n  差距 <=0.03（几乎并列）: {close:.1%}")
    print(f"  差距 >0.10（明显落后）:  {far:.1%}")

    if close > 0.4:
        print(f"\n  → ✅ 多数是「几乎并列」，加一层精排就能捞回来")
        print(f"  → 方向：精排（cross-encoder 或 LLM 判断），不是换 embedding")
    elif far > 0.4:
        print(f"\n  → 多数差距明显，语义确实不够")
        print(f"  → 方向：换 embedding 或查询改写")
    else:
        print(f"\n  → 两者都有，需要组合方案")

    out = {"experiment": "E-185", "n_gap": len(gaps),
           "gap_median": round(statistics.median(gaps), 4),
           "gap_mean": round(statistics.mean(gaps), 4),
           "buckets": dict(buckets),
           "close_ratio": round(close, 4),
           "far_ratio": round(far, 4)}
    with open(os.path.join(RESULTS, "e185_gap.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e185_gap.json')}")


def mark_of_id(st, i):
    return ""


if __name__ == "__main__":
    main()
