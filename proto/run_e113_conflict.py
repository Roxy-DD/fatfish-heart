"""
E-113 矛盾记忆的实际危害（先证明问题存在，再设计解法）

目的：
  当前系统对"新旧信息矛盾"没有任何处理，只是把两条都存进去。
  实验要验证：这会造成什么样的实际错误？

设计：
  写入一组矛盾记忆（旧值/新值），用真实问法检索，
  看系统返回什么 —— 如果同时返回两条互斥答案，那就是故障。

四类矛盾各测一组：
  A 时间性（密码从8848改成9988）
  B 事实性（同一时刻两个不同来源说不同值）
  C 视角（截止日期原本15号，后延期20号）
  D 可合成（客户要A，也要B）
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
RESULTS = os.path.join(_HERE, "results")
DB = os.path.join(_HERE, "e113_conflict.db")

# 每组：旧记忆、新记忆、查询、期望的正确行为
CASES = [
    {
        "type": "A_time",
        "desc": "时间性矛盾：密码改了",
        "old": "我电脑的登录密码是 8848",
        "new": "我把电脑登录密码改成了 9988，旧的不能用了",
        "query": "我电脑密码是多少",
        "correct": "9988（新值），不应同时出现 8848",
    },
    {
        "type": "B_fact",
        "desc": "事实性矛盾：同一时刻两个说法",
        "old": "老王说门锁密码是 3721",
        "new": "小李说门锁密码是 9512",
        "query": "家里门锁密码是多少",
        "correct": "无法确定，应追问用户到底哪个对",
    },
    {
        "type": "C_view",
        "desc": "视角矛盾：截止日期延期",
        "old": "这个项目 15 号截止",
        "new": "客户同意把项目截止延到 20 号",
        "query": "项目什么时候截止",
        "correct": "20号（延期后），或说明原15号已延到20号",
    },
    {
        "type": "D_merge",
        "desc": "可合成：客户两个需求",
        "old": "客户要求界面必须支持深色模式",
        "new": "客户还要求界面必须支持大字体",
        "query": "客户有什么要求",
        "correct": "两条都要（深色模式 + 大字体），这是可合成的",
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

    print("=" * 84)
    print("E-113 矛盾记忆的实际危害")
    print("=" * 84)

    for c in CASES:
        ep_old = st.add(c["old"])
        ep_new = st.add(c["new"])
        res = st.search(c["query"], top=5)

        got = [(ep.raw_text, sc) for ep, sc, *_ in res]

        # 判断是否"同时返回了互斥答案"
        has_old = any(c["old"] in g[0] for g in got)
        has_new = any(c["new"] in g[0] for g in got)
        both = has_old and has_new

        print(f"\n{'=' * 84}")
        print(f"[{c['type']}] {c['desc']}")
        print("-" * 84)
        print(f"  旧: {c['old']}")
        print(f"  新: {c['new']}")
        print(f"  查: {c['query']}")
        print(f"  期望: {c['correct']}")
        print(f"\n  实际返回 {len(got)} 条:")
        for txt, sc in got:
            print(f"    [{sc:.2f}] {txt[:56]}")
        print(f"\n  旧值出现={has_old}  新值出现={has_new}  "
              f"→ {'⚠️ 同时返回互斥答案（故障）' if both else '（未同时返回）'}")

        results.append({
            "type": c["type"], "desc": c["desc"],
            "query": c["query"],
            "returned": [t for t, _ in got],
            "has_old": has_old, "has_new": has_new,
            "both_returned": both,
            "expected": c["correct"],
        })

    # 汇总
    print("\n" + "=" * 84)
    print("汇总")
    print("=" * 84)
    n_both = sum(1 for r in results if r["both_returned"])
    print(f"  四类矛盾中，同时返回互斥答案的: {n_both}/{len(results)}")
    print()
    for r in results:
        mark = "❌ 互斥答案共存" if r["both_returned"] else "✓"
        print(f"  {mark:<16}{r['type']:<12}{r['desc']}")

    print("\n结论：")
    if n_both >= 2:
        print(f"  → 系统对矛盾无处理，{n_both} 类会同时返回互斥答案")
        print("  → 这不是检索排序问题，是缺少'矛盾判定 + 时效标记'这一层")
    else:
        print("  → 危害有限，但仍缺显式矛盾处理")

    out = {"experiment": "E-113", "n_cases": len(results),
           "n_both_returned": n_both, "cases": results}
    with open(os.path.join(RESULTS, "e113_conflict.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e113_conflict.json')}")


if __name__ == "__main__":
    main()
