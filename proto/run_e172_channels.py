"""
E-172 精确定位：store.search 的哪一段导致 17.9pp 损失

E-171 结果：
  store.search  0.5099
  手写两路      0.6887  (+17.88pp)
  纯语义        0.5728  (+6.29pp)

已排除：关键词权重（0.5 vs 0.8 结果相同）

剩余嫌疑（按 store.search 代码顺序）：
  R1  FTS5 bm25 通道（1.0/(1+|s|)）
  R2  LIKE 模糊匹配（每词 +0.4，LIMIT top*2）
  R3  结构化通道（+0.8）—— E-169 已证有害
  R4  情绪通道（q_aff / triggers）
  R5  固定席位（默认 0，应无影响）

方法：在 store.search 里逐段注释掉，看指标变化。
不修改生产代码，而是复制一份 search 到测试里，逐段开关。
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e172")
os.makedirs(TASK, exist_ok=True)
RESULTS = os.path.join(_HERE, "results")
DATA = os.path.join(_HERE, "work", "e165", "locomo10.json")

K = 10
N_CONV = 2


def main():
    from store_v2 import MemoryStore
    from store_v1 import (Embedder, extract_elements, split_query_terms,
                          detect_triggers, estimate_affect_peak)
    import numpy as np

    data = json.load(open(DATA, encoding="utf-8"))

    print("=" * 94)
    print("E-172 逐段定位 store.search 的性能损失")
    print("=" * 94)

    # 建库
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

    def run_custom(st, query, use_fts, use_like, use_struct,
                   use_affect, top=K):
        """
        复制 store.search 的打分逻辑，逐段可开关。
        """
        scores = {}
        emb = Embedder.get()

        # (1) 语义
        st._load_vectors(include_invalid=False)
        if st._vec is not None and len(st._vec):
            norms = np.linalg.norm(st._vec, axis=1)
            norms[norms == 0] = 1.0
            nv = emb.encode(query)[0]
            sims = (st._vec @ nv) / norms
            for i, eid in enumerate(st._vec_ids):
                scores[eid] = float(sims[i]) * 2.0

        terms = split_query_terms(query)

        # (2a) FTS5 bm25
        if use_fts and terms:
            expr = " OR ".join('"%s"' % t.replace('"', '""')
                               for t in terms if t)
            if expr:
                try:
                    rows = st.con.execute("""
                        SELECT e.ep_id, bm25(ep_fts) AS s
                        FROM ep_fts JOIN episodes e ON e.rowid=ep_fts.rowid
                        WHERE ep_fts MATCH ? AND e.invalid_at IS NULL
                        ORDER BY s LIMIT ?""",
                        (expr, top * 6)).fetchall()
                    for eid, s in rows:
                        scores[eid] = scores.get(eid, 0) + 1.0 / (1 + abs(s))
                except sqlite3.OperationalError:
                    pass

        # (2b) LIKE 模糊
        if use_like and terms:
            for t in terms[:8]:
                rows = st.con.execute("""
                    SELECT ep_id FROM episodes e
                    WHERE (raw_text LIKE ? OR atmosphere LIKE ?)
                    AND e.invalid_at IS NULL LIMIT ?""",
                    (f"%{t}%", f"%{t}%", top * 2)).fetchall()
                for (eid,) in rows:
                    scores[eid] = scores.get(eid, 0) + 0.4

        # (3) 结构化
        if use_struct:
            el = extract_elements(query)
            for fname in ("person", "time", "location"):
                for v in el[fname]:
                    rows = st.con.execute(f"""
                        SELECT ep_id FROM episodes e
                        WHERE {fname} LIKE ? AND e.invalid_at IS NULL LIMIT ?""",
                        (f"%{v}%", top * 2)).fetchall()
                    for (eid,) in rows:
                        scores[eid] = scores.get(eid, 0) + 0.8

        # (4) 情绪
        if use_affect:
            q_trig = set(detect_triggers(query))
            q_aff = estimate_affect_peak(query)
            rows = st.con.execute("""
                SELECT ep_id, triggers, affect_peak, valence FROM episodes e
                WHERE e.invalid_at IS NULL""").fetchall()
            for eid, trigs, peak, val in rows:
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
        return order[:top]

    # ---------- 逐段开关 ----------
    CONFIGS = {
        "全开(等于 store)":   dict(use_fts=True, use_like=True,
                                   use_struct=True, use_affect=True),
        "关情绪":             dict(use_fts=True, use_like=True,
                                   use_struct=True, use_affect=False),
        "关结构化":           dict(use_fts=True, use_like=True,
                                   use_struct=False, use_affect=True),
        "关FTS5":             dict(use_fts=False, use_like=True,
                                   use_struct=True, use_affect=True),
        "关LIKE模糊":         dict(use_fts=True, use_like=False,
                                   use_struct=True, use_affect=True),
        "只语义+FTS5":        dict(use_fts=True, use_like=False,
                                   use_struct=False, use_affect=False),
        "只语义+LIKE":        dict(use_fts=False, use_like=True,
                                   use_struct=False, use_affect=False),
        "只语义":             dict(use_fts=False, use_like=False,
                                   use_struct=False, use_affect=False),
    }

    print(f"\n{'配置':<22}{'Recall':<14}{'相对全开'}")
    print("-" * 94)

    base_rate = None
    res = {}

    for name, cfg in CONFIGS.items():
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
                order = run_custom(st, question, **cfg)
                got = {mark_of.get(e) for e in order}
                got.discard(None)
                total += 1
                hit += 1 if any(e in got for e in ev) else 0
            st.con.close()

        rate = hit / max(1, total)
        if base_rate is None:
            base_rate = rate
        res[name] = round(rate, 4)
        print(f"{name:<22}{f'{hit}/{total}':<10}{rate:<10.4f}"
              f"{rate - base_rate:+.4f}")

    # ---------- 结论 ----------
    print("\n" + "=" * 94)
    print("结论")
    print("=" * 94)

    best = max(res, key=lambda k: res[k])
    print(f"\n  最优配置: {best}（{res[best]:.4f}）")
    print(f"  全开配置: {res['全开(等于 store)']:.4f}")
    print(f"  提升: {res[best] - res['全开(等于 store)']:+.4f}")

    print(f"\n  各通道的贡献（关掉后的变化）：")
    for k in ["关情绪", "关结构化", "关FTS5", "关LIKE模糊"]:
        if k in res:
            d = res[k] - res["全开(等于 store)"]
            verdict = "有害" if d > 0.01 else ("有用" if d < -0.01 else "中性")
            print(f"    {k:<12}{d:+.4f}  → {verdict}")

    out = {"experiment": "E-172", "results": res, "best": best}
    with open(os.path.join(RESULTS, "e172_channels.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e172_channels.json')}")


if __name__ == "__main__":
    main()
