"""
E-175 量化：LIKE 通道的绝对加分是否淹没了语义信号

E-174 实测反直觉现象：
  内部 top=10 → 取前10 Recall 0.5919
  内部 top=50 → 取前10 Recall 0.3212（更差！）

假设：
  LIKE 通道每命中一个词 +0.4，但候选池随 top 放大而变大。
  top=50 时 LIMIT = 100 条/词，8 个词 → 最多 800 次加分。
  一条记忆若命中 6 个词，得 2.4 分 > 语义满分 2.0
  → 关键词分完全压制语义分 → 排序被噪声主导

验证：
  ① 统计不同 top 下，有多少条记忆拿到 LIKE 加分、平均加多少分
  ② 对比"语义分"与"关键词分"的量级
  ③ 若关键词分 >> 语义分，则必须归一化
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e175")
os.makedirs(TASK, exist_ok=True)
RESULTS = os.path.join(_HERE, "results")
DATA = os.path.join(_HERE, "work", "e165", "locomo10.json")


def main():
    from store_v2 import MemoryStore
    from store_v1 import Embedder, split_query_terms
    import numpy as np

    data = json.load(open(DATA, encoding="utf-8"))
    conv = data[0]
    qa = conv.get("qa") or []
    c = conv.get("conversation") or {}

    sessions = []
    j = 1
    while f"session_{j}" in c:
        if c[f"session_{j}"]:
            sessions.append((j, c.get(f"session_{j}_date_time", ""),
                             c[f"session_{j}"]))
        j += 1

    db = os.path.join(TASK, "locomo.db")
    for suf in ("", "-wal", "-shm"):
        p = db + suf
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass
    st = MemoryStore(db)
    for snum, dt, turns in sessions:
        for ti, turn in enumerate(turns, start=1):
            spk = turn.get("speaker") or ""
            txt = turn.get("text") or turn.get("content") or ""
            txt = re.sub(r"\s+", " ", str(txt)).strip()
            if len(txt) < 5:
                continue
            full = f"[{dt}] {spk}: {txt}" if dt else f"{spk}: {txt}"
            try:
                st.add(full[:800], resolve_conflict=False)
            except Exception:
                pass

    print("=" * 94)
    print("E-175 量化：LIKE 通道的加分是否淹没语义")
    print("=" * 94)
    print(f"\n库中 {st.count()} 条记忆")

    st._load_vectors(include_invalid=False)
    n_mem = len(st._vec_ids)
    norms = np.linalg.norm(st._vec, axis=1)
    norms[norms == 0] = 1.0
    emb = Embedder.get()

    # 取 5 条查询分析
    for qi, q in enumerate(qa[:5]):
        question = q.get("question") or ""
        if not question:
            continue

        terms = split_query_terms(question)

        # 语义分
        nv = emb.encode(question)[0]
        sims = (st._vec @ nv) / norms
        sem = {eid: float(sims[i]) * 2.0 for i, eid in enumerate(st._vec_ids)}

        print(f"\n{'=' * 94}")
        print(f"[{qi}] {question[:66]}")
        print(f"  关键词: {terms[:6]}")

        # 不同 top 下的 LIKE 加分
        print(f"\n  {'内部top':<10}{'LIKE加权条数':<16}{'最高LIKE分':<14}"
              f"{'语义最高':<12}{'压制?'}")
        print("  " + "-" * 70)

        for top in [10, 20, 30, 50]:
            like = {}
            for t in terms[:8]:
                rows = st.con.execute("""
                    SELECT ep_id FROM episodes e
                    WHERE (raw_text LIKE ? OR atmosphere LIKE ?)
                    AND e.invalid_at IS NULL LIMIT ?""",
                    (f"%{t}%", f"%{t}%", top * 2)).fetchall()
                for (eid,) in rows:
                    like[eid] = like.get(eid, 0) + 0.4

            max_like = max(like.values()) if like else 0
            max_sem = max(sem.values()) if sem else 0
            oppress = max_like > max_sem
            print(f"  {top:<10}{len(like):<16}{max_like:<14.2f}"
                  f"{max_sem:<12.2f}{'❌ 是' if oppress else '✅ 否'}")

    st.con.close()

    # ---------- 结论 ----------
    print("\n" + "=" * 94)
    print("结论")
    print("=" * 94)
    print(f"\n  语义通道上限: 2.0（相似度 1.0 × 2.0）")
    print(f"  LIKE 通道: 每词 +0.4，最多 {len(split_query_terms(qa[0].get('question', '')))} 词")
    print(f"  → 若一条记忆命中 5 个以上词，关键词分(>=2.0) 就超过语义满分")
    print(f"\n  这解释了 E-174 的反直觉现象：")
    print(f"    top 越大 → LIKE 扫描范围越大 → 更多记忆拿到高分 → 噪声压制语义")


if __name__ == "__main__":
    main()
