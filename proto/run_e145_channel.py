"""
E-145 多通道熟练度：基础正确性与红线验证

这一步只验证"记账是否准确"，不涉及"是否用于排序"。
排序用法留到 E-146 做 A/B 对比。

验证：
  ① 计数正确性（每次访问 +1）
  ② 通道独立性（"会说 ≠ 会写"：不同通道互不影响）
  ③ R1 迁移（跨进程一致）
  ④ R2 不遗忘（只增不减，无删除）
  ⑤ R3 可重建（meta 清空后结构仍稳定）
  ⑥ 性能（记账开销）
"""
from __future__ import annotations

import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
RESULTS = os.path.join(_HERE, "results")
DB = os.path.join(_HERE, "e145_channel.db")


def fresh():
    for suf in ("", "-wal", "-shm"):
        p = DB + suf
        if os.path.exists(p):
            os.remove(p)


def main():
    from store_v2 import MemoryStore
    import channel as ch

    fresh()
    st = MemoryStore(DB)

    print("=" * 90)
    print("E-145 多通道熟练度：基础正确性与红线")
    print("=" * 90)

    results = []

    # ---------- ① 计数正确性 ----------
    print("\n① 计数正确性")
    ep = st.add("家里的门锁密码是 3721", resolve_conflict=False)
    eid = ep.ep_id

    init = ch.get_channels(st, eid)
    print(f"  初始：{init}")
    ok_init = all(v == 0 for v in init.values())

    for _ in range(5):
        ch.bump(st, eid, "recall_by_query")
    after5 = ch.get_channels(st, eid)
    print(f"  查询通道 +5 后：{after5}")
    ok_count = after5["recall_by_query"] == 5

    print(f"  → 初始全 0: {'✅' if ok_init else '❌'}  计数正确: {'✅' if ok_count else '❌'}")
    results.append({"case": "count_correct", "ok": ok_init and ok_count})

    # ---------- ② 通道独立性 ----------
    print("\n② 通道独立性（对应'会说 ≠ 会写'）")
    ch.bump(st, eid, "recall_by_fixed", 3)
    ch.bump(st, eid, "recall_by_chain", 2)
    cur = ch.get_channels(st, eid)
    print(f"  {cur}")
    ok_indep = (cur["recall_by_query"] == 5 and
                cur["recall_by_fixed"] == 3 and
                cur["recall_by_chain"] == 2 and
                cur["recall_by_merge"] == 0 and
                cur["recall_by_prompt"] == 0)
    print(f"  → 各通道互不影响: {'✅' if ok_indep else '❌'}")
    results.append({"case": "channel_independent", "ok": ok_indep})

    # 诊断指标
    print(f"\n  诊断：总访问 {ch.total_visits(cur)}  "
          f"主导通道 {ch.dominant_channel(cur)}  "
          f"熟练度 {ch.familiarity(cur)}")
    results.append({"case": "diagnostics", "ok": ch.total_visits(cur) == 10})

    # ---------- ③ 性能 ----------
    print("\n③ 记账开销")
    N = 200
    t0 = time.perf_counter()
    for _ in range(N):
        ch.bump(st, eid, "recall_by_query")
    dt = (time.perf_counter() - t0) / N * 1000
    print(f"  单次 bump 平均: {dt:.3f} ms（{N} 次）")
    ok_perf = dt < 5.0
    print(f"  → {'✅ 可接受（<5ms）' if ok_perf else '❌ 太慢'}")
    results.append({"case": "perf", "ok": ok_perf, "ms": round(dt, 3)})

    # 批量
    ids = [st.add(f"测试记忆{i}", resolve_conflict=False).ep_id
           for i in range(50)]
    t0 = time.perf_counter()
    ch.bump_many(st, ids, "recall_by_query")
    dt2 = (time.perf_counter() - t0) / 50 * 1000
    print(f"  批量 bump_many 平均: {dt2:.3f} ms/条（50 条）")

    # ---------- ④ R2 不遗忘 ----------
    print("\n④ R2 不遗忘（计数只增不减）")
    before = ch.get_channels(st, eid)["recall_by_query"]
    ch.bump(st, eid, "recall_by_query", 0)     # 加 0 不应改变
    after = ch.get_channels(st, eid)["recall_by_query"]
    ch.bump(st, eid, "recall_by_query", -5)    # 负数应被拦
    after_neg = ch.get_channels(st, eid)["recall_by_query"]
    ok_r2 = (before == after == after_neg)
    print(f"  加 0: {before}→{after}  加负数: →{after_neg}")
    print(f"  → 只会增不会减: {'✅' if ok_r2 else '❌'}")
    results.append({"case": "r2_only_increase", "ok": ok_r2})

    # 记录总数不变
    total_before = st.count()
    ch.bump(st, eid, "recall_by_query")
    total_after = st.count()
    ok_r2b = total_before == total_after
    print(f"  记录总数 {total_before} → {total_after}: "
          f"{'✅ 记账不产生记忆' if ok_r2b else '❌ 产生了'}")
    results.append({"case": "r2_no_new_records", "ok": ok_r2b})

    # ---------- ⑤ R1 迁移 ----------
    print("\n⑤ R1 迁移（跨连接一致）")
    snap_before = ch.get_channels(st, eid)
    st.con.close()

    st2 = MemoryStore(DB)
    snap_after = ch.get_channels(st2, eid)
    ok_r1 = snap_before == snap_after
    print(f"  重开前：{snap_before}")
    print(f"  重开后：{snap_after}")
    print(f"  → {'✅ 一致' if ok_r1 else '❌ 不一致'}")
    results.append({"case": "r1_migration", "ok": ok_r1})

    # 异库复制
    import shutil
    db2 = os.path.join(_HERE, "e145_copy.db")
    for suf in ("", "-wal", "-shm"):
        p = db2 + suf
        if os.path.exists(p):
            os.remove(p)
    shutil.copy(DB, db2)
    st3 = MemoryStore(db2)
    snap_copy = ch.get_channels(st3, eid)
    ok_r1b = snap_copy == snap_before
    print(f"  异库复制后：{snap_copy}")
    print(f"  → {'✅ 一致' if ok_r1b else '❌ 不一致'}")
    st3.con.close()
    results.append({"case": "r1_copy", "ok": ok_r1b})

    # ---------- ⑥ R3 可重建 ----------
    print("\n⑥ R3 索引可重建（清空 meta 后结构仍稳定）")
    st2.con.execute("DELETE FROM meta WHERE key LIKE 'channel:%'")
    st2.con.commit()
    empty = ch.get_channels(st2, eid)
    ok_r3 = (all(v == 0 for v in empty.values()) and
             set(empty.keys()) == set(ch.CHANNELS))
    print(f"  清空后：{empty}")
    print(f"  → 结构稳定（全 0 且通道齐全）: {'✅' if ok_r3 else '❌'}")

    # 清空后检索仍可用（索引不依赖通道）
    res = st2.search("门锁密码", top=3)
    ok_r3b = len(res) > 0
    print(f"  清空通道后检索仍可用: {'✅' if ok_r3b else '❌'}")
    results.append({"case": "r3_stable", "ok": ok_r3 and ok_r3b})

    # ---------- ⑦ 全库统计 ----------
    print("\n⑦ 全库统计")
    sm = ch.stats(st2)
    print(f"  被追踪记忆: {sm['memories_tracked']}")
    print(f"  各通道总访问: {sm['by_channel']}")

    st2.con.close()

    # ---------- 汇总 ----------
    print("\n" + "=" * 90)
    print("汇总")
    print("=" * 90)
    n_ok = sum(1 for r in results if r["ok"])
    for r in results:
        print(f"  {'✅' if r['ok'] else '❌'} {r['case']}")
    print(f"\n  通过 {n_ok}/{len(results)}")
    passed = n_ok == len(results)
    print(f"  → {'✅ 多通道熟练度基础层可用' if passed else '❌ 有问题'}")

    out = {"experiment": "E-145", "passed": passed,
           "n_pass": n_ok, "n_total": len(results),
           "cases": results}
    with open(os.path.join(RESULTS, "e145_channel.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e145_channel.json')}")


if __name__ == "__main__":
    main()
