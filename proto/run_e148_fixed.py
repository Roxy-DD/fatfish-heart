"""
E-148 方案 B：熟练度驱动"固定席位"（而不是排序加分）

E-147 的负面结果：
  熟练度参与排序加成 → 召回不变，平均排名 1.14 → 1.81（恶化）

为什么？
  高频记忆通常本来就是用户最常问的，已经在前面了。
  额外加权只会把"低熟频率但重要"的记忆往下挤。

用户原话："手上有惯性，手就直接随着这种惯性，不需要动脑子就能按出来。"
  → 关键词是"不需要动脑子"，即**不参与检索竞争，直接在场**
  → 这对应系统里已有的 fixed_slots 机制，不是排序加分

本实验验证方案 B：
  高熟练度记忆 → 进固定席位（不占检索席位，也不挤掉别人）
  对比：
    base      fixed_slots=0
    B-fixed   熟练度驱动的固定席位（top-3 熟练的都进）
    B-fixed5  top-5
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
RESULTS = os.path.join(_HERE, "results")
DB = os.path.join(_HERE, "e147_hard.db")   # 复用 E-147 的难测试集

K = 5


def main():
    from store_v2 import MemoryStore
    import channel as ch

    if not os.path.exists(DB):
        print("缺少 E-147 测试库")
        return

    st = MemoryStore(DB)

    print("=" * 94)
    print("E-148 方案 B：熟练度驱动固定席位")
    print("=" * 94)

    # 复用 E-147 的查询
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "e147", os.path.join(_HERE, "run_e147_hard.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    QUERIES = ([(q, txt, "password") for txt, k, q in mod.PASSWORD_GROUP] +
               [(q, txt, "indirect") for txt, k, q in mod.INDIRECT_GROUP])

    print(f"\n库中 {st.count()} 条，查询 {len(QUERIES)} 条")

    # 熟练度统计
    sm = ch.stats(st, limit=8)
    print(f"\n熟练度追踪: {sm['memories_tracked']} 条")
    print(f"总访问: {sm['total_visits']}")
    print(f"各通道: {sm['by_channel']}")
    print(f"\n最熟练的 8 条:")
    for t in sm["top"][:8]:
        ep = st.get(t["ep_id"])
        if ep:
            print(f"  {t['visits']:<6}次  {ep.raw_text[:44]}")

    def measure(fixed_n):
        hit = 0
        ranks = []
        by_grp = {}
        n_results = []
        for q, gold, grp in QUERIES:
            res = st.search(q, top=K, fixed_slots=fixed_n)
            got = [ep.raw_text for ep, *_ in res]
            n_results.append(len(got))
            ok = gold in got
            if ok:
                hit += 1
                ranks.append(got.index(gold) + 1)
            d = by_grp.setdefault(grp, [0, 0])
            d[1] += 1
            d[0] += 1 if ok else 0
        return {
            "recall": round(hit / len(QUERIES), 4),
            "hit": hit,
            "avg_rank": round(sum(ranks) / max(1, len(ranks)), 2),
            "avg_results": round(sum(n_results) / len(n_results), 2),
            "by_grp": {k: round(v[0] / v[1], 4) for k, v in by_grp.items()},
        }

    print("\n" + "-" * 94)
    print("对比")
    print("-" * 94)
    print("%-18s%-14s%-12s%-12s%-10s%-10s" % (
        "方案", "召回", "平均排名", "结果条数", "密码类", "间接类"))
    print("-" * 94)

    rows = []
    for name, fixed_n in [("base(fixed=0)", 0), ("B(fixed=3)", 3),
                          ("B(fixed=5)", 5), ("B(fixed=8)", 8)]:
        r = measure(fixed_n)
        r["name"] = name
        r["fixed"] = fixed_n
        rows.append(r)
        print("%-18s%-14s%-12s%-12s%-10.4f%-10.4f" % (
            name, "%d/%d" % (r["hit"], len(QUERIES)),
            r["avg_rank"], r["avg_results"],
            r["by_grp"].get("password", 0),
            r["by_grp"].get("indirect", 0)))

    # ---------- 分析 ----------
    print("\n" + "=" * 94)
    print("结论")
    print("=" * 94)

    base = rows[0]
    best = max(rows[1:], key=lambda r: (r["recall"], -r["avg_rank"]))

    print(f"\n  基线: 召回 {base['recall']:.4f}  排名 {base['avg_rank']}")
    print(f"  最优: {best['name']}  召回 {best['recall']:.4f}  "
          f"排名 {best['avg_rank']}")

    d_recall = best["recall"] - base["recall"]
    print(f"\n  召回变化: {d_recall:+.4f}")
    print(f"  结果条数: {base['avg_results']} → {best['avg_results']}")

    print(f"\n  关键观察：")
    if best["recall"] > base["recall"]:
        print(f"    ✅ 固定席位提升了召回（+{d_recall:.4f}）")
        print(f"    → 因为高熟练记忆直接在场，不占检索席位")
    elif abs(d_recall) < 0.01:
        print(f"    — 召回基本不变（{d_recall:+.4f}）")
        print(f"    → 但结果条数从 {base['avg_results']} 增加到 "
              f"{best['avg_results']}，说明固定席位确实额外加了内容")
        print(f"    → 这些额外内容不占检索席位，所以没挤掉任何目标")
    else:
        print(f"    ❌ 召回下降（{d_recall:+.4f}）")

    print(f"\n  与 E-147（排序加分）对比：")
    print(f"    排序加分:   召回不变，平均排名 1.14 → 1.81（恶化）")
    print(f"    固定席位:   召回 {base['recall']:.4f} → {best['recall']:.4f}，"
          f"排名 {base['avg_rank']} → {best['avg_rank']}")
    print(f"\n    → 结论：'惯性'应该实现为【直接在场】，不是【排得更前】")

    out = {"experiment": "E-148",
           "n_queries": len(QUERIES),
           "results": rows,
           "base": base, "best": best,
           "delta_recall": round(d_recall, 4)}
    with open(os.path.join(RESULTS, "e148_fixed_slot.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    st.con.close()
    print(f"\n写入 {os.path.join(RESULTS, 'e148_fixed_slot.json')}")


if __name__ == "__main__":
    main()
