"""E-289 并发写入安全：多个进程同时写同一个库会不会坏？

架构上存在真实的并发风险：
  · 插件进程（Node.js）通过内核 HTTP/JSONL 访问
  · 内核进程（Python）持有 SQLite 连接
  · 我的实验脚本也会直接开连接
  · 备份/迁移工具也开连接

SQLite 默认 rollback journal 模式在并发下容易 database locked，
WAL 模式好很多但不是无限制。

本实验测：
  ① 多进程同时写入是否报错
  ② 是否会丢数据
  ③ 是否会数据错乱（数量对不上）
  ④ 锁等待超时行为
"""
import sys
import os
import json
import time
import sqlite3
import multiprocessing as mp

_HERE = os.path.dirname(os.path.abspath(__file__))
_MEM = os.path.join(_HERE, "cosmos_mem")
sys.path.insert(0, _MEM)

TEST_DB = os.path.join(_HERE, "work", "test_db", "e289.db")
os.makedirs(os.path.dirname(TEST_DB), exist_ok=True)


# ============ 工作进程（必须定义在模块级，Windows spawn 才能 pickle）============
def writer(worker_id, n_writes, result_queue):
    """每个进程写 n 条，记录成功/失败"""
    try:
        import warnings
        warnings.filterwarnings("ignore")
        sys.path.insert(0, _MEM)
        from store_v2 import MemoryStore

        st = MemoryStore(TEST_DB)
        ok = 0
        errors = []
        t0 = time.perf_counter()

        for i in range(n_writes):
            try:
                st.add(f"并发测试 W{worker_id} 第{i}条 记忆系统检索",
                       resolve_conflict=False)
                ok += 1
            except Exception as e:
                errors.append(f"{type(e).__name__}: {str(e)[:60]}")

        ms = (time.perf_counter() - t0) * 1000
        st.con.close()
        result_queue.put({
            "worker": worker_id, "ok": ok, "errors": errors[:3],
            "n_errors": len(errors), "ms": round(ms, 1),
        })
    except Exception as e:
        result_queue.put({
            "worker": worker_id, "ok": 0,
            "errors": [f"FATAL {type(e).__name__}: {str(e)[:60]}"],
            "n_errors": 1, "ms": 0,
        })


def main():
    print("=" * 78)
    print("E-289 并发写入安全")
    print("=" * 78)

    # ============ 准备 ============
    for suf in ("", "-wal", "-shm"):
        p = TEST_DB + suf
        if os.path.exists(p):
            os.remove(p)

    from store_v2 import MemoryStore
    st = MemoryStore(TEST_DB)
    mode = st.con.execute("PRAGMA journal_mode").fetchone()[0]
    print(f"\njournal_mode: {mode}")
    print(f"busy_timeout: {st.con.execute('PRAGMA busy_timeout').fetchone()[0]}")
    st.con.close()

    # ============ ① 4 进程 x 10 条 ============
    print("\n① 4 进程并发写入，每进程 10 条")

    N_WORKERS = 4
    N_EACH = 10

    q = mp.Queue()
    procs = []
    for w in range(N_WORKERS):
        p = mp.Process(target=writer, args=(w, N_EACH, q))
        p.start()
        procs.append(p)

    for p in procs:
        p.join(timeout=180)

    results = []
    while not q.empty():
        results.append(q.get())

    print(f"\n   {'进程':>4} {'成功':>6} {'失败':>6} {'耗时':>10} {'错误样本'}")
    print("   " + "-" * 70)
    total_ok = 0
    total_err = 0
    for r in sorted(results, key=lambda x: x["worker"]):
        total_ok += r["ok"]
        total_err += r["n_errors"]
        err = r["errors"][0][:34] if r["errors"] else ""
        print(f"   {r['worker']:>4} {r['ok']:>6} {r['n_errors']:>6} "
              f"{r['ms']:>9.0f}ms {err}")

    expected = N_WORKERS * N_EACH
    print(f"\n   期望写入: {expected} 条")
    print(f"   实际成功: {total_ok} 条")
    print(f"   失败次数: {total_err} 次")

    # ============ ② 验证数据完整性 ============
    print("\n② 验证数据完整性")

    st2 = MemoryStore(TEST_DB)
    n_total = st2.con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
    n_valid = st2.con.execute(
        "SELECT COUNT(*) FROM episodes WHERE invalid_at IS NULL").fetchone()[0]
    n_vec = st2.con.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]

    print(f"   episodes 总数: {n_total}")
    print(f"   有效: {n_valid}")
    print(f"   vectors: {n_vec}")

    orphans = st2.con.execute(
        "SELECT COUNT(*) FROM vectors v WHERE NOT EXISTS "
        "(SELECT 1 FROM episodes e WHERE e.ep_id = v.ep_id)").fetchone()[0]
    print(f"   孤儿向量: {orphans}")

    missing_vec = st2.con.execute(
        "SELECT COUNT(*) FROM episodes e WHERE NOT EXISTS "
        "(SELECT 1 FROM vectors v WHERE v.ep_id = e.ep_id)").fetchone()[0]
    print(f"   缺向量的记忆: {missing_vec}")

    try:
        n_fts = st2.con.execute("SELECT COUNT(*) FROM ep_fts").fetchone()[0]
        print(f"   FTS 条目: {n_fts}")
        fts_sync = (n_fts == n_total)
    except Exception as e:
        print(f"   FTS 读取失败: {e}")
        n_fts = -1
        fts_sync = False

    # ============ ③ 检索 ============
    print("\n③ 检索能否找到并发写入的记忆")
    found = 0
    for w in range(N_WORKERS):
        q_text = f"并发测试 W{w}"
        res = st2.search(q_text, top=5)
        for ep, sc, fl in res:
            if q_text in ep.raw_text:
                found += 1
                break
    print(f"   4 个 worker 的记忆都能找到: {found}/{N_WORKERS}")

    st2.con.close()

    # ============ ④ 配置 ============
    print("\n④ 连接的锁等待配置")
    con = sqlite3.connect(TEST_DB)
    con.execute("PRAGMA busy_timeout = 5000")
    bt = con.execute("PRAGMA busy_timeout").fetchone()[0]
    jm = con.execute("PRAGMA journal_mode").fetchone()[0]
    sync = con.execute("PRAGMA synchronous").fetchone()[0]
    con.close()
    print(f"   busy_timeout: {bt} ms")
    print(f"   journal_mode: {jm}")
    print(f"   synchronous:  {sync}")

    # ============ 结论 ============
    print("\n" + "=" * 78)
    print("结论")
    print("=" * 78)

    issues = []
    if total_err > 0:
        issues.append(f"{total_err} 次写入失败（并发冲突）")
    if total_ok < expected:
        issues.append(f"丢失 {expected - total_ok} 条写入")
    if orphans > 0:
        issues.append(f"{orphans} 个孤儿向量（数据不一致）")
    if missing_vec > 0:
        issues.append(f"{missing_vec} 条记忆缺向量")
    if not fts_sync:
        issues.append(f"FTS 未同步（{n_fts} vs {n_total}）")
    if found < N_WORKERS:
        issues.append(f"只找到 {found}/{N_WORKERS} 个 worker 的记忆")

    if issues:
        print("\n  发现的问题：")
        for i in issues:
            print(f"    · {i}")
    else:
        print("\n  ✅ 并发写入安全，数据完整")

    print(f"""
  数据：
    期望 {expected} 条 / 成功 {total_ok} 条 / 失败 {total_err} 次
    向量 {n_vec} / FTS {n_fts} / 孤儿 {orphans} / 缺向量 {missing_vec}
    检索命中 {found}/{N_WORKERS}
""")

    out = {
        "experiment": "E-289",
        "journal_mode": jm, "busy_timeout": bt,
        "expected": expected, "ok": total_ok, "errors": total_err,
        "n_total": n_total, "n_vec": n_vec, "n_fts": n_fts,
        "orphans": orphans, "missing_vec": missing_vec,
        "found": found, "issues": issues,
    }
    p = os.path.join(_HERE, "results", "e289_concurrency.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"写入 results/e289_concurrency.json")


if __name__ == "__main__":
    main()
