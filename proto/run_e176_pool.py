"""
E-176 解耦：把"候选池大小"从 top 参数中分离

E-174/E-175 确认的缺陷：
  top 同时控制三处：
    FTS5  LIMIT top*6
    LIKE  LIMIT top*2
    最终  [:top]
  导致 top 变大时 LIKE 扫描半径变大，噪声记忆被抬进前 10。

E-175 证明：不是关键词分压制语义（LIKE 最高 0.8 < 语义 1.36），
             而是候选池膨胀后，语义排名 11-50 的记忆被 LIKE 分抬上来。

修复方案：
  给 search 加独立参数 pool_factor（默认 2，与现状一致），
  让 LIKE 用固定小池，与 top 解耦。

对比：
  P1 现状：LIKE LIMIT = top*2
  P2 固定池：LIKE LIMIT = 20（不随 top 变）
  P3 更小池：LIKE LIMIT = 10
  P4 更大池：LIKE LIMIT = 40

只看 top=10 时的召回（因为实际使用就是 top=10）。
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e176")
os.makedirs(TASK, exist_ok=True)
RESULTS = os.path.join(_HERE, "results")
DATA = os.path.join(_HERE, "work", "e165", "locomo10.json")

K = 10
N_CONV = 3


def main():
    from store_v2 import MemoryStore
    from store_v1 import (Embedder, split_query_terms, detect_triggers,
                          estimate_affect_peak)
    import numpy as np

    data = json.load(open(DATA, encoding="utf-8"))

    print("=" * 94)
    print("E-176 解耦候选池大小与返回条数")
    print("=" * 94)
    print(f"\n跑前 {N_CONV} 个对话，K={K}\n")

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

    def run_with_pool(like_limit):
        """
        完全复刻 store.search，但 LIKE 的 LIMIT 用固定值。
        其它通道（FTS5 / 情绪 / 结构化关）保持与生产一致。
        """
        hit = total = 0
        for sid, db, qa, mark_of in all_data:
            st = MemoryStore(db)
            emb = Embedder.get()
            st._load_vectors(include_invalid=False)
            norms = np.linalg.norm(st._vec, axis=1)
            norms[norms == 0] = 1.0
            w = "AND e.invalid_at IS NULL"

            for q in qa:
                question = q.get("question") or ""
                ev = q.get("evidence") or []
                if not isinstance(ev, list):
                    ev = [ev]
                ev = [str(e).strip() for e in ev if e]
                if not question or not ev:
                    continue

                scores = {}
                # 语义
                nv = emb.encode(question)[0]
                sims = (st._vec @ nv) / norms
                for i, eid in enumerate(st._vec_ids):
                    scores[eid] = float(sims[i]) * 2.0

                terms = split_query_terms(question)
                if terms:
                    # FTS5（保持 top*6）
                    expr = " OR ".join('"%s"' % t.replace('"', '""')
                                       for t in terms if t)
                    if expr:
                        try:
                            for eid, s in st.con.execute(f"""
                                    SELECT e.ep_id, bm25(ep_fts) AS s
                                    FROM ep_fts JOIN episodes e
                                    ON e.rowid=ep_fts.rowid
                                    WHERE ep_fts MATCH ? {w}
                                    ORDER BY s LIMIT ?""",
                                    (expr, K * 6)).fetchall():
                                scores[eid] = (scores.get(eid, 0)
                                               + 1.0 / (1 + abs(s)))
                        except sqlite3.OperationalError:
                            pass
                    # LIKE（用传入的固定 limit）
                    for t in terms[:8]:
                        for (eid,) in st.con.execute(f"""
                                SELECT ep_id FROM episodes e
                                WHERE (raw_text LIKE ? OR atmosphere LIKE ?)
                                {w} LIMIT ?""",
                                (f"%{t}%", f"%{t}%", like_limit)).fetchall():
                            scores[eid] = scores.get(eid, 0) + 0.4

                # 情绪（保持）
                q_trig = set(detect_triggers(question))
                q_aff = estimate_affect_peak(question)
                for eid, trigs, peak, val in st.con.execute(f"""
                        SELECT ep_id, triggers, affect_peak, valence
                        FROM episodes e WHERE 1=1 {w}""").fetchall():
                    try:
                        tl = json.loads(trigs) if trigs else []
                    except Exception:
                        tl = []
                    add = 0.0
                    if q_trig and (q_trig & set(tl)):
                        add += 0.8 + 0.7 * (peak or 0)
                    if (q_aff["peak"] > 0.15
                            and (val or 0) * q_aff["valence"] > 0):
                        add += 0.5 * (peak or 0)
                    if add:
                        scores[eid] = scores.get(eid, 0) + add

                order = sorted(scores, key=lambda k: -scores[k])[:K]
                got = {mark_of.get(e) for e in order}
                got.discard(None)
                total += 1
                hit += 1 if any(e in got for e in ev) else 0

            st.con.close()
        return hit / max(1, total), hit, total

    print(f"{'LIKE 池大小':<18}{'Recall':<14}{'说明'}")
    print("-" * 94)

    res = {}
    for lim, note in [(K * 2, "现状（= top*2，随 top 变）"),
                      (10, "固定 10"),
                      (20, "固定 20"),
                      (40, "固定 40"),
                      (80, "固定 80"),
                      (999999, "不限制")]:
        r, h, t = run_with_pool(lim)
        res[str(lim)] = round(r, 4)
        print(f"{lim:<18}{f'{h}/{t}':<10}{r:<10.4f}{note}")

    # ---------- 结论 ----------
    print("\n" + "=" * 94)
    print("结论")
    print("=" * 94)

    best_lim = max(res, key=lambda k: res[k])
    print(f"\n  最优 LIKE 池大小: {best_lim} → {res[best_lim]:.4f}")
    print(f"  现状（20）: {res[str(K*2)]:.4f}")
    print(f"  提升: {res[best_lim] - res[str(K*2)]:+.4f}")

    if abs(res[best_lim] - res[str(K*2)]) > 0.02:
        print(f"\n  → ✅ 候选池大小显著影响召回，应解耦")
        print(f"  → 建议 LIKE 池固定为 {best_lim}")
    else:
        print(f"\n  → 候选池大小影响不显著（<2pp）")

    out = {"experiment": "E-176", "like_pool_results": res,
           "best": best_lim}
    with open(os.path.join(RESULTS, "e176_pool.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e176_pool.json')}")


if __name__ == "__main__":
    main()
