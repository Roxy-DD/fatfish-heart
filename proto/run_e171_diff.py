"""
E-171 定位：store.search 比"朴素两路打分"低 11pp 的原因

E-169/E-170 对照（同 3 个对话，495 条 QA）：
  store.search（含全部通道）      : 0.5919
  E-170 手写打分（语义+关键词）    : 0.7010
  差距                            : +10.91pp

按嫌疑排序逐个排查：
  C1  情绪通道（路 4）：给"affect 高"的记忆加分，但英文时 affect 全 0，
      理论上应该无影响 → 需验证
  C2  固定席位（fixed_slots=0）：默认关闭，应无影响
  C3  _expand（expand=False）：默认关闭，应无影响
  C4  关键词权重不同：store 用 +0.8/词，E-170 用 +0.5/词
  C5  语义权重：store 用 *2.0，E-170 也用 *2.0（相同）
  C6  store 的 top-K 截断时机 / 去重逻辑

方法：逐项开关，隔离出真正的差异源。
"""
from __future__ import annotations

import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e171")
os.makedirs(TASK, exist_ok=True)
RESULTS = os.path.join(_HERE, "results")
DATA = os.path.join(_HERE, "work", "e165", "locomo10.json")

K = 10
N_CONV = 2   # 少跑几个，快速定位


def main():
    from store_v2 import MemoryStore
    from store_v1 import Embedder, extract_elements, split_query_terms
    import numpy as np

    data = json.load(open(DATA, encoding="utf-8"))

    print("=" * 94)
    print("E-171 定位 store.search 的 11pp 性能损失")
    print("=" * 94)
    print(f"\n跑前 {N_CONV} 个对话\n")

    # 先建库（共用）
    all_data = []
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
        all_data.append((sid, db, qa, mark_of))

    def eval_with(fn, label):
        """用给定打分函数测召回"""
        hit = total = 0
        for sid, db, qa, mark_of in all_data:
            st = MemoryStore(db)
            for q in qa:
                question = q.get("question") or ""
                ev = q.get("evidence") or []
                if not isinstance(ev, list):
                    ev = [ev]
                ev = [str(e).strip() for e in ev if e]
                if not question or not ev:
                    continue
                order = fn(st, question)
                got = {mark_of.get(e) for e in order[:K]}
                got.discard(None)
                total += 1
                hit += 1 if any(e in got for e in ev) else 0
            st.con.close()
        rate = hit / max(1, total)
        print(f"  {label:<36}{hit}/{total} = {rate:.4f}")
        return rate

    def norm_key(text):
        return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (text or "").lower())).strip()

    # ---------- A: store.search 原样 ----------
    def f_A(st, q):
        res = st.search(q, top=K)
        return [t[0].ep_id for t in res]

    # ---------- B: 手写两路（语义×2.0 + 关键词×0.5），与 E-170 一致 ----------
    def f_B(st, q):
        emb = Embedder.get()
        st._load_vectors(include_invalid=False)
        norms = np.linalg.norm(st._vec, axis=1)
        norms[norms == 0] = 1.0
        nv = emb.encode(q)[0]
        sims = (st._vec @ nv) / norms
        scores = {eid: float(sims[i]) * 2.0
                  for i, eid in enumerate(st._vec_ids)}
        terms = [t.lower() for t in split_query_terms(q)]
        if terms:
            for i, eid in enumerate(st._vec_ids):
                ep = st.get(eid)
                if not ep:
                    continue
                tl = ep.raw_text.lower()
                hitn = sum(1 for w in terms if w in tl)
                if hitn:
                    scores[eid] += hitn * 0.5
        return sorted(scores, key=lambda k: -scores[k])

    # ---------- C: 两路 + 关键词权重 0.8（对齐 store）----------
    def f_C(st, q):
        emb = Embedder.get()
        st._load_vectors(include_invalid=False)
        norms = np.linalg.norm(st._vec, axis=1)
        norms[norms == 0] = 1.0
        nv = emb.encode(q)[0]
        sims = (st._vec @ nv) / norms
        scores = {eid: float(sims[i]) * 2.0
                  for i, eid in enumerate(st._vec_ids)}
        terms = [t.lower() for t in split_query_terms(q)]
        if terms:
            for i, eid in enumerate(st._vec_ids):
                ep = st.get(eid)
                if not ep:
                    continue
                tl = ep.raw_text.lower()
                hitn = sum(1 for w in terms if w in tl)
                if hitn:
                    scores[eid] += hitn * 0.8
        return sorted(scores, key=lambda k: -scores[k])

    # ---------- D: 纯语义 ----------
    def f_D(st, q):
        emb = Embedder.get()
        st._load_vectors(include_invalid=False)
        norms = np.linalg.norm(st._vec, axis=1)
        norms[norms == 0] = 1.0
        nv = emb.encode(q)[0]
        sims = (st._vec @ nv) / norms
        return [st._vec_ids[i] for i in np.argsort(-sims)]

    print(f"{'方案':<38}{'Recall'}")
    print("-" * 94)

    rA = eval_with(f_A, "A store.search 原样")
    rB = eval_with(f_B, "B 手写两路（语义2.0+关键词0.5）")
    rC = eval_with(f_C, "C 手写两路（语义2.0+关键词0.8）")
    rD = eval_with(f_D, "D 纯语义")

    # ---------- 分析 ----------
    print("\n" + "=" * 94)
    print("分析")
    print("=" * 94)

    print(f"\n  A store.search:        {rA:.4f}")
    print(f"  B 两路(0.5):           {rB:.4f}   vs A: {rB-rA:+.4f}")
    print(f"  C 两路(0.8):           {rC:.4f}   vs A: {rC-rA:+.4f}")
    print(f"  D 纯语义:              {rD:.4f}   vs A: {rD-rA:+.4f}")

    print(f"\n  定位：")
    if abs(rB - rC) > 0.02:
        print(f"    → 关键词权重是主因（B vs C 差 {rC-rB:+.4f}）")
    else:
        print(f"    → 关键词权重影响小（B vs C 差 {rC-rB:+.4f}）")

    if abs(rC - rA) > 0.02:
        print(f"    → 即使用相同权重，手写仍与 store 差 {rC-rA:+.4f}")
        print(f"    → 差异在 store 的其他通道或逻辑（情绪/固定席位/去重）")
    else:
        print(f"    → 两路手写与 store 接近（差 {rC-rA:+.4f}）")
        print(f"    → 之前的 11pp 差异主要来自关键词权重")

    if rD > rA:
        print(f"    → ⚠ 纯语义({rD:.4f}) 竟然优于 store({rA:.4f})")
        print(f"    → 说明 store 的多路融合在该语料上是负收益")

    out = {"experiment": "E-171", "n_conv": N_CONV,
           "store": round(rA, 4), "two_way_05": round(rB, 4),
           "two_way_08": round(rC, 4), "semantic_only": round(rD, 4)}
    with open(os.path.join(RESULTS, "e171_diff.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e171_diff.json')}")


if __name__ == "__main__":
    main()
