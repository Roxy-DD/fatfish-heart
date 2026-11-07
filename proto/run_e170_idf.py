"""
E-170 IDF 加权的结构化通道：验证是否优于"固定加分"和"完全关闭"

E-169 实测：
  结构化固定加分 0.8  → 0.5495
  完全关闭            → 0.5919（更好）
  差 -4.24pp

根因：LoCoMo 里说话人名字覆盖率 100%，固定加分对所有记忆等值，
     反而夸大了含多实体长文本的分数。

本实验对比三种方案：
  S0 关闭结构化（基线，0.5919 参考值）
  S1 固定加分 0.8（原方案）
  S2 IDF 加权：加分 = 0.8 * idf(entity)
     idf(entity) = log(N / df) / log(N)
     出现率 100% 的实体 → idf≈0 → 几乎不加分
     出现率 1% 的实体   → idf≈1 → 加满分

如果 S2 > S0，说明 IDF 加权能把"高频实体噪声"变成"低频实体信号"。
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e170")
os.makedirs(TASK, exist_ok=True)
RESULTS = os.path.join(_HERE, "results")
DATA = os.path.join(_HERE, "work", "e165", "locomo10.json")

K = 10


def main():
    from store_v2 import MemoryStore
    from store_v1 import Embedder, extract_elements, split_query_terms
    import numpy as np

    data = json.load(open(DATA, encoding="utf-8"))
    N_CONV = 3

    print("=" * 94)
    print("E-170 IDF 加权的结构化通道")
    print("=" * 94)
    print(f"\n跑前 {N_CONV} 个对话\n")

    results = {}

    for mode in ["S0_off", "S1_fixed", "S2_idf"]:
        hit = total = 0

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

            db = os.path.join(TASK, f"{sid}_{mode}.db")
            for suf in ("", "-wal", "-shm"):
                p = db + suf
                if os.path.exists(p):
                    try:
                        os.remove(p)
                    except Exception:
                        pass
            st = MemoryStore(db)

            # 写记忆，同时记录每条的实体
            mark_of = {}
            ents_of = {}     # ep_id -> set(entities)
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
                        el = extract_elements(full)
                        ents = set()
                        for k in ("person", "time", "location"):
                            for v in (el.get(k) or []):
                                ents.add(f"{k}:{v.lower()}")
                        ents_of[ep.ep_id] = ents
                    except Exception:
                        pass

            n_mem = len(mark_of)

            # 算 df（每个实体出现在多少条记忆里）
            df = Counter()
            for ents in ents_of.values():
                for e in ents:
                    df[e] += 1

            def idf(e):
                d = df.get(e, 0)
                if d <= 0:
                    return 0.0
                return max(0.0, math.log(n_mem / d) / math.log(max(2, n_mem)))

            # 逐题评测：自己实现打分，不依赖 store.search
            emb = Embedder.get()
            st._load_vectors(include_invalid=False)
            norms = np.linalg.norm(st._vec, axis=1)
            norms[norms == 0] = 1.0

            for q in qa:
                question = q.get("question") or ""
                ev = q.get("evidence") or []
                if not isinstance(ev, list):
                    ev = [ev]
                ev = [str(e).strip() for e in ev if e]
                if not question or not ev:
                    continue

                # 语义分
                nv = emb.encode(question)[0]
                sims = (st._vec @ nv) / norms
                scores = {}
                for i, eid in enumerate(st._vec_ids):
                    scores[eid] = float(sims[i]) * 2.0

                # 关键词分
                terms = [t.lower() for t in split_query_terms(question)]
                if terms:
                    for i, eid in enumerate(st._vec_ids):
                        ep = st.get(eid)
                        if not ep:
                            continue
                        tl = ep.raw_text.lower()
                        hitn = sum(1 for w in terms if w in tl)
                        if hitn:
                            scores[eid] = scores.get(eid, 0) + hitn * 0.5

                # 结构化分
                if mode != "S0_off":
                    q_el = extract_elements(question)
                    q_ents = set()
                    for k in ("person", "time", "location"):
                        for v in (q_el.get(k) or []):
                            q_ents.add(f"{k}:{v.lower()}")
                    if q_ents:
                        for eid in st._vec_ids:
                            mine = ents_of.get(eid, set())
                            common = q_ents & mine
                            if not common:
                                continue
                            if mode == "S1_fixed":
                                scores[eid] = scores.get(eid, 0) + 0.8
                            else:  # S2_idf
                                # 加分 = 0.8 * 平均 idf，且要求至少一个稀有实体
                                w = sum(idf(e) for e in common) / len(common)
                                if w > 0:
                                    scores[eid] = scores.get(eid, 0) + 0.8 * w

                order = sorted(scores, key=lambda k: -scores[k])[:K]
                got = {mark_of.get(e) for e in order}
                got.discard(None)
                total += 1
                hit += 1 if any(e in got for e in ev) else 0

            st.con.close()

        rate = hit / max(1, total)
        results[mode] = {"hit": hit, "total": total, "rate": round(rate, 4)}
        print(f"  {mode:<12} {hit}/{total} = {rate:.4f}")

    # ---------- 对比 ----------
    print("\n" + "=" * 94)
    print("对比")
    print("=" * 94)

    s0 = results["S0_off"]["rate"]
    s1 = results["S1_fixed"]["rate"]
    s2 = results["S2_idf"]["rate"]

    print(f"\n{'方案':<24}{'Recall':<12}{'vs 关闭'}")
    print("-" * 60)
    print(f"{'S0 关闭结构化':<24}{s0:<12.4f}{'—'}")
    print(f"{'S1 固定加分 0.8':<24}{s1:<12.4f}{s1-s0:+.4f}")
    print(f"{'S2 IDF 加权':<24}{s2:<12.4f}{s2-s0:+.4f}")

    print("\n" + "=" * 94)
    print("结论")
    print("=" * 94)

    best = max([("S0_off", s0), ("S1_fixed", s1), ("S2_idf", s2)],
               key=lambda x: x[1])
    print(f"\n  最优方案: {best[0]}（{best[1]:.4f}）")

    if s2 > s0 + 0.01:
        print(f"\n  → ✅ IDF 加权优于关闭（+{s2-s0:.4f}）")
        print(f"    → 低频实体确实提供了区分信号")
    elif abs(s2 - s0) <= 0.01:
        print(f"\n  → IDF 加权与关闭基本持平（{s2-s0:+.4f}）")
        print(f"    → 说明 LoCoMo 里实体匹配的收益被其他因素抵消")
    else:
        print(f"\n  → ❌ IDF 加权仍不如关闭（{s2-s0:+.4f}）")
        print(f"    → 结构化通道整体应该关闭")

    out = {"experiment": "E-170", "n_conv": N_CONV,
           "results": results,
           "best": best[0]}
    with open(os.path.join(RESULTS, "e170_idf.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e170_idf.json')}")


if __name__ == "__main__":
    main()
