"""E-24 检索性能剖析：逐通道计时，定位瓶颈

E-23 发现：检索延迟线性增长（100条55ms → 2000条1383ms）
写入稳定（11-12ms/条），说明问题在检索路径。

store.search 的通道：
  (1) 语义      self._vec @ qv         矩阵乘法，O(N*512)
  (2) FTS5      bm25(ep_fts)          有 FTS 索引
  (3) LIKE      raw_text LIKE '%t%'    ← 怀疑：全表扫描，无索引
  (4) 结构化    person/time/location   ← 怀疑：全表扫描
  (5) 情绪      全表扫描 triggers     ← 怀疑：全表扫描

本实验逐通道计时，精确定位。
"""
import sys
import os
import time
import sqlite3

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))

from store_v2 import MemoryStore
from store_v1 import Embedder, split_query_terms, extract_elements

DB = os.path.join(_HERE, "work", "e23_2000.db")


def timeit(fn, n=5):
    """跑 n 次取平均（毫秒）"""
    fn()  # 预热
    t0 = time.perf_counter()
    for _ in range(n):
        r = fn()
    return (time.perf_counter() - t0) / n * 1000, r


def main():
    print("=" * 82)
    print("E-24 检索性能剖析")
    print("=" * 82)

    if not os.path.exists(DB):
        print(f"缺少 {DB}，先跑 E-23")
        return

    st = MemoryStore(DB)
    n = st.count()
    print(f"\n库中记忆: {n} 条")

    q = "登录密码改成 9999 了吗"
    print(f"查询: {q}\n")

    terms = split_query_terms(q)
    print(f"拆词: {terms[:8]}\n")

    # ---------- (1) 语义通道 ----------
    import numpy as np
    st._load_vectors(include_invalid=False)
    print(f"向量矩阵: {st._vec.shape}")

    def sem():
        qv = Embedder.get().encode(q)[0]
        sims = st._vec @ qv
        k = min(40, len(sims))
        return list(np.argpartition(-sims, k - 1)[:k])

    ms_sem, _ = timeit(sem)
    print(f"(1) 语义通道:        {ms_sem:8.2f} ms")

    # ---------- (2) FTS5 ----------
    expr = " OR ".join(f'"{t}"' for t in terms)

    def fts():
        return st.con.execute(
            "SELECT e.ep_id, bm25(ep_fts) FROM ep_fts JOIN episodes e "
            "ON e.rowid=ep_fts.rowid WHERE ep_fts MATCH ? "
            "AND e.invalid_at IS NULL ORDER BY bm25(ep_fts) LIMIT 60",
            (expr,)).fetchall()

    try:
        ms_fts, fts_res = timeit(fts)
        print(f"(2) FTS5 通道:       {ms_fts:8.2f} ms  ({len(fts_res)} 行)")
    except sqlite3.OperationalError as e:
        print(f"(2) FTS5 通道:       失败 {e}")
        ms_fts = 0

    # ---------- (3) LIKE 通道（怀疑点） ----------
    def like_channel():
        out = []
        for t in terms[:8]:
            out += st.con.execute(
                "SELECT ep_id FROM episodes WHERE (raw_text LIKE ? OR atmosphere LIKE ?) "
                "AND invalid_at IS NULL LIMIT 20",
                (f"%{t}%", f"%{t}%")).fetchall()
        return out

    ms_like, like_res = timeit(like_channel)
    print(f"(3) LIKE 通道:       {ms_like:8.2f} ms  ({len(like_res)} 行)  ← 怀疑")

    # 单次 LIKE 耗时
    t0 = time.perf_counter()
    for _ in range(10):
        st.con.execute("SELECT ep_id FROM episodes WHERE raw_text LIKE ? LIMIT 20",
                       (f"%{terms[0]}%",)).fetchall()
    ms_like_one = (time.perf_counter() - t0) / 10 * 1000
    print(f"     单次 LIKE 全表扫描: {ms_like_one:8.2f} ms × {len(terms[:8])} 词 "
          f"= {ms_like_one * len(terms[:8]):.1f} ms")

    # ---------- (4) 结构化通道 ----------
    el = extract_elements(q)
    has_el = bool(el["person"] or el["time"] or el["location"])
    if has_el:
        def struct():
            out = []
            for fname in ("person", "time", "location"):
                for v in el[fname]:
                    out += st.con.execute(
                        f"SELECT ep_id FROM episodes WHERE {fname} LIKE ? "
                        f"AND invalid_at IS NULL LIMIT 20",
                        (f"%{v}%",)).fetchall()
            return out

        ms_struct, _ = timeit(struct)
        print(f"(4) 结构化通道:      {ms_struct:8.2f} ms")
    else:
        ms_struct = 0
        print(f"(4) 结构化通道:      跳过（查询无结构化元素）")

    # ---------- (5) 情绪通道 ----------
    from store_v1 import detect_triggers, estimate_affect_peak

    def affect_channel():
        q_trig = set(detect_triggers(q))
        q_aff = estimate_affect_peak(q)
        rows = st.con.execute(
            "SELECT ep_id, triggers, affect_peak, valence FROM episodes "
            "WHERE invalid_at IS NULL").fetchall()
        out = []
        for eid, trigs, peak, val in rows:
            if q_trig or q_aff["peak"] > 0.15:
                out.append(eid)
        return out

    ms_aff, aff_res = timeit(affect_channel)
    print(f"(5) 情绪通道:        {ms_aff:8.2f} ms  ({len(aff_res)} 条候选) ← 全表扫描")

    # ---------- 完整 search ----------
    ms_full, _ = timeit(lambda: st.search(q, top=10), n=3)
    print(f"\n完整 store.search(): {ms_full:8.2f} ms")

    # ---------- 汇总 ----------
    total_parts = ms_sem + ms_fts + ms_like + ms_struct + ms_aff
    print("\n" + "=" * 82)
    print("通道耗时占比")
    print("=" * 82)
    parts = [
        ("语义", ms_sem),
        ("FTS5", ms_fts),
        ("LIKE", ms_like),
        ("结构化", ms_struct),
        ("情绪", ms_aff),
    ]
    parts.sort(key=lambda x: -x[1])
    for name, ms in parts:
        pct = ms / max(0.01, total_parts) * 100
        bar = "#" * int(pct / 3)
        print(f"  {name:<8}{ms:8.2f} ms  {pct:5.1f}%  {bar}")
    print(f"  {'合计':<8}{total_parts:8.2f} ms")
    print(f"  {'实际':<8}{ms_full:8.2f} ms")

    top_name, top_ms = parts[0]
    print(f"\n瓶颈: {top_name}（{top_ms:.1f} ms，占 {top_ms/max(0.01,total_parts)*100:.0f}%）")

    st.con.close()


if __name__ == "__main__":
    main()
