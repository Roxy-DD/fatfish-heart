"""
E-163 诊断：为什么单点检索只有 0.5157

E-162 推翻了"聚合不足"的假设：
  单条 evidence: 0.5157（82/159）
  多条 evidence: 0.3947（15/38）
  差距只有 0.12 → 聚合不是主因

真正的问题是：**单点检索本身就有一半找不到**。

本实验对失败的 77 条单点题做深度诊断，测四个假设：
  H1 语义差异：evidence 轮次与问题的余弦排在第几？
  H2 长度稀释：evidence 轮次比召回的轮次短多少？
  H3 说话人干扰：是否因为说话人标记导致匹配偏了？
  H4 竞争挤压：被多少条无关但更长的轮次挤掉？

方法：
  对每条失败题，找到 evidence 轮次在**全库余弦排名**里的位置
  · 如果排名在 10 以后 → 语义确实不匹配（H1）
  · 如果排名在 10 以内但没进 top-10 → 排序/竞争问题（H4）
  · 如果排名在 1-3 却没进 → 代码 bug
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "cosmos_mem"))
TASK = os.path.join(_HERE, "work", "e163")
os.makedirs(TASK, exist_ok=True)
RESULTS = os.path.join(_HERE, "results")
DATA = os.path.join(_HERE, "work", "e159", "locomo_sample.json")

K = 10
CAT = {"1": "单跳", "2": "时间", "3": "多跳", "4": "常识", "5": "对抗"}


def main():
    from store_v2 import MemoryStore
    from store_v1 import Embedder
    import numpy as np

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
    id_of_mark = {}
    len_of = {}
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
                mark = f"D{snum}:{ti}"
                mark_of[ep.ep_id] = mark
                id_of_mark[mark] = ep.ep_id
                len_of[mark] = len(full)
            except Exception:
                pass

    emb = Embedder.get()
    st._load_vectors(include_invalid=False)
    norms = np.linalg.norm(st._vec, axis=1)
    norms[norms == 0] = 1.0
    idx_of_id = {eid: i for i, eid in enumerate(st._vec_ids)}

    print("=" * 94)
    print("E-163 单点检索失败诊断")
    print("=" * 94)

    rows = []
    for q in qa:
        question = q.get("question") or ""
        cat = str(q.get("category"))
        ev = q.get("evidence") or []
        if not isinstance(ev, list):
            ev = [ev]
        ev = [str(e).strip() for e in ev if e]
        if not question or len(ev) != 1:
            continue

        mark = ev[0]
        if mark not in id_of_mark:
            continue

        eid = id_of_mark[mark]

        # 全库余弦排名
        nv = emb.encode(question)[0]
        sims = (st._vec @ nv) / norms
        row_idx = idx_of_id[eid]
        rank = int((sims > sims[row_idx]).sum()) + 1
        sim = float(sims[row_idx])

        # 实际 search 的 top-K 是否包含
        res = st.search(question, top=K)
        got = {mark_of.get(t[0].ep_id) for t in res}
        hit = mark in got

        rows.append({
            "q": question, "cat": cat, "mark": mark,
            "rank": rank, "sim": round(sim, 4),
            "hit": hit, "ev_len": len_of.get(mark, 0),
        })

    fails = [r for r in rows if not r["hit"]]
    hits = [r for r in rows if r["hit"]]

    print(f"\n单条 evidence 题: {len(rows)}")
    print(f"  命中 {len(hits)} / 失败 {len(fails)}")

    # ---------- 失败题的余弦排名分布 ----------
    print("\n① 失败题：evidence 在全库余弦排名")
    print("-" * 94)
    buckets = Counter()
    for r in fails:
        rk = r["rank"]
        if rk <= 3:
            buckets["1-3（应命中却没命中）"] += 1
        elif rk <= 10:
            buckets["4-10（被挤掉）"] += 1
        elif rk <= 30:
            buckets["11-30"] += 1
        elif rk <= 100:
            buckets["31-100"] += 1
        else:
            buckets["100+（语义差太远）"] += 1

    nf = len(fails)
    for b in ["1-3（应命中却没命中）", "4-10（被挤掉）",
              "11-30", "31-100", "100+（语义差太远）"]:
        c = buckets.get(b, 0)
        print(f"  {b:<28}{c:<6}{c/max(1,nf):.1%}")

    # ---------- 命中题 vs 失败题的余弦对比 ----------
    print("\n② 命中 vs 失败的余弦分布")
    print("-" * 94)
    if hits:
        import statistics
        h_sims = [r["sim"] for r in hits]
        f_sims = [r["sim"] for r in fails] if fails else [0]
        print(f"  命中题余弦: 均值 {statistics.mean(h_sims):.4f}  "
              f"中位 {statistics.median(h_sims):.4f}")
        print(f"  失败题余弦: 均值 {statistics.mean(f_sims):.4f}  "
              f"中位 {statistics.median(f_sims):.4f}")
        print(f"  失败题排名: 中位 "
              f"{statistics.median([r['rank'] for r in fails]) if fails else 0}")

    # ---------- 长度对比 ----------
    print("\n③ 长度是否影响（H2 长度稀释）")
    print("-" * 94)
    if hits and fails:
        import statistics
        print(f"  命中题 evidence 长度: 均值 "
              f"{statistics.mean([r['ev_len'] for r in hits]):.0f}")
        print(f"  失败题 evidence 长度: 均值 "
              f"{statistics.mean([r['ev_len'] for r in fails]):.0f}")

    # ---------- 分类型 ----------
    print("\n④ 分类型失败率")
    print("-" * 94)
    by = {}
    for r in rows:
        d2 = by.setdefault(r["cat"], [0, 0])
        d2[1] += 1
        d2[0] += 1 if r["hit"] else 0
    print(f"  {'类别':<10}{'命中/总':<14}{'命中率'}")
    print("  " + "-" * 40)
    for c in sorted(by):
        h, n = by[c]
        print(f"  {CAT.get(c, c):<10}{f'{h}/{n}':<14}{h/n:.4f}")

    # ---------- 案例 ----------
    print("\n⑤ 典型失败案例（余弦排名 1-10 却没命中）")
    print("-" * 94)
    for r in [x for x in fails if x["rank"] <= 10][:5]:
        print(f"\n  [{CAT.get(r['cat'])}] 排名 #{r['rank']}  余弦 {r['sim']:.4f}")
        print(f"    问题: {r['q'][:70]}")
        print(f"    证据: {r['mark']}  长度 {r['ev_len']}")

    st.con.close()

    # ---------- 结论 ----------
    print("\n" + "=" * 94)
    print("结论")
    print("=" * 94)

    top3 = buckets.get("1-3（应命中却没命中）", 0)
    top10 = buckets.get("4-10（被挤掉）", 0)
    far = buckets.get("100+（语义差太远）", 0)

    print(f"\n  失败 {nf} 条中：")
    print(f"    余弦排名前 3 却没命中: {top3}  ← 代码 bug 或排序问题")
    print(f"    排名 4-10 被挤掉:      {top10}  ← 席位竞争")
    print(f"    排名 100 以后:          {far}  ← 语义确实不匹配")

    print(f"\n  判断：")
    if top3 > 0:
        print(f"    → 有 {top3} 条余弦前 3 却没命中，需要查代码")
    if top10 >= 3:
        print(f"    → 有 {top10} 条被挤出，可考虑扩 top-K 或改进排序")
    if far >= nf * 0.3:
        print(f"    → {far} 条语义差太远，是本质难题（问答用词完全不同）")

    out = {"experiment": "E-163",
           "n_single": len(rows), "n_hit": len(hits),
           "n_fail": nf,
           "rank_buckets": dict(buckets),
           "details": rows}
    with open(os.path.join(RESULTS, "e163_diag.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n写入 {os.path.join(RESULTS, 'e163_diag.json')}")


if __name__ == "__main__":
    main()
