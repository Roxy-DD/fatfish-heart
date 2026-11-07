"""
E-180 单跳题诊断（0.5426，本该最容易）

E-179 结论：单跳题答案词在证据里覆盖率 0.673（高），
             说明单跳确实是「素材可读」的题，检索应该能找到。

但实测只有 0.5426。为什么？

按 E-163 的方法诊断：
  ① 失败的 evidence 在**全库余弦排名**的第几位？
     · 排名 1-10 却没进 top-10 → 排序/通道问题（可修）
     · 排名 >100 → 语义确实不匹配（难修）
  ② 失败题的 evidence 长度 vs 命中题
  ③ 是否有"同 session 内多条相似记忆"导致混淆

只用真实 store.search，不手写复现（E-173 教训）。
"""
from __future__ import annotations

import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e180")
os.makedirs(TASK, exist_ok=True)
RESULTS = os.path.join(_HERE, "results")
DATA = os.path.join(_HERE, "work", "e165", "locomo10.json")

K = 10


def main():
    from store_v2 import MemoryStore
    from store_v1 import Embedder
    import numpy as np

    data = json.load(open(DATA, encoding="utf-8"))

    print("=" * 94)
    print("E-180 单跳题失败诊断（真实 store.search）")
    print("=" * 94)

    from collections import Counter
    buckets = Counter()
    n_hit = n_fail = 0
    hit_lens, fail_lens = [], []
    cases = []

    emb = Embedder.get()

    for conv in data[:4]:      # 先跑 4 个对话
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

        mark_of, id_of_mark, len_of = {}, {}, {}
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
                    len_of[mark] = len(full)
                except Exception:
                    pass

        # 全库余弦（用于算真实排名）
        st._load_vectors(include_invalid=False)
        norms = np.linalg.norm(st._vec, axis=1)
        norms[norms == 0] = 1.0
        idx_of_id = {eid: i for i, eid in enumerate(st._vec_ids)}

        for q in qa:
            if str(q.get("category")) != "1":
                continue
            question = q.get("question") or ""
            ev = q.get("evidence") or []
            if not isinstance(ev, list):
                ev = [ev]
            ev = [str(e).strip() for e in ev if e]
            if not question or not ev:
                continue

            res = st.search(question, top=K)
            got = {mark_of.get(t[0].ep_id) for t in res}
            got.discard(None)
            hit = any(e in got for e in ev)

            if hit:
                n_hit += 1
                for e in ev:
                    if e in len_of:
                        hit_lens.append(len_of[e])
                        break
            else:
                n_fail += 1
                # 算 evidence 的余弦排名
                best_rank = None
                for e in ev:
                    eid = id_of_mark.get(e)
                    if not eid or eid not in idx_of_id:
                        continue
                    nv = emb.encode(question)[0]
                    sims = (st._vec @ nv) / norms
                    r = int((sims > sims[idx_of_id[eid]]).sum()) + 1
                    if best_rank is None or r < best_rank:
                        best_rank = r
                    if e in len_of:
                        fail_lens.append(len_of[e])
                        break

                if best_rank is None:
                    buckets["evidence 不在库"] += 1
                elif best_rank <= 3:
                    buckets["余弦前3（排序问题）"] += 1
                elif best_rank <= 10:
                    buckets["余弦4-10（被挤掉）"] += 1
                elif best_rank <= 50:
                    buckets["余弦11-50"] += 1
                elif best_rank <= 200:
                    buckets["余弦51-200"] += 1
                else:
                    buckets["余弦200+（语义远）"] += 1

                if len(cases) < 5 and best_rank and best_rank <= 10:
                    cases.append({
                        "sid": sid, "q": question, "ev": ev,
                        "rank": best_rank,
                    })

        st.con.close()

    # ---------- 汇总 ----------
    print(f"\n单跳题: 命中 {n_hit} / 失败 {n_fail}")

    print(f"\n① 失败题的 evidence 余弦排名分布")
    print("-" * 60)
    for b in ["余弦前3（排序问题）", "余弦4-10（被挤掉）", "余弦11-50",
              "余弦51-200", "余弦200+（语义远）", "evidence 不在库"]:
        c = buckets.get(b, 0)
        if c:
            print(f"  {b:<26}{c:<6}{c/max(1,n_fail):.1%}")

    print(f"\n② 长度对比")
    if hit_lens and fail_lens:
        import statistics
        print(f"  命中题 evidence 长度: 均值 {statistics.mean(hit_lens):.0f}")
        print(f"  失败题 evidence 长度: 均值 {statistics.mean(fail_lens):.0f}")

    print(f"\n③ 余弦前 10 却失败的案例")
    for c in cases[:4]:
        print(f"  [{c['sid']}] {c['q'][:60]}")
        print(f"    evidence: {c['ev']}  余弦排名 #{c['rank']}")

    # ---------- 结论 ----------
    print("\n" + "=" * 94)
    print("结论")
    print("=" * 94)

    nf = max(1, n_fail)
    top3 = buckets.get("余弦前3（排序问题）", 0) / nf
    top10 = buckets.get("余弦4-10（被挤掉）", 0) / nf
    far = buckets.get("余弦200+（语义远）", 0) / nf

    print(f"\n  余弦前 3 却失败: {top3:.1%}  ← 排序/bug，可修")
    print(f"  余弦 4-10 被挤掉: {top10:.1%}  ← 席位竞争，可修")
    print(f"  余弦 200+ : {far:.1%}  ← 语义远，难修")

    if top3 + top10 > 0.15:
        print(f"\n  → 有 {top3+top10:.1%} 的失败是「可修的排序问题」")
        print(f"  → 值得深入")
    elif far > 0.4:
        print(f"\n  → 多数是语义距离远（{far:.1%}），是本质难点")
        print(f"  → 检索层已接近边界")

    out = {"experiment": "E-180",
           "n_hit": n_hit, "n_fail": n_fail,
           "buckets": dict(buckets),
           "cases": cases}
    with open(os.path.join(RESULTS, "e180_single.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e180_single.json')}")


if __name__ == "__main__":
    main()
