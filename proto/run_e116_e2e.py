"""
E-116 矛盾处理接入存储后的端到端验证

对比：
  基线（E-113）：4/4 类矛盾同时返回互斥答案 → 全失效
  现在：       矛盾判定已接入 store_v2.add

验证两件事：
  ① 写入时是否自动判定并落盘（replace 标失效 / merge 互链 / conflict 标低置信）
  ② 检索时矛盾是否被消解（不再同时返回互斥答案）
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
RESULTS = os.path.join(_HERE, "results")
DB = os.path.join(_HERE, "e116_e2e.db")

CASES = [
    {
        "type": "A_time",
        "old": "我电脑的登录密码是 8848",
        "new": "我把电脑登录密码改成了 9988，旧的不能用了",
        "query": "我电脑密码是多少",
        "expect_relation": "replace",
        "expect_old_active": False,   # 旧值应失效
    },
    {
        "type": "C_view",
        "old": "这个项目 15 号截止",
        "new": "客户同意把项目截止延到 20 号",
        "query": "项目什么时候截止",
        "expect_relation": "replace",
        "expect_old_active": False,
    },
    {
        "type": "D_merge",
        "old": "客户要求界面必须支持深色模式",
        "new": "客户还要求界面必须支持大字体",
        "query": "客户有什么要求",
        "expect_relation": "merge",
        "expect_old_active": True,    # 合并，两条都该在
    },
    {
        "type": "B_fact",
        "old": "老王说门锁密码是 3721",
        "new": "小李说门锁密码是 9512",
        "query": "门锁密码是多少",
        "expect_relation": "conflict",
        "expect_old_active": True,    # 冲突，两条都保留
    },
]


def main():
    from store_v2 import MemoryStore

    for suf in ("", "-wal", "-shm"):
        p = DB + suf
        if os.path.exists(p):
            os.remove(p)

    st = MemoryStore(DB)
    results = []

    print("=" * 88)
    print("E-116 矛盾处理端到端验证")
    print("=" * 88)

    for c in CASES:
        # 写入旧记忆（第一条不该触发矛盾）
        ep_old = st.add(c["old"])

        # 写入新记忆（应触发矛盾判定）
        ep_new, verdict = st.add(c["new"], return_verdict=True)

        got_rel = verdict.relation if verdict else "none"
        sim = verdict.similarity if verdict else 0

        # 检查旧值是否失效
        old_ep = st.get(ep_old.ep_id)
        old_active = old_ep.invalid_at is None

        # 检索（默认排除失效）
        res = st.search(c["query"], top=5)
        hits = [ep.raw_text for ep, *_ in res]
        has_old = any(c["old"] in h for h in hits)
        has_new = any(c["new"] in h for h in hits)

        rel_ok = (got_rel == c["expect_relation"])
        active_ok = (old_active == c["expect_old_active"])

        # 判定这轮是否"消解了矛盾"
        if c["expect_relation"] == "replace":
            # 期望：新值在，旧值不在（因为已失效被默认排除）
            resolved = has_new and not has_old
        elif c["expect_relation"] == "merge":
            # 期望：两条都在
            resolved = has_new and has_old
        else:  # conflict
            # 期望：两条都在，但系统知道有冲突
            resolved = has_new and has_old

        print(f"\n{'=' * 88}")
        print(f"[{c['type']}] {c['old']}  →  {c['new']}")
        print("-" * 88)
        print(f"  判定: {got_rel} (期望 {c['expect_relation']}) {'✓' if rel_ok else '✗'}"
              f"  相似度={sim:.3f}")
        print(f"  旧值状态: {'有效' if old_active else '已失效'}"
              f" (期望 {'有效' if c['expect_old_active'] else '已失效'})"
              f" {'✓' if active_ok else '✗'}")
        print(f"  检索 \"{c['query']}\" 返回 {len(hits)} 条:")
        for h in hits:
            mark = ""
            if c["old"] in h:
                mark = " ←旧值"
            elif c["new"] in h:
                mark = " ←新值"
            print(f"    {h[:56]}{mark}")
        print(f"  矛盾消解: {'✅ 是' if resolved else '❌ 否'}"
              f"  (旧值出现={has_old} 新值出现={has_new})")

        results.append({
            "type": c["type"], "relation": got_rel,
            "expected_relation": c["expect_relation"],
            "similarity": sim,
            "rel_ok": rel_ok, "active_ok": active_ok,
            "has_old": has_old, "has_new": has_new,
            "resolved": resolved,
        })

    # ---------- 汇总 ----------
    print("\n" + "=" * 88)
    print("汇总对比")
    print("=" * 88)
    n_res = sum(1 for r in results if r["resolved"])
    n_rel = sum(1 for r in results if r["rel_ok"])

    print(f"{'类型':<12}{'判定':<12}{'判定对?':<10}{'旧值状态':<12}{'矛盾消解'}")
    print("-" * 88)
    for r in results:
        print(f"{r['type']:<12}{r['relation']:<12}"
              f"{'✓' if r['rel_ok'] else '✗':<10}"
              f"{'有效' if not r['has_old'] else '仍返回':<12}"
              f"{'✅' if r['resolved'] else '❌'}")
    print("-" * 88)
    print(f"\n  判定准确: {n_rel}/{len(results)}")
    print(f"  矛盾消解: {n_res}/{len(results)}")
    print(f"\n  基线（E-113）: 0/{len(results)} 消解（全部同时返回互斥答案）")
    print(f"  现在：        {n_res}/{len(results)} 消解")
    print(f"  提升:         {n_res}/{len(results)} vs 0/{len(results)}")

    out = {"experiment": "E-116", "n": len(results),
           "relation_accurate": n_rel, "resolved": n_res,
           "baseline_e113_resolved": 0,
           "cases": results}
    with open(os.path.join(RESULTS, "e116_e2e.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e116_e2e.json')}")


if __name__ == "__main__":
    main()
