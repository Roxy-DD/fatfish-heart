"""E-25 FTS5 瓶颈深挖：为什么 343ms？

E-24 定位：FTS5 占 95%（343.7ms / 2000 条），是唯一瓶颈。

嫌疑：
  H1  bm25() 排序代价高（需要算所有命中行的分数）
  H2  MATCH 表达式用 OR 拼接了 7 个词，命中行数太多
  H3  缺 FTS5 索引优化（如 rank 而非 bm25）
  H4  ep_fts 是 external content 表，JOIN episodes 有额外开销
  H5  FTS 表从未 optimize，索引碎片

本实验逐个验证。
"""
import sys
import os
import time
import sqlite3

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
from store_v1 import split_query_terms

DB = os.path.join(_HERE, "work", "e23_2000.db")


def timeit(fn, n=5):
    fn()
    t0 = time.perf_counter()
    for _ in range(n):
        r = fn()
    return (time.perf_counter() - t0) / n * 1000, r


def main():
    print("=" * 82)
    print("E-25 FTS5 瓶颈深挖")
    print("=" * 82)

    con = sqlite3.connect(DB)
    n = con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
    print(f"\n库中 {n} 条记忆")

    q = "登录密码改成 9999 了吗"
    terms = split_query_terms(q)
    print(f"拆词 {len(terms)} 个: {terms}\n")

    # ---------- H2 验证：OR 拼接 vs 单词 ----------
    print("【H2】MATCH 表达式复杂度")
    or_expr = " OR ".join(f'"{t}"' for t in terms)

    def match_or():
        return con.execute(
            "SELECT rowid FROM ep_fts WHERE ep_fts MATCH ?", (or_expr,)
        ).fetchall()

    ms_or, or_res = timeit(match_or, n=3)
    print(f"  7 词 OR: {ms_or:7.1f} ms  命中 {len(or_res)} 行")

    # 单词
    def match_one():
        return con.execute(
            "SELECT rowid FROM ep_fts WHERE ep_fts MATCH ?", ('"密码"',)
        ).fetchall()

    ms_one, one_res = timeit(match_one, n=3)
    print(f"  单词:    {ms_one:7.1f} ms  命中 {len(one_res)} 行")

    # ---------- H1 验证：bm25 排序 vs 不排序 ----------
    print("\n【H1】bm25() 排序开销")

    def with_bm25():
        return con.execute(
            "SELECT rowid, bm25(ep_fts) FROM ep_fts "
            "WHERE ep_fts MATCH ? ORDER BY bm25(ep_fts) LIMIT 60",
            (or_expr,)).fetchall()

    def without_order():
        return con.execute(
            "SELECT rowid FROM ep_fts WHERE ep_fts MATCH ? LIMIT 60",
            (or_expr,)).fetchall()

    ms_bm25, _ = timeit(with_bm25, n=3)
    ms_noord, _ = timeit(without_order, n=3)
    print(f"  带 bm25+ORDER BY: {ms_bm25:7.1f} ms")
    print(f"  不带排序:          {ms_noord:7.1f} ms")
    print(f"  排序开销:          {ms_bm25 - ms_noord:7.1f} ms")

    # ---------- H4 验证：JOIN episodes 的开销 ----------
    print("\n【H4】JOIN episodes 开销")

    def join_ep():
        return con.execute(
            "SELECT e.ep_id, bm25(ep_fts) FROM ep_fts JOIN episodes e "
            "ON e.rowid=ep_fts.rowid WHERE ep_fts MATCH ? "
            "AND e.invalid_at IS NULL ORDER BY bm25(ep_fts) LIMIT 60",
            (or_expr,)).fetchall()

    ms_join, _ = timeit(join_ep, n=3)
    print(f"  JOIN episodes: {ms_join:7.1f} ms")
    print(f"  JOIN 开销:     {ms_join - ms_bm25:7.1f} ms")

    # ---------- H5 验证：FTS 索引大小与碎片 ----------
    print("\n【H5】FTS 索引状态")
    try:
        # FTS5 没有直接的大小查询，用 shadow 表估算
        rows = con.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE 'ep_fts%' "
            "AND type='table'").fetchall()
        print(f"  shadow 表: {[r[0] for r in rows]}")
        for (tn,) in rows:
            try:
                c = con.execute(f"SELECT COUNT(*) FROM {tn}").fetchone()[0]
                print(f"    {tn}: {c} 行")
            except Exception:
                pass
    except Exception as e:
        print(f"  查询失败: {e}")

    # ---------- H3 验证：rank 列 ----------
    print("\n【H3】用 rank 列替代 bm25()")
    try:
        def use_rank():
            return con.execute(
                "SELECT rowid, rank FROM ep_fts WHERE ep_fts MATCH ? "
                "ORDER BY rank LIMIT 60", (or_expr,)).fetchall()

        ms_rank, _ = timeit(use_rank, n=3)
        print(f"  ORDER BY rank: {ms_rank:7.1f} ms")
        print(f"  vs bm25():     {ms_bm25:7.1f} ms")
        print(f"  提升:          {ms_bm25 - ms_rank:7.1f} ms")
    except sqlite3.OperationalError as e:
        print(f"  rank 不可用: {e}")

    # ---------- 尝试 optimize ----------
    print("\n【优化尝试】FTS5 optimize")
    t0 = time.perf_counter()
    try:
        con.execute("INSERT INTO ep_fts(ep_fts) VALUES('optimize')")
        con.commit()
        print(f"  optimize 耗时: {(time.perf_counter()-t0)*1000:.0f} ms")
        ms_after, _ = timeit(with_bm25, n=3)
        print(f"  optimize 后 bm25: {ms_after:7.1f} ms（之前 {ms_bm25:.1f} ms）")
    except Exception as e:
        print(f"  optimize 失败: {e}")

    con.close()

    # ---------- 结论 ----------
    print("\n" + "=" * 82)
    print("结论")
    print("=" * 82)
    print(f"\n  OR 拼接 {len(terms)} 词 → 命中 {len(or_res)} 行")
    print(f"  单词命中 {len(one_res)} 行")
    print(f"  主要开销: {'bm25 排序' if ms_bm25 - ms_noord > 50 else 'MATCH 匹配'}")

    if len(or_res) > n * 0.5:
        print(f"\n  ⚠ 命中 {len(or_res)}/{n} 行（超过一半），OR 拼接太宽")
        print(f"  → 优化方向：减少 OR 词数 / 用 AND 收窄 / 用 FTS5 的 NEAR")


if __name__ == "__main__":
    main()
