"""
E-162 量化：LoCoMo 各题型的"聚合需求"

E-161 诊断发现：单跳失败的主因不是检索不到，而是答案分散在多轮，
需要"跨条目聚合"（如问 Melanie 参加什么活动，答案 pottery/camping/swimming
分散在 D5:4 / D9:1 / D1:18 三轮）。

本实验量化：
  ① 每类题型里，evidence 条数分布（1 条 = 纯单点，>=3 = 强聚合）
  ② 只用"单条 evidence"的题，召回率是多少（纯检索能力）
  ③ 需要聚合的题，召回率是多少（聚合能力）

这决定了下一步该优化什么：
  · 若单条 evidence 的召回率高 → 检索没问题，缺的是聚合
  · 若单条也低 → 检索本身有问题
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter, defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e162")
os.makedirs(TASK, exist_ok=True)
RESULTS = os.path.join(_HERE, "results")
DATA = os.path.join(_HERE, "work", "e159", "locomo_sample.json")

K = 10
CAT = {"1": "单跳", "2": "时间", "3": "多跳", "4": "常识", "5": "对抗"}


def main():
    from store_v2 import MemoryStore

    d = json.load(open(DATA, encoding="utf-8"))
    qa = d.get("qa") or []
    conv = d.get("conversation") or {}

    sessions = []
    i = 1
    while f"session_{i}" in conv:
        turns = conv[f"session_{i}"]
        if turns:
            sessions.append((i, conv.get(f"session_{i}_date_time", ""), turns))
        i += 1

    db = os.path.join(TASK, "locomo.db")
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

    print("=" * 94)
    print("E-162 量化：聚合需求 vs 检索能力")
    print("=" * 94)

    # 统计
    ev_count_by_cat = defaultdict(Counter)
    stats = defaultdict(lambda: {"single_n": 0, "single_hit": 0,
                                 "multi_n": 0, "multi_hit": 0,
                                 "all_n": 0, "all_hit": 0})

    for q in qa:
        question = q.get("question") or ""
        cat = str(q.get("category"))
        ev = q.get("evidence") or []
        if not isinstance(ev, list):
            ev = [ev]
        ev = [str(e).strip() for e in ev if e]
        if not question or not ev:
            continue

        ev_count_by_cat[cat][len(ev)] += 1

        res = st.search(question, top=K)
        got = {mark_of.get(t[0].ep_id) for t in res}
        got.discard(None)

        any_hit = any(e in got for e in ev)
        all_hit = all(e in got for e in ev)

        s = stats[cat]
        if len(ev) == 1:
            s["single_n"] += 1
            s["single_hit"] += 1 if any_hit else 0
        else:
            s["multi_n"] += 1
            s["multi_hit"] += 1 if any_hit else 0
        s["all_n"] += 1
        s["all_hit"] += 1 if all_hit else 0

    st.con.close()

    # ---------- evidence 条数分布 ----------
    print("\n① evidence 条数分布（1=纯单点，>=3=强聚合）")
    print("-" * 94)
    print(f"{'类别':<10}{'1条':<8}{'2条':<8}{'3条':<8}{'4+条':<8}{'总'}")
    print("-" * 94)
    for c in sorted(ev_count_by_cat):
        cc = ev_count_by_cat[c]
        n1 = cc.get(1, 0)
        n2 = cc.get(2, 0)
        n3 = cc.get(3, 0)
        n4 = sum(v for k, v in cc.items() if k >= 4)
        tot = sum(cc.values())
        print(f"{CAT.get(c, c):<10}{n1:<8}{n2:<8}{n3:<8}{n4:<8}{tot}")

    # ---------- 单点 vs 聚合的召回 ----------
    print("\n② 单点检索能力 vs 聚合能力")
    print("-" * 94)
    print(f"{'类别':<10}{'单条evidence':<22}{'多条evidence':<22}{'全证据'}")
    print(f"{'':<10}{'召回/总':<12}{'率':<10}{'召回/总':<12}{'率':<10}")
    print("-" * 94)

    total_single_n = total_single_h = 0
    total_multi_n = total_multi_h = 0
    total_all_n = total_all_h = 0

    for c in sorted(stats):
        s = stats[c]
        sr = s["single_hit"] / s["single_n"] if s["single_n"] else 0
        mr = s["multi_hit"] / s["multi_n"] if s["multi_n"] else 0
        ar = s["all_hit"] / s["all_n"] if s["all_n"] else 0
        sc = "%d/%d" % (s["single_hit"], s["single_n"])
        mc = "%d/%d" % (s["multi_hit"], s["multi_n"])
        print("%-10s%-12s%-10.4f%-12s%-10.4f%.4f" % (
            CAT.get(c, c), sc, sr, mc, mr, ar))
        total_single_n += s["single_n"]
        total_single_h += s["single_hit"]
        total_multi_n += s["multi_n"]
        total_multi_h += s["multi_hit"]
        total_all_n += s["all_n"]
        total_all_h += s["all_hit"]

    print("-" * 94)
    sr = total_single_h / max(1, total_single_n)
    mr = total_multi_h / max(1, total_multi_n)
    ar = total_all_h / max(1, total_all_n)
    sc = "%d/%d" % (total_single_h, total_single_n)
    mc = "%d/%d" % (total_multi_h, total_multi_n)
    print("%-10s%-12s%-10.4f%-12s%-10.4f%.4f" % (
        "总体", sc, sr, mc, mr, ar))

    # ---------- 结论 ----------
    print("\n" + "=" * 94)
    print("结论")
    print("=" * 94)

    print(f"\n  纯单点检索（1 条 evidence）: {sr:.4f}（{total_single_h}/{total_single_n}）")
    print(f"  需要聚合（>=2 条 evidence）: {mr:.4f}（{total_multi_h}/{total_multi_n}）")
    print(f"  全证据召回: {ar:.4f}（{total_all_h}/{total_all_n}）")

    gap = sr - mr
    print(f"\n  差距: 单点比聚合高 {gap:+.4f}")

    print(f"\n  判断：")
    if sr >= 0.7 and mr < 0.5:
        print(f"    → ✅ 检索能力本身没问题（单点 {sr:.2f}）")
        print(f"    → ❌ 缺的是**跨条目聚合能力**（聚合 {mr:.2f}）")
        print(f"    → 下一步：做聚合机制，不是调检索参数")
    elif sr < 0.5:
        print(f"    → ❌ 连单点检索都弱（{sr:.2f}）")
        print(f"    → 下一步：先修检索（切分粒度/排序）")
    else:
        print(f"    → 检索与聚合都有改进空间")

    out = {"experiment": "E-162",
           "single": {"hit": total_single_h, "n": total_single_n,
                      "rate": round(sr, 4)},
           "multi": {"hit": total_multi_h, "n": total_multi_n,
                     "rate": round(mr, 4)},
           "all_evidence": {"hit": total_all_h, "n": total_all_n,
                            "rate": round(ar, 4)},
           "by_category": {c: dict(v) for c, v in stats.items()},
           "ev_count_dist": {c: dict(v) for c, v in ev_count_by_cat.items()}}
    with open(os.path.join(RESULTS, "e162_aggregation.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e162_aggregation.json')}")


if __name__ == "__main__":
    main()
