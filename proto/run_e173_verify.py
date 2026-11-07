"""
E-173 精确对比：store.search 与手写复现的 top-10 是否一致

E-171/E-172 的矛盾：
  E-171 store.search          = 0.5099
  E-172 "全开（等于 store）"   = 0.3444
  两者应该等价，却差 16.5pp

可能原因：
  R1  我的复现漏了某个通道
  R2  LIKE 通道的 LIMIT 顺序导致结果集不同
  R3  语义分初始化方式不同
  R4  store.search 有额外的去重/截断

方法：
  取 5 条查询，逐条对比 store.search 与手写版的 top-10 ep_id 列表，
  看差在哪一位，从而定位到底漏了什么。
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e173")
os.makedirs(TASK, exist_ok=True)
RESULTS = os.path.join(_HERE, "results")
DATA = os.path.join(_HERE, "work", "e165", "locomo10.json")

K = 10


def main():
    from store_v2 import MemoryStore
    from store_v1 import (Embedder, extract_elements, split_query_terms,
                          detect_triggers, estimate_affect_peak)
    import numpy as np

    data = json.load(open(DATA, encoding="utf-8"))
    conv = data[0]
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

    text_of = {}
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
                text_of[ep.ep_id] = full
            except Exception:
                pass

    print("=" * 94)
    print("E-173 精确对比 store.search 与手写复现")
    print("=" * 94)
    print(f"\n库中 {len(text_of)} 条记忆，取前 5 条查询对比\n")

    def hand_roll(query, use_struct=False):
        """完全按 store.search 的逻辑复现"""
        scores = {}
        emb = Embedder.get()
        w = "AND e.invalid_at IS NULL"

        # (1) 语义
        st._load_vectors(include_invalid=False)
        if st._vec is not None and len(st._vec):
            norms = np.linalg.norm(st._vec, axis=1)
            norms[norms == 0] = 1.0
            nv = emb.encode(query)[0]
            sims = (st._vec @ nv) / norms
            for i, eid in enumerate(st._vec_ids):
                scores[eid] = float(sims[i]) * 2.0

        # (2) FTS5 + LIKE
        terms = split_query_terms(query)
        if terms:
            expr = " OR ".join('"%s"' % t.replace('"', '""')
                               for t in terms if t)
            if expr:
                try:
                    for eid, s in st.con.execute(f"""
                            SELECT e.ep_id, bm25(ep_fts) AS s
                            FROM ep_fts JOIN episodes e ON e.rowid=ep_fts.rowid
                            WHERE ep_fts MATCH ? {w} ORDER BY s LIMIT ?""",
                            (expr, K * 6)).fetchall():
                        scores[eid] = scores.get(eid, 0) + 1.0 / (1 + abs(s))
                except sqlite3.OperationalError:
                    pass
            for t in terms[:8]:
                for (eid,) in st.con.execute(f"""
                        SELECT ep_id FROM episodes e
                        WHERE (raw_text LIKE ? OR atmosphere LIKE ?) {w} LIMIT ?""",
                        (f"%{t}%", f"%{t}%", K * 2)).fetchall():
                    scores[eid] = scores.get(eid, 0) + 0.4

        # (3) 结构化
        if use_struct:
            el = extract_elements(query)
            for fname in ("person", "time", "location"):
                for v in el[fname]:
                    for (eid,) in st.con.execute(f"""
                            SELECT ep_id FROM episodes e
                            WHERE {fname} LIKE ? {w} LIMIT ?""",
                            (f"%{v}%", K * 2)).fetchall():
                        scores[eid] = scores.get(eid, 0) + 0.8

        # (4) 情绪
        q_trig = set(detect_triggers(query))
        q_aff = estimate_affect_peak(query)
        for eid, trigs, peak, val in st.con.execute(f"""
                SELECT ep_id, triggers, affect_peak, valence FROM episodes e
                WHERE 1=1 {w}""").fetchall():
            try:
                tl = json.loads(trigs) if trigs else []
            except Exception:
                tl = []
            add = 0.0
            if q_trig and (q_trig & set(tl)):
                add += 0.8 + 0.7 * (peak or 0)
            if q_aff["peak"] > 0.15 and (val or 0) * q_aff["valence"] > 0:
                add += 0.5 * (peak or 0)
            if add:
                scores[eid] = scores.get(eid, 0) + add

        order = sorted(scores, key=lambda k: -scores[k])
        return order[:K], scores

    n_same = 0
    for qi, q in enumerate(qa[:5]):
        question = q.get("question") or ""
        if not question:
            continue

        # store.search
        res = st.search(question, top=K)
        store_ids = [t[0].ep_id for t in res]
        store_scores = {t[0].ep_id: (t[1] if len(t) > 1 else 0) for t in res}

        # 手写
        hand_ids, hand_scores = hand_roll(question)

        same = store_ids == hand_ids
        n_same += 1 if same else 0

        print(f"\n{'=' * 94}")
        print(f"[{qi}] {question[:70]}")
        print(f"  store.search top-3:")
        for eid in store_ids[:3]:
            print(f"    score={store_scores.get(eid, 0):.3f}  "
                  f"{text_of.get(eid, '')[:64]}")
        print(f"  手写 top-3:")
        for eid in hand_ids[:3]:
            print(f"    score={hand_scores.get(eid, 0):.3f}  "
                  f"{text_of.get(eid, '')[:64]}")
        print(f"  完全一致: {'✅' if same else '❌'}")

        if not same:
            only_store = set(store_ids) - set(hand_ids)
            only_hand = set(hand_ids) - set(store_ids)
            print(f"    仅 store 有: {len(only_store)}")
            print(f"    仅手写有:   {len(only_hand)}")

    st.con.close()

    print("\n" + "=" * 94)
    print("结论")
    print("=" * 94)
    print(f"\n  {n_same}/5 条查询的 top-10 完全一致")
    if n_same == 5:
        print(f"  → ✅ 复现正确，E-172 的结论可信")
    else:
        print(f"  → ❌ 复现不一致，E-172 的结论需重新验证")


if __name__ == "__main__":
    main()
