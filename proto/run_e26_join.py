"""E-26 验证：JOIN 255ms 的根因是不是 external content 回表

E-25 发现：JOIN episodes 占 255ms（99%），FTS MATCH 本身只要 0.7ms。

假设：
  H1  external content 表在 JOIN 时回表读 raw_text 重建索引 → 慢
  H2  invalid_at IS NULL 条件迫使全表 JOIN → 慢
  H3  bm25(ep_fts) 在 JOIN 后重新计算 → 慢

方法：对比不同写法的耗时
  A) SELECT rowid FROM ep_fts MATCH        （不回表）
  B) SELECT rowid, rank FROM ep_fts MATCH （不回表，带排序）
  C) SELECT e.ep_id, bm25(ep_fts) FROM ep_fts JOIN episodes（回表）
  D) 先拿 rowid，再用 ep_id IN (...) 二次查（两段式）
  E) 用子查询：WHERE rowid IN (SELECT rowid FROM ep_fts MATCH ...)
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
    print("E-26 JOIN 根因验证")
    print("=" * 82)

    con = sqlite3.connect(DB)
    n = con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
    q = "登录密码改成 9999 了吗"
    terms = split_query_terms(q)
    expr = " OR ".join(f'"{t}"' for t in terms)
    print(f"\n库 {n} 条，查询拆 {len(terms)} 词\n")

    # A：不回表
    def A():
        return con.execute(
            "SELECT rowid FROM ep_fts WHERE ep_fts MATCH ? LIMIT 60",
            (expr,)).fetchall()

    # B：不回表 + rank 排序
    def B():
        return con.execute(
            "SELECT rowid, rank FROM ep_fts WHERE ep_fts MATCH ? "
            "ORDER BY rank LIMIT 60", (expr,)).fetchall()

    # C：回表（当前实现）
    def C():
        return con.execute(
            "SELECT e.ep_id, bm25(ep_fts) FROM ep_fts JOIN episodes e "
            "ON e.rowid=ep_fts.rowid WHERE ep_fts MATCH ? "
            "AND e.invalid_at IS NULL ORDER BY bm25(ep_fts) LIMIT 60",
            (expr,)).fetchall()

    # D：两段式（先拿 rowid，再用 ep_id IN）
    def D():
        rows = con.execute(
            "SELECT rowid, rank FROM ep_fts WHERE ep_fts MATCH ? "
            "ORDER BY rank LIMIT 60", (expr,)).fetchall()
        if not rows:
            return []
        rids = [r[0] for r in rows]
        ph = ",".join("?" * len(rids))
        return con.execute(
            f"SELECT ep_id, rowid FROM episodes WHERE rowid IN ({ph}) "
            f"AND invalid_at IS NULL", rids).fetchall()

    # E：子查询形式
    def E():
        return con.execute(
            "SELECT e.ep_id FROM episodes e WHERE e.rowid IN "
            "(SELECT rowid FROM ep_fts WHERE ep_fts MATCH ?) "
            "AND e.invalid_at IS NULL LIMIT 60", (expr,)).fetchall()

    # F：只查 rowid，不 JOIN，但保留 invalid 过滤（用 NOT IN 子查询）
    def F():
        return con.execute(
            "SELECT rowid, rank FROM ep_fts WHERE ep_fts MATCH ? "
            "AND rowid NOT IN (SELECT rowid FROM episodes WHERE invalid_at IS NOT NULL) "
            "ORDER BY rank LIMIT 60", (expr,)).fetchall()

    tests = [
        ("A 不回表（无排序）", A),
        ("B 不回表 + rank 排序", B),
        ("C 回表 JOIN（当前实现）", C),
        ("D 两段式（rowid → ep_id IN）", D),
        ("E 子查询 IN", E),
        ("F 不回表 + NOT IN 排除失效", F),
    ]

    print(f"{'写法':<32}{'耗时':<14}{'结果数'}")
    print("-" * 60)
    results = {}
    for name, fn in tests:
        ms, r = timeit(fn, n=5)
        results[name] = ms
        print(f"{name:<32}{ms:8.2f} ms    {len(r)}")

    # ---------- 结论 ----------
    print("\n" + "=" * 82)
    print("结论")
    print("=" * 82)
    ms_c = results["C 回表 JOIN（当前实现）"]
    ms_b = results["B 不回表 + rank 排序"]
    ms_d = results["D 两段式（rowid → ep_id IN）"]
    ms_e = results["E 子查询 IN"]
    ms_f = results["F 不回表 + NOT IN 排除失效"]

    print(f"\n  当前实现（C）:      {ms_c:7.2f} ms")
    print(f"  纯 FTS 查询（B）:    {ms_b:7.2f} ms   ← 快了 {ms_c/max(0.01,ms_b):.0f}x")
    print(f"  两段式（D）:         {ms_d:7.2f} ms   ← 快了 {ms_c/max(0.01,ms_d):.0f}x")
    print(f"  子查询 IN（E）:      {ms_e:7.2f} ms")
    print(f"  NOT IN 排除（F）:    {ms_f:7.2f} ms   ← 快了 {ms_c/max(0.01,ms_f):.0f}x")

    best = min(
        [("B", ms_b), ("D", ms_d), ("E", ms_e), ("F", ms_f)],
        key=lambda x: x[1])
    print(f"\n  最优写法: {best[0]}（{best[1]:.2f} ms）")
    print(f"  相对当前提升: {ms_c/max(0.01,best[1]):.0f}x")

    if best[1] < ms_c * 0.2:
        print(f"\n  → ✅ 确认：回表是主要开销，改用「{best[0]}」可降到 1/5 以下")

    con.close()


if __name__ == "__main__":
    main()
